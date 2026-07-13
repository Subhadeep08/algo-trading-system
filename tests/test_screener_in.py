"""Tests for cockpit/screener_in.py — ScreenerInClient and ScreenerInData."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from cockpit.screener_in import ScreenerInClient, ScreenerInData


def _make_json(*, roce=22.5, de=0.3, pe=28.0, net_profit=None,
               quarterly_eps=None, promoters=None):
    return {
        "ratios": [
            {"name": "ROCE", "value": str(roce)},
            {"name": "Debt to Equity", "value": str(de)},
            {"name": "Stock P/E", "value": str(pe)},
        ],
        "profit_loss": {
            "rows": [
                {"title": "Net Profit",
                 "values": net_profit or ["500", "400", "350", "280"]},
            ]
        },
        "cash_flow": {
            "rows": [
                {"title": "Cash from Operating Activity",
                 "values": ["600", "520", "450", "380"]},
                {"title": "Cash from Investing Activity",
                 "values": ["-200", "-180", "-150", "-120"]},
            ]
        },
        "quarterly_results": {
            "rows": [
                {"title": "EPS in Rs",
                 "values": quarterly_eps or ["12.5", "10.0", "9.5", "9.0", "8.8"]},
                {"title": "Net Profit",
                 "values": ["130", "105", "98", "92", "88"]},
            ]
        },
        "shareholding": {
            "promoters": promoters or ["58.3", "58.5", "58.7"],
        },
    }


def _mock_response(status_code, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


class TestScreenerInClientFetch:
    def test_fetch_tries_consolidated_first(self):
        json_data = _make_json()
        with patch("requests.Session") as mock_session_cls:
            session = MagicMock()
            mock_session_cls.return_value = session
            session.get.return_value = _mock_response(200, json_data)
            client = ScreenerInClient()
            result = client.fetch("CGPOWER")
        assert result is not None
        assert result.ticker == "CGPOWER"
        first_url = session.get.call_args_list[0][0][0]
        assert "/consolidated/" in first_url

    def test_fallback_to_standalone_on_404(self):
        json_data = _make_json()
        with patch("requests.Session") as mock_session_cls:
            session = MagicMock()
            mock_session_cls.return_value = session
            session.get.side_effect = [
                _mock_response(404),
                _mock_response(200, json_data),
            ]
            client = ScreenerInClient()
            result = client.fetch("WELCORP")
        assert result is not None
        assert result.ticker == "WELCORP"
        assert session.get.call_count == 2
        second_url = session.get.call_args_list[1][0][0]
        assert "/consolidated/" not in second_url

    def test_returns_none_on_all_errors(self):
        with patch("requests.Session") as mock_session_cls:
            session = MagicMock()
            mock_session_cls.return_value = session
            session.get.side_effect = Exception("timeout")
            client = ScreenerInClient()
            result = client.fetch("UNKNOWN")
        assert result is None

    def test_cache_prevents_double_fetch(self):
        json_data = _make_json()
        with patch("requests.Session") as mock_session_cls:
            session = MagicMock()
            mock_session_cls.return_value = session
            session.get.return_value = _mock_response(200, json_data)
            client = ScreenerInClient()
            r1 = client.fetch("CGPOWER")
            r2 = client.fetch("CGPOWER")
        assert r1 is r2
        assert session.get.call_count == 1

    def test_is_configured_false_without_env(self):
        env = {k: v for k, v in os.environ.items() if k != "SCREENER_IN_SESSION"}
        with patch.dict(os.environ, env, clear=True):
            assert ScreenerInClient.is_configured() is False

    def test_is_configured_true_with_env(self):
        with patch.dict(os.environ, {"SCREENER_IN_SESSION": "abc123"}):
            assert ScreenerInClient.is_configured() is True


class TestScreenerInDataFromJson:
    def test_from_json_parses_ratios(self):
        data = ScreenerInData.from_json(_make_json(roce=22.5, de=0.3, pe=28.0), "CGPOWER")
        assert data.roce_pct == pytest.approx(22.5)
        assert data.de_ratio == pytest.approx(0.3)
        assert data.current_pe == pytest.approx(28.0)

    def test_from_json_parses_quarterly_eps_most_recent_first(self):
        # Screener.in gives oldest-first; from_json reverses to most-recent-first
        data = ScreenerInData.from_json(
            _make_json(quarterly_eps=["12.5", "10.0", "9.5", "9.0", "8.8"]), "T"
        )
        assert data.quarterly_eps[0] == pytest.approx(8.8)
        assert data.quarterly_eps[-1] == pytest.approx(12.5)

    def test_from_json_promoter_holding(self):
        data = ScreenerInData.from_json(
            _make_json(promoters=["58.3", "58.5", "58.7"]), "T"
        )
        assert data.promoter_holding_pct == pytest.approx(58.7)

    def test_from_json_partial_data_no_crash(self):
        data = ScreenerInData.from_json({}, "EMPTY")
        assert data.roce_pct is None
        assert data.de_ratio is None
        assert data.annual_net_profit == []
        assert data.quarterly_eps == []
