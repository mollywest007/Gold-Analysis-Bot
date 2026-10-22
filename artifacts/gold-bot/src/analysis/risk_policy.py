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


def risk_fraction_for_mode(mode: str) -> float:
    """Return a validated mode override, bounded by the global suggested cap."""
    normalized_mode = str(mode).strip().upper()
    if not normalized_mode or not normalized_mode.replace("_", "").isalnum():
        raise ValueError("Mode name is invalid.")
    value = _env_float(
        f"XAU_{normalized_mode}_RISK_PER_TRADE_FRACTION",
        RISK_POLICY.risk_per_trade_fraction,
    )
    if (
        not math.isfinite(value)
        or value <= 0
        or value > RISK_POLICY.max_suggested_risk_fraction
    ):
        raise ValueError(
            f"XAU_{normalized_mode}_RISK_PER_TRADE_FRACTION must be in "
            f"(0, {RISK_POLICY.max_suggested_risk_fraction}]."
        )
    return value


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
    commission_rate: float = 0.0,
    slippage_per_oz: float = 0.0,
    leverage: float | None = None,
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
        commission = float(commission_rate)
        slippage = float(slippage_per_oz)
        leverage_value = float(leverage) if leverage is not None else None
    except (TypeError, ValueError, OverflowError):
        return {"available": False, "reason": "Position-sizing inputs are invalid."}
    numeric = [*values, entry_value, stop_value, risk]
    if max_lot is not None:
        numeric.append(max_lot)
    numeric.extend((commission, slippage))
    if leverage_value is not None:
        numeric.append(leverage_value)
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
        or commission < 0
        or commission >= 1
        or slippage < 0
        or (leverage_value is not None and leverage_value <= 0)
        or (max_lot is not None and max_lot < min_lot)
    ):
        return {"available": False, "reason": "Position-sizing inputs violate risk limits."}

    cash_risk = equity_value * risk
    price_risk_per_lot = abs(entry_value - stop_value) * contract_size
    execution_cost_per_lot = (
        (entry_value + stop_value) * contract_size * commission
        + 2.0 * contract_size * slippage
    )
    risk_per_lot = price_risk_per_lot + execution_cost_per_lot
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
        "price_risk": round(lots * price_risk_per_lot, 2),
        "estimated_execution_cost": round(lots * execution_cost_per_lot, 2),
        "actual_risk_fraction": round(lots * risk_per_lot / equity_value, 8),
        "estimated_position_value": round(
            lots * contract_size * entry_value, 2
        ),
        "estimated_margin": (
            round(lots * contract_size * entry_value / leverage_value, 2)
            if leverage_value is not None
            else None
        ),
    }


def calculate_trade_economics(
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    lots: float,
    contract_size_oz_per_lot: float,
    leverage: float | None = None,
    commission_rate: float = 0.0,
    slippage_per_oz: float = 0.0,
) -> dict:
    """Estimate gross/net P&L only when contract and execution inputs are known.

    Commission is a fraction of notional charged on both entry and exit.
    Slippage is a per-ounce estimate applied on both sides. Leverage affects
    estimated margin, never the price-based P&L.
    """
    try:
        values = {
            "entry": float(entry),
            "stop": float(stop),
            "target": float(target),
            "lots": float(lots),
            "contract_size_oz_per_lot": float(contract_size_oz_per_lot),
            "commission_rate": float(commission_rate),
            "slippage_per_oz": float(slippage_per_oz),
        }
        leverage_value = float(leverage) if leverage is not None else None
    except (TypeError, ValueError, OverflowError):
        return {"available": False, "reason": "Trade-economics inputs are invalid."}
    if any(not math.isfinite(value) for value in values.values()) or (
        leverage_value is not None and not math.isfinite(leverage_value)
    ):
        return {"available": False, "reason": "Trade-economics inputs are non-finite."}
    direction_value = str(direction).strip().upper()
    entry_value = values["entry"]
    stop_value = values["stop"]
    target_value = values["target"]
    lots_value = values["lots"]
    contract_size = values["contract_size_oz_per_lot"]
    commission = values["commission_rate"]
    slippage = values["slippage_per_oz"]
    if (
        direction_value not in ("BUY", "SELL")
        or min(entry_value, stop_value, target_value, lots_value, contract_size) <= 0
        or not (0 <= commission < 1)
        or slippage < 0
        or (leverage_value is not None and leverage_value <= 0)
        or (direction_value == "BUY" and not stop_value < entry_value < target_value)
        or (direction_value == "SELL" and not target_value < entry_value < stop_value)
    ):
        return {"available": False, "reason": "Trade-economics inputs violate valid trade geometry."}

    units = lots_value * contract_size
    entry_notional = entry_value * units
    stop_notional = stop_value * units
    target_notional = target_value * units

    def estimate(exit_price: float) -> dict:
        gross = (
            (exit_price - entry_value) * units
            if direction_value == "BUY"
            else (entry_value - exit_price) * units
        )
        commission_cost = (entry_notional + exit_price * units) * commission
        slippage_cost = 2.0 * units * slippage
        costs = commission_cost + slippage_cost
        return {
            "gross_pnl": round(gross, 2),
            "commission": round(commission_cost, 2),
            "slippage": round(slippage_cost, 2),
            "estimated_net_pnl": round(gross - costs, 2),
        }

    return {
        "available": True,
        "direction": direction_value,
        "price_distance": {
            "stop": round(abs(entry_value - stop_value), 5),
            "target": round(abs(target_value - entry_value), 5),
        },
        "units_oz": round(units, 8),
        "position_value": round(entry_notional, 2),
        "margin": (
            round(entry_notional / leverage_value, 2)
            if leverage_value is not None
            else None
        ),
        "leverage": leverage_value,
        "at_stop": estimate(stop_value),
        "at_target": estimate(target_value),
        "note": "Leverage changes margin requirements, not gross price P&L.",
    }
