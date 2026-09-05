"""Pure multi-confirmation signal logic.

No function in this module sends Telegram messages or writes to the database.
That separation makes it possible to replay candles safely before enabling live
alerts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from config import (
    BREAKOUT_VOLUME_MULTIPLIER,
    DATA_MAX_AGE_SECONDS,
    MIN_RRR,
    NO_TRADE_RANGE_PCT,
    NO_TRADE_TREND_PCT,
    OI_DECAY_EDGE,
)
from market_data import Candle, MarketSnapshot, OptionChainSnapshot


@dataclass(frozen=True)
class SignalDecision:
    signal: dict | None
    reasons: tuple[str, ...]
    no_trade_zone: bool = False
    data_unavailable: bool = False


def _finite(values: Iterable[float]) -> list[float]:
    return [float(value) for value in values if value is not None and math.isfinite(float(value))]


def ema_series(values: Iterable[float], period: int) -> list[float]:
    values = _finite(values)
    if not values:
        return []
    period = max(1, min(period, len(values)))
    seed = sum(values[:period]) / period
    result = [seed]
    alpha = 2 / (period + 1)
    for value in values[period:]:
        result.append((value - result[-1]) * alpha + result[-1])
    # Align the output to the input length for convenient last/previous access.
    return [seed] * (len(values) - len(result)) + result


def ema(values: Iterable[float], period: int) -> float:
    series = ema_series(values, period)
    return series[-1] if series else 0.0


def rsi(values: Iterable[float], period: int = 14) -> float:
    values = _finite(values)
    if len(values) < 2:
        return 50.0
    period = max(2, min(period, len(values) - 1))
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    window = changes[-period:]
    gains = sum(change for change in window if change > 0) / period
    losses = -sum(change for change in window if change < 0) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100 - (100 / (1 + gains / losses))


def atr(candles: Iterable[Candle], period: int = 14) -> float:
    candles = list(candles)
    if len(candles) < 2:
        return 0.0
    true_ranges: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    window = true_ranges[-max(1, min(period, len(true_ranges))):]
    return sum(window) / len(window) if window else 0.0


def vwap(candles: Iterable[Candle]) -> float:
    candles = list(candles)
    weighted = 0.0
    volume = 0.0
    for candle in candles:
        typical = (candle.high + candle.low + candle.close) / 3
        weighted += typical * max(candle.volume, 0.0)
        volume += max(candle.volume, 0.0)
    if volume <= 0:
        return candles[-1].close if candles else 0.0
    return weighted / volume


def relative_volume(candles: Iterable[Candle], lookback: int = 20) -> float:
    candles = list(candles)
    if len(candles) < 2:
        return 0.0
    previous = candles[-(lookback + 1):-1]
    average = sum(candle.volume for candle in previous) / len(previous) if previous else 0.0
    if average <= 0:
        return 0.0
    return candles[-1].volume / average


def detect_no_trade_zone(candles_5m: Iterable[Candle], candles_15m: Iterable[Candle]) -> tuple[bool, str]:
    five = list(candles_5m)
    fifteen = list(candles_15m)
    if len(five) < 20 or len(fifteen) < 20:
        return True, "insufficient candle history"

    recent = five[-12:]
    last = recent[-1].close
    if last <= 0:
        return True, "invalid market price"
    range_pct = (max(c.high for c in recent) - min(c.low for c in recent)) / last
    ema_fast = ema([c.close for c in fifteen], 8)
    ema_slow = ema([c.close for c in fifteen], 21)
    trend_pct = abs(ema_fast - ema_slow) / last if last else 0.0
    volume_ratio = relative_volume(five)
    current_vwap = vwap(five[-30:])
    current_atr = atr(five)
    stuck_to_vwap = current_atr > 0 and abs(last - current_vwap) < current_atr * 0.25

    if range_pct <= NO_TRADE_RANGE_PCT and trend_pct <= NO_TRADE_TREND_PCT:
        return True, "5m/15m range compression with weak trend"
    if stuck_to_vwap and volume_ratio < BREAKOUT_VOLUME_MULTIPLIER:
        return True, "price trapped around VWAP without volume expansion"
    return False, "trend/range conditions are usable"


def _select_near_atm_quote(chain: OptionChainSnapshot, option_type: str):
    candidates = [quote for quote in chain.quotes if quote.option_type == option_type and quote.ltp > 0]
    if not candidates:
        return None
    return min(candidates, key=lambda quote: abs(quote.strike - chain.spot))


def _oi_direction(chain: OptionChainSnapshot | None) -> tuple[str | None, str]:
    if chain is None or chain.spot <= 0 or not chain.quotes:
        return None, "option-chain data unavailable"

    # Compare the most relevant near-the-money strikes, not the entire chain.
    nearest_strikes = sorted(
        {quote.strike for quote in chain.quotes},
        key=lambda strike: abs(strike - chain.spot),
    )[:8]
    near = [quote for quote in chain.quotes if quote.strike in nearest_strikes]
    call_decay = 0.0
    put_decay = 0.0
    for quote in near:
        if quote.oi <= 0:
            continue
        normalized_decay = max(-quote.oi_change, 0.0) / quote.oi
        if quote.option_type == "CE":
            call_decay += normalized_decay
        elif quote.option_type == "PE":
            put_decay += normalized_decay

    if call_decay <= 0 and put_decay <= 0:
        return None, "near-ATM OI decay is not decisive"
    if call_decay > put_decay * OI_DECAY_EDGE:
        return "BUY", f"call OI decay dominant ({call_decay:.2%} vs put {put_decay:.2%})"
    if put_decay > call_decay * OI_DECAY_EDGE:
        return "SELL", f"put OI decay dominant ({put_decay:.2%} vs call {call_decay:.2%})"
    return None, "call and put OI decay are balanced"


def _price_action_direction(candles: list[Candle]) -> tuple[str | None, str, float]:
    if len(candles) < 8:
        return None, "not enough 5m candles for breakout", 0.0
    previous = candles[-7:-1]
    current = candles[-1]
    swing_high = max(candle.high for candle in previous)
    swing_low = min(candle.low for candle in previous)
    volume_ratio = relative_volume(candles)
    if current.close > swing_high and current.close > current.open and volume_ratio >= BREAKOUT_VOLUME_MULTIPLIER:
        return "BUY", f"5m breakout above {swing_high:.2f} with {volume_ratio:.1f}x volume", volume_ratio
    if current.close < swing_low and current.close < current.open and volume_ratio >= BREAKOUT_VOLUME_MULTIPLIER:
        return "SELL", f"5m breakdown below {swing_low:.2f} with {volume_ratio:.1f}x volume", volume_ratio
    return None, "no volume-confirmed breakout/breakdown", volume_ratio


def _timeframe_direction(candles: list[Candle]) -> tuple[str | None, str]:
    if len(candles) < 25:
        return None, "not enough 15m candles for confirmation"
    closes = [candle.close for candle in candles]
    series = ema_series(closes, 21)
    last = closes[-1]
    previous_ema = series[-2] if len(series) > 1 else series[-1]
    current_ema = series[-1]
    if last > current_ema and current_ema >= previous_ema:
        return "BUY", f"15m trend above rising EMA ({current_ema:.2f})"
    if last < current_ema and current_ema <= previous_ema:
        return "SELL", f"15m trend below falling EMA ({current_ema:.2f})"
    return None, "15m trend is not aligned"


def build_signal(snapshot: MarketSnapshot, slot: str | None = None) -> SignalDecision:
    if not snapshot.fresh or snapshot.spot <= 0:
        return SignalDecision(None, ("market snapshot is stale or invalid",), data_unavailable=True)
    if not snapshot.option_chain:
        return SignalDecision(None, ("live option-chain snapshot unavailable",), data_unavailable=True)

    five = list(snapshot.candles_5m)
    fifteen = list(snapshot.candles_15m)
    if not five or not fifteen:
        return SignalDecision(None, ("required timeframe candles unavailable",), data_unavailable=True)
    latest_age = (datetime.now(timezone.utc) - five[-1].timestamp).total_seconds()
    if latest_age > max(DATA_MAX_AGE_SECONDS, 20 * 60):
        return SignalDecision(None, (f"5m candle is stale by {latest_age:.0f}s",), data_unavailable=True)
    in_range, range_reason = detect_no_trade_zone(five, fifteen)
    if in_range:
        return SignalDecision(None, (range_reason,), no_trade_zone=True)

    price_direction, price_reason, volume_ratio = _price_action_direction(five)
    timeframe_direction, timeframe_reason = _timeframe_direction(fifteen)
    oi_direction, oi_reason = _oi_direction(snapshot.option_chain)
    last = five[-1].close if five else snapshot.spot
    current_vwap = vwap(five[-30:]) if five else 0.0
    current_rsi = rsi([candle.close for candle in five])
    current_atr = atr(five)

    if not price_direction:
        return SignalDecision(None, (price_reason, timeframe_reason, oi_reason))
    if timeframe_direction != price_direction:
        return SignalDecision(None, (price_reason, timeframe_reason, oi_reason))
    if oi_direction != price_direction:
        return SignalDecision(None, (price_reason, timeframe_reason, oi_reason))
    if current_atr <= 0:
        return SignalDecision(None, ("ATR is unavailable",))

    if price_direction == "BUY":
        if last <= current_vwap or current_rsi < 50 or current_rsi > 82:
            return SignalDecision(None, (price_reason, timeframe_reason, "VWAP/RSI did not confirm the long"))
        stop = min(candle.low for candle in five[-10:]) - current_atr * 0.20
        option_type = "CE"
    else:
        if last >= current_vwap or current_rsi > 50 or current_rsi < 18:
            return SignalDecision(None, (price_reason, timeframe_reason, "VWAP/RSI did not confirm the short"))
        stop = max(candle.high for candle in five[-10:]) + current_atr * 0.20
        option_type = "PE"

    selected_option = _select_near_atm_quote(snapshot.option_chain, option_type)
    if selected_option is None:
        return SignalDecision(None, (f"liquid {option_type} option quote unavailable",), data_unavailable=True)

    risk = abs(last - stop)
    if risk <= 0:
        return SignalDecision(None, ("invalid stop distance",))
    if price_direction == "BUY":
        targets = (last + risk, last + risk * 2, last + risk * 3)
    else:
        targets = (last - risk, last - risk * 2, last - risk * 3)
    rrr = abs(targets[2] - last) / risk
    if rrr < MIN_RRR:
        return SignalDecision(None, (f"RRR {rrr:.2f} is below {MIN_RRR:.2f}",))

    strike_text = f"{selected_option.strike:g}"
    asset = f"{snapshot.symbol} {strike_text} {option_type}"
    logic = "; ".join(
        [
            price_reason,
            timeframe_reason,
            f"price {'above' if price_direction == 'BUY' else 'below'} VWAP",
            f"RSI {current_rsi:.1f}",
            oi_reason,
        ]
    )
    signal = {
        "symbol": asset,
        "underlying": snapshot.symbol,
        "direction": price_direction,
        "option_type": option_type,
        "option_strike": selected_option.strike,
        "option_ltp": selected_option.ltp,
        "entry": round(last, 2),
        "entry_low": round(last - current_atr * 0.05, 2),
        "entry_high": round(last + current_atr * 0.05, 2),
        "sl": round(stop, 2),
        # T1 is the 1R management milestone; no booking happens there.
        "t1": round(targets[0], 2),
        "t2": round(targets[1], 2),
        "t3": round(targets[2], 2),
        "risk_points": round(risk, 2),
        "rrr": round(rrr, 2),
        "strategy": "ELITE_SNIPER_MTF_OI",
        "logic": logic,
        "evidence": [price_reason, timeframe_reason, oi_reason],
        "volume_ratio": round(volume_ratio, 2),
        "vwap": round(current_vwap, 2),
        "rsi": round(current_rsi, 2),
        "slot": slot,
        "source": snapshot.source,
    }
    return SignalDecision(signal, tuple(signal["evidence"]))
