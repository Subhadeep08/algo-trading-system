"""Shared frozen dataclasses for the cockpit package.

Pure data containers with computed properties — no business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class HoldingConfig:
    """Raw holding configuration loaded from portfolio.yaml."""

    ticker: str
    qty: int
    cost: float
    sl: float
    target: Optional[float]
    catalyst: str


@dataclass(frozen=True)
class GttOrderConfig:
    """Raw GTT order configuration loaded from portfolio.yaml."""

    ticker: str
    qty: int
    gtt: float
    target: Optional[float]
    catalyst: str


@dataclass(frozen=True)
class HoldingSnapshot:
    """Live market state for an active holding."""

    ticker: str
    current_price: float
    previous_close: float
    quantity: int
    average_cost: float
    stop_loss_price: float
    target_price: Optional[float]

    @property
    def day_change_pct(self) -> float:
        """Percentage change from previous close to current price."""
        if self.previous_close == 0:
            return 0.0
        return ((self.current_price - self.previous_close) / self.previous_close) * 100.0

    @property
    def profit_loss_pct(self) -> float:
        """Unrealised P&L percentage relative to average cost."""
        if self.average_cost == 0:
            return 0.0
        return ((self.current_price - self.average_cost) / self.average_cost) * 100.0

    @property
    def stop_loss_buffer_pct(self) -> float:
        """Percentage gap between current price and stop-loss level.

        Positive value means price is above stop-loss (safe).
        Negative value means price has breached stop-loss.
        """
        if self.stop_loss_price == 0:
            return 0.0
        return ((self.current_price - self.stop_loss_price) / self.stop_loss_price) * 100.0

    @property
    def market_value(self) -> float:
        """Current market value of the holding (quantity * current price)."""
        return self.quantity * self.current_price

    @property
    def book_value(self) -> float:
        """Cost basis of the holding (quantity * average cost)."""
        return self.quantity * self.average_cost

    @property
    def is_stop_loss_breached(self) -> bool:
        """True if current price is at or below the stop-loss level."""
        return self.current_price <= self.stop_loss_price


@dataclass(frozen=True)
class GttOrderSnapshot:
    """Live state for a pending GTT (Good Till Triggered) order."""

    ticker: str
    current_price: float
    trigger_price: float
    target_price: Optional[float]
    quantity: int

    @property
    def distance_to_trigger_pct(self) -> float:
        """Percentage by which current price exceeds the trigger price.

        Positive: current price is above trigger (not yet triggered).
        Negative or zero: trigger has been reached or breached.
        """
        if self.trigger_price == 0:
            return 0.0
        return ((self.current_price - self.trigger_price) / self.trigger_price) * 100.0

    @property
    def is_trigger_breached(self) -> bool:
        """True if current price is at or below the GTT trigger price."""
        return self.current_price <= self.trigger_price


@dataclass(frozen=True)
class VolumeSnapshot:
    """Volume data for a ticker on the current session."""

    ticker: str
    today_volume: float
    average_20d_volume: float
    volume_ratio: float


@dataclass(frozen=True)
class ScreeningResult:
    """Outcome of running the PMS 4-gate + secondary overlay screen on a ticker."""

    ticker: str
    passed_all_gates: bool
    gate_scores: dict
    secondary_score: float
    suggested_position_size: float
    notes: list
