"""yfinance wrapper — all NSE market data fetching lives here."""
from __future__ import annotations

import logging
from typing import Optional

import yfinance as yf

from cockpit.config import (
    NSE_SUFFIX, NIFTY_50_SYMBOL, NIFTY_500_SYMBOL, VOLUME_LOOKBACK_PERIOD,
)
from cockpit.models import HoldingSnapshot, GttOrderSnapshot, VolumeSnapshot
from cockpit.portfolio import get_registry

logger = logging.getLogger(__name__)


class MarketDataService:
    """Fetches live NSE prices and volumes. Wraps yfinance; all errors are logged and return None."""

    def holding_snapshots(self) -> dict[str, Optional[HoldingSnapshot]]:
        registry = get_registry()
        return {
            cfg.ticker: self._holding_snapshot(cfg)
            for cfg in registry.holdings()
        }

    def gtt_snapshots(self) -> dict[str, Optional[GttOrderSnapshot]]:
        registry = get_registry()
        return {
            cfg.ticker: self._gtt_snapshot(cfg)
            for cfg in registry.gtt_orders()
        }

    def nifty_day_change_pct(self) -> Optional[float]:
        try:
            fi = yf.Ticker(NIFTY_50_SYMBOL).fast_info
            if fi.last_price is None or fi.previous_close is None:
                raise ValueError("incomplete Nifty data")
            return (fi.last_price - fi.previous_close) / fi.previous_close * 100
        except Exception as exc:
            logger.warning("Nifty 50 fetch failed: %s", exc)
            return None

    def volume_snapshot(self, ticker: str) -> Optional[VolumeSnapshot]:
        try:
            hist = yf.Ticker(f"{ticker}{NSE_SUFFIX}").history(period=VOLUME_LOOKBACK_PERIOD)
            if hist.empty or len(hist) < 2:
                raise ValueError("insufficient history")
            today_vol = int(hist["Volume"].iloc[-1])
            avg_20d   = int(hist["Volume"].iloc[:-1].mean())
            ratio     = today_vol / avg_20d if avg_20d > 0 else 0.0
            return VolumeSnapshot(ticker=ticker, today_volume=today_vol,
                                  average_20d_volume=avg_20d, volume_ratio=ratio)
        except Exception as exc:
            logger.warning("Volume fetch failed for %s: %s", ticker, exc)
            return None

    def _holding_snapshot(self, cfg) -> Optional[HoldingSnapshot]:
        try:
            fi = yf.Ticker(f"{cfg.ticker}{NSE_SUFFIX}").fast_info
            if fi.last_price is None or fi.previous_close is None:
                raise ValueError("no price data")
            return HoldingSnapshot(
                ticker=cfg.ticker,
                current_price=float(fi.last_price),
                previous_close=float(fi.previous_close),
                quantity=cfg.qty,
                average_cost=cfg.cost,
                stop_loss_price=cfg.sl,
                target_price=cfg.target,
            )
        except Exception as exc:
            logger.warning("Price fetch failed for %s: %s", cfg.ticker, exc)
            return None

    def _gtt_snapshot(self, cfg) -> Optional[GttOrderSnapshot]:
        try:
            price = yf.Ticker(f"{cfg.ticker}{NSE_SUFFIX}").fast_info.last_price
            if price is None:
                raise ValueError("no price data")
            return GttOrderSnapshot(
                ticker=cfg.ticker,
                current_price=float(price),
                trigger_price=cfg.gtt,
                target_price=cfg.target,
                quantity=cfg.qty,
            )
        except Exception as exc:
            logger.warning("GTT price fetch failed for %s: %s", cfg.ticker, exc)
            return None
