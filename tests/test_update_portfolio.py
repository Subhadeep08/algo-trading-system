"""Tests for scripts/update_portfolio.py — AST patching and trade actions."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from update_portfolio import (
    _execute_add_gtt_order,
    _execute_buy,
    _execute_remove_gtt_order,
    _execute_sell,
    _execute_update_stop_loss,
    _find_dict_block_bounds,
    _format_gtt_orders_as_source_block,
    _format_holdings_as_source_block,
    _load_catalyst_notes_from_markdown,
    _parse_dict_from_source,
    _patch_dict_in_source,
)

# ── _find_dict_block_bounds ───────────────────────────────────────────────────

SIMPLE_SOURCE = textwrap.dedent("""\
    x = 1
    HOLDINGS = {
        "TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0},
    }
    y = 2
""")


class TestFindDictBlockBounds:
    def test_finds_correct_start_and_end(self):
        start, end = _find_dict_block_bounds(SIMPLE_SOURCE, "HOLDINGS")
        lines = SIMPLE_SOURCE.splitlines()
        assert lines[start].startswith("HOLDINGS")
        assert lines[end].strip() == "}"

    def test_raises_when_variable_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            _find_dict_block_bounds(SIMPLE_SOURCE, "NONEXISTENT")

    def test_raises_on_unmatched_braces(self):
        bad_source = "HOLDINGS = {\n    unclosed\n"
        with pytest.raises(ValueError, match="Unmatched"):
            _find_dict_block_bounds(bad_source, "HOLDINGS")

    def test_multiline_nested_dict(self):
        source = textwrap.dedent("""\
            GTT_ORDERS = {
                "SYRMA": {"qty": 30, "gtt": 1275.00, "target": 1600.00},
                "OTHER": {"qty": 10, "gtt": 500.00,  "target": 650.00},
            }
        """)
        start, end = _find_dict_block_bounds(source, "GTT_ORDERS")
        lines = source.splitlines()
        assert lines[start].startswith("GTT_ORDERS")
        assert "}" in lines[end]


# ── _parse_dict_from_source ───────────────────────────────────────────────────

class TestParseDictFromSource:
    def test_parses_holdings_correctly(self, tmp_path):
        f = tmp_path / "runner.py"
        f.write_text(SIMPLE_SOURCE, encoding="utf-8")
        result = _parse_dict_from_source(f, "HOLDINGS")
        assert result["TICK"]["qty"] == 10
        assert result["TICK"]["cost"] == 100.0

    def test_parses_none_target(self, tmp_path):
        source = textwrap.dedent("""\
            HOLDINGS = {
                "WELCORP": {"qty": 30, "cost": 1491.70, "sl": 1350.00, "target": None},
            }
        """)
        f = tmp_path / "runner.py"
        f.write_text(source, encoding="utf-8")
        result = _parse_dict_from_source(f, "HOLDINGS")
        assert result["WELCORP"]["target"] is None

    def test_raises_on_missing_variable(self, tmp_path):
        f = tmp_path / "runner.py"
        f.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="not found"):
            _parse_dict_from_source(f, "HOLDINGS")


# ── _patch_dict_in_source ─────────────────────────────────────────────────────

class TestPatchDictInSource:
    def test_replaces_block_in_file(self, tmp_path):
        f = tmp_path / "runner.py"
        f.write_text(SIMPLE_SOURCE, encoding="utf-8")
        new_block = 'HOLDINGS = {\n    "NEW": {"qty": 5, "cost": 50.0, "sl": 45.0, "target": 60.0},\n}'
        _patch_dict_in_source(f, "HOLDINGS", new_block)
        content = f.read_text(encoding="utf-8")
        assert '"NEW"' in content
        assert '"TICK"' not in content

    def test_preserves_surrounding_lines(self, tmp_path):
        f = tmp_path / "runner.py"
        f.write_text(SIMPLE_SOURCE, encoding="utf-8")
        new_block = 'HOLDINGS = {\n}'
        _patch_dict_in_source(f, "HOLDINGS", new_block)
        content = f.read_text(encoding="utf-8")
        assert "x = 1" in content
        assert "y = 2" in content


# ── _format_holdings_as_source_block ─────────────────────────────────────────

class TestFormatHoldingsAsSourceBlock:
    def test_output_starts_with_holdings_equals(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        block = _format_holdings_as_source_block(holdings)
        assert block.startswith("HOLDINGS = {")

    def test_output_ends_with_closing_brace(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        block = _format_holdings_as_source_block(holdings)
        assert block.strip().endswith("}")

    def test_none_target_renders_as_none(self):
        holdings = {"WELCORP": {"qty": 30, "cost": 1491.70, "sl": 1350.00, "target": None}}
        block = _format_holdings_as_source_block(holdings)
        assert "None" in block

    def test_numeric_target_renders_with_two_decimals(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        block = _format_holdings_as_source_block(holdings)
        assert "120.00" in block

    def test_output_is_valid_python(self):
        holdings = {
            "A": {"qty": 5,  "cost": 100.0, "sl": 90.0,  "target": 120.0},
            "B": {"qty": 10, "cost": 200.0, "sl": 180.0, "target": None},
        }
        block = _format_holdings_as_source_block(holdings)
        parsed = {}
        exec(block, parsed)
        assert parsed["HOLDINGS"]["A"]["qty"] == 5
        assert parsed["HOLDINGS"]["B"]["target"] is None


# ── _format_gtt_orders_as_source_block ───────────────────────────────────────

class TestFormatGttOrdersAsSourceBlock:
    def test_output_starts_with_gtt_orders(self):
        gtt = {"SYRMA": {"qty": 30, "gtt": 1275.00, "target": 1600.00}}
        block = _format_gtt_orders_as_source_block(gtt)
        assert block.startswith("GTT_ORDERS = {")

    def test_output_is_valid_python(self):
        gtt = {"SYRMA": {"qty": 30, "gtt": 1275.00, "target": 1600.00}}
        block = _format_gtt_orders_as_source_block(gtt)
        parsed = {}
        exec(block, parsed)
        assert parsed["GTT_ORDERS"]["SYRMA"]["gtt"] == pytest.approx(1275.00)

    def test_empty_dict_renders_correctly(self):
        block = _format_gtt_orders_as_source_block({})
        assert "GTT_ORDERS = {" in block
        assert block.strip().endswith("}")


# ── _execute_buy ──────────────────────────────────────────────────────────────

class TestExecuteBuy:
    def test_new_holding_added(self):
        holdings = {}
        catalyst_notes = {}
        _execute_buy(holdings, "NEWCO", 10, 500.0, 450.0, 650.0, "Good stock", catalyst_notes)
        assert "NEWCO" in holdings
        assert holdings["NEWCO"]["qty"] == 10
        assert holdings["NEWCO"]["cost"] == 500.0
        assert catalyst_notes["NEWCO"] == "Good stock"

    def test_accumulation_weighted_average_cost(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        catalyst_notes = {}
        # Buy 10 more at 120 → avg = (10*100 + 10*120) / 20 = 110
        _execute_buy(holdings, "TICK", 10, 120.0, 0.0, 0.0, "", catalyst_notes)
        assert holdings["TICK"]["qty"] == 20
        assert holdings["TICK"]["cost"] == pytest.approx(110.0)

    def test_accumulation_preserves_existing_sl_when_zero(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        _execute_buy(holdings, "TICK", 5, 110.0, 0.0, 0.0, "", {})
        assert holdings["TICK"]["sl"] == pytest.approx(90.0)

    def test_accumulation_updates_sl_when_nonzero(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        _execute_buy(holdings, "TICK", 5, 110.0, 95.0, 0.0, "", {})
        assert holdings["TICK"]["sl"] == pytest.approx(95.0)

    def test_new_holding_missing_sl_exits(self, capsys):
        with pytest.raises(SystemExit):
            _execute_buy({}, "NEWCO", 10, 500.0, 0.0, 0.0, "", {})


# ── _execute_sell ─────────────────────────────────────────────────────────────

class TestExecuteSell:
    def test_full_exit_removes_holding(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        catalyst_notes = {"TICK": "some notes"}
        _execute_sell(holdings, "TICK", 10, catalyst_notes)
        assert "TICK" not in holdings
        assert "TICK" not in catalyst_notes

    def test_full_exit_when_qty_exceeds_held(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        _execute_sell(holdings, "TICK", 20, {})
        assert "TICK" not in holdings

    def test_partial_exit_reduces_quantity(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        _execute_sell(holdings, "TICK", 3, {})
        assert holdings["TICK"]["qty"] == 7

    def test_sell_unknown_ticker_exits(self):
        with pytest.raises(SystemExit):
            _execute_sell({}, "UNKNOWN", 5, {})


# ── _execute_update_stop_loss ─────────────────────────────────────────────────

class TestExecuteUpdateStopLoss:
    def test_updates_sl_correctly(self):
        holdings = {"TICK": {"qty": 10, "cost": 100.0, "sl": 90.0, "target": 120.0}}
        _execute_update_stop_loss(holdings, "TICK", 95.0)
        assert holdings["TICK"]["sl"] == pytest.approx(95.0)

    def test_unknown_ticker_exits(self):
        with pytest.raises(SystemExit):
            _execute_update_stop_loss({}, "UNKNOWN", 95.0)


# ── _execute_add_gtt_order ────────────────────────────────────────────────────

class TestExecuteAddGttOrder:
    def test_adds_gtt_order(self):
        gtt_orders = {}
        catalyst_notes = {}
        _execute_add_gtt_order(gtt_orders, "SYRMA", 30, 1275.0, 1600.0, "Strong PAT", catalyst_notes)
        assert "SYRMA" in gtt_orders
        assert gtt_orders["SYRMA"]["qty"] == 30
        assert gtt_orders["SYRMA"]["gtt"] == pytest.approx(1275.0)
        assert catalyst_notes["GTT_SYRMA"] == "Strong PAT"

    def test_no_catalyst_key_when_empty(self):
        gtt_orders = {}
        catalyst_notes = {}
        _execute_add_gtt_order(gtt_orders, "SYRMA", 30, 1275.0, 1600.0, "", catalyst_notes)
        assert "GTT_SYRMA" not in catalyst_notes


# ── _execute_remove_gtt_order ─────────────────────────────────────────────────

class TestExecuteRemoveGttOrder:
    def test_removes_gtt_order(self):
        gtt_orders = {"SYRMA": {"qty": 30, "gtt": 1275.0, "target": 1600.0}}
        catalyst_notes = {"GTT_SYRMA": "strong"}
        _execute_remove_gtt_order(gtt_orders, "SYRMA", catalyst_notes)
        assert "SYRMA" not in gtt_orders
        assert "GTT_SYRMA" not in catalyst_notes

    def test_unknown_ticker_exits(self):
        with pytest.raises(SystemExit):
            _execute_remove_gtt_order({}, "UNKNOWN", {})


# ── _load_catalyst_notes_from_markdown ────────────────────────────────────────

class TestLoadCatalystNotes:
    PORTFOLIO_MD = textwrap.dedent("""\
        # Portfolio Data

        ## Active Holdings

        | Ticker (NSE)       | Qty | Cost Price (₹) | Trailing Stop-Loss (₹) | Target Price (₹) | Key Catalyst |
        |---------------------|-----|-----------------|-------------------------|-------------------|--------------|
        | APTUS               | 100 |          294.70 |                  285.00 |            350.00 | Citi watch |
        | KIRLOSENG           | 25  |        2,349.88 |                2,183.00 |          3,100.00 | HyperNext order |

        ## Pending GTT Limit Buys

        | Ticker (NSE)       | Qty | GTT Limit (₹) | Target Price (₹) | Key Catalyst |
        |---------------------|-----|----------------|-------------------|--------------|\n
        | SYRMA               | 30  |       1,275.00 |          1,600.00 | PAT +87% |
    """)

    def test_parses_holding_catalysts(self, tmp_path, monkeypatch):
        md = tmp_path / "portfolio-data.md"
        md.write_text(self.PORTFOLIO_MD, encoding="utf-8")
        import update_portfolio
        monkeypatch.setattr(update_portfolio, "PORTFOLIO_MD", md)
        notes = _load_catalyst_notes_from_markdown()
        assert notes.get("APTUS") == "Citi watch"
        assert notes.get("KIRLOSENG") == "HyperNext order"

    def test_parses_gtt_catalysts_with_prefix(self, tmp_path, monkeypatch):
        md = tmp_path / "portfolio-data.md"
        md.write_text(self.PORTFOLIO_MD, encoding="utf-8")
        import update_portfolio
        monkeypatch.setattr(update_portfolio, "PORTFOLIO_MD", md)
        notes = _load_catalyst_notes_from_markdown()
        assert notes.get("GTT_SYRMA") == "PAT +87%"

    def test_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        import update_portfolio
        monkeypatch.setattr(update_portfolio, "PORTFOLIO_MD", tmp_path / "nonexistent.md")
        notes = _load_catalyst_notes_from_markdown()
        assert notes == {}
