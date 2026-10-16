"""Validated risk defaults and sizing helpers for XAU/USD signal plans.

This module never places orders. Position sizing is only available when the
caller supplies actual account and broker contract specifications.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os


@dataclass(frozen=True)
class RiskPolicy:
    risk_per_trade_fraction: float = 0.005
    max_suggested_risk_fraction: float = 0.01
    max_total_risk_fraction: float = 0.02
    max_simultaneous_setups: int = 4
    tp1_partial_fraction: float = 0.50
    breakeven_after_tp1: bool = False
    structure_trailing_enabled: bool = False
    event_filter_before_minutes: int = 15
    event_filter_after_minutes: int = 15

    def validate(self) -> "RiskPolicy":
        fractions = (
            self.risk_per_trade_fraction,
            self.max_suggested_risk_fraction,
            self.max_total_risk_fraction,
        )
        if any(not math.isfinite(value) or value <= 0 or value > 1 for value in fractions):
            raise ValueError("Risk fractions must be finite and in (0, 1].")
        if self.risk_per_trade_fraction > self.max_suggested_risk_fraction:
            raise ValueError("Default risk cannot exceed maximum suggested risk.")
        if self.max_total_risk_fraction < self.risk_per_trade_fraction:
            raise ValueError("Total risk limit cannot be below per-trade risk.")
        if type(self.max_simultaneous_setups) is not int or self.max_simultaneous_setups < 1:
            raise ValueError("Maximum simultaneous setups must be a positive integer.")
        if (
            not math.isfinite(self.tp1_partial_fraction)
            or not 0 < self.tp1_partial_fraction < 1
        ):
            raise ValueError("TP1 partial fraction must be in (0, 1).")
        if (
            type(self.event_filter_before_minutes) is not int
            or type(self.event_filter_after_minutes) is not int
            or self.event_filter_before_minutes < 0
            or self.event_filter_after_minutes < 0
        ):
            raise ValueError("Event-risk windows cannot be negative.")
        return self


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number.") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if str(value) != raw.strip():
        raise ValueError(f"{name} must be an integer.")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false.")
    return value in {"true", "1", "yes"}


def load_risk_policy() -> RiskPolicy:
    """Load validated, non-secret policy overrides from environment variables."""
    return RiskPolicy(
        risk_per_trade_fraction=_env_float(
            "XAU_RISK_PER_TRADE_FRACTION", 0.005
        ),
        max_suggested_risk_fraction=_env_float(
            "XAU_MAX_SUGGESTED_RISK_FRACTION", 0.01
        ),
        max_total_risk_fraction=_env_float(
            "XAU_MAX_TOTAL_RISK_FRACTION", 0.02
        ),
        max_simultaneous_setups=_env_int("XAU_MAX_SIMULTANEOUS_SETUPS", 4),
        tp1_partial_fraction=_env_float("XAU_TP1_PARTIAL_FRACTION", 0.50),
        breakeven_after_tp1=_env_bool("XAU_BREAKEVEN_AFTER_TP1", False),
        structure_trailing_enabled=_env_bool(
            "XAU_STRUCTURE_TRAILING_ENABLED", False
        ),
        event_filter_before_minutes=_env_int(
            "XAU_EVENT_FILTER_BEFORE_MINUTES", 15
        ),
        event_filter_after_minutes=_env_int(
            "XAU_EVENT_FILTER_AFTER_MINUTES", 15
        ),
    ).validate()


RISK_POLICY = load_risk_policy()


def calculate_position_size(
    *,
    equity: float | None,
    entry: float,
    stop: float,
    contract_size_oz_per_lot: float | None,
    lot_step: float | None,
    minimum_lot: float | None,
    risk_fraction: float | None = None,
    maximum_lot: float | None = None,
) -> dict:
    """Calculate lots only from supplied account equity and broker specs."""
    required = (equity, contract_size_oz_per_lot, lot_step, minimum_lot)
    if any(value is None for value in required):
        return {
            "available": False,
            "reason": "Account equity and broker contract specifications are required.",
        }
    try:
        values = [float(value) for value in required]
        equity_value, contract_size, step, min_lot = values
        entry_value, stop_value = float(entry), float(stop)
        risk = float(
            RISK_POLICY.risk_per_trade_fraction
            if risk_fraction is None
            else risk_fraction
        )
        max_lot = float(maximum_lot) if maximum_lot is not None else None
    except (TypeError, ValueError, OverflowError):
        return {"available": False, "reason": "Position-sizing inputs are invalid."}
    numeric = [*values, entry_value, stop_value, risk]
    if max_lot is not None:
        numeric.append(max_lot)
    if any(not math.isfinite(value) for value in numeric):
        return {"available": False, "reason": "Position-sizing inputs are non-finite."}
    if (
        equity_value <= 0
        or contract_size <= 0
        or step <= 0
        or min_lot <= 0
        or entry_value <= 0
        or stop_value <= 0
        or entry_value == stop_value
        or risk <= 0
        or risk > RISK_POLICY.max_suggested_risk_fraction
        or (max_lot is not None and max_lot < min_lot)
    ):
        return {"available": False, "reason": "Position-sizing inputs violate risk limits."}

    cash_risk = equity_value * risk
    risk_per_lot = abs(entry_value - stop_value) * contract_size
    raw_lots = cash_risk / risk_per_lot
    lots = math.floor((raw_lots + 1e-12) / step) * step
    if max_lot is not None:
        lots = min(lots, max_lot)
        lots = math.floor((lots + 1e-12) / step) * step
    if lots < min_lot:
        return {
            "available": False,
            "reason": "Calculated size is below the broker minimum lot.",
            "cash_risk": round(cash_risk, 2),
        }
    return {
        "available": True,
        "lots": round(lots, 8),
        "cash_risk": round(cash_risk, 2),
        "actual_risk_fraction": round(lots * risk_per_lot / equity_value, 8),
    }
