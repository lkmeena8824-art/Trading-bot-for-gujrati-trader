"""Free/best-effort market-data adapters.

The default provider uses yfinance for candles and the public NSE endpoint for
option-chain/FII-DII snapshots. Both sources can throttle or become stale, so
callers must treat a missing snapshot as a hard *no trade* condition. No fake
or random market values are generated here.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import aiohttp
import yfinance as yf

from config import (
    CANDLE_MIN_INTERVAL_SECONDS,
    DATA_MAX_AGE_SECONDS,
    DATA_TIMEOUT_SECONDS,
    NSE_OPTION_CHAIN_URL,
    OPTION_CHAIN_MIN_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)

INDEX_TICKERS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
}

NSE_CHAIN_SYMBOLS = {
    "NIFTY": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
}


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OptionQuote:
    strike: float
    option_type: str
    expiry: str
    ltp: float
    oi: float
    oi_change: float
    volume: float
    iv: float


@dataclass(frozen=True)
class OptionChainSnapshot:
    symbol: str
    spot: float
    expiry: str
    quotes: tuple[OptionQuote, ...]
    fetched_at: datetime
    source: str


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    spot: float
    candles_5m: tuple[Candle, ...]
    candles_15m: tuple[Candle, ...]
    option_chain: OptionChainSnapshot | None
    fetched_at: datetime
    source: str

    @property
    def age_seconds(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.fetched_at).total_seconds())

    @property
    def fresh(self) -> bool:
        return self.age_seconds <= DATA_MAX_AGE_SECONDS


class MarketDataError(RuntimeError):
    """Raised only for provider failures; callers should fail closed."""


_CANDLE_CACHE: dict[tuple[str, str, str], tuple[float, list[Candle]]] = {}
_CANDLE_CACHE_LOCK = asyncio.Lock()


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        # pandas/numpy scalar compatibility without importing either directly.
        if hasattr(value, "item"):
            value = value.item()
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _first_scalar(value: Any) -> Any:
    if hasattr(value, "iloc"):
        try:
            return value.iloc[0]
        except Exception:
            return value
    return value


def _flatten_columns(frame: Any) -> Any:
    try:
        if getattr(frame.columns, "nlevels", 1) > 1:
            frame = frame.copy()
            frame.columns = [
                column[0] if isinstance(column, tuple) else column
                for column in frame.columns
            ]
    except Exception:
        pass
    return frame


def _timestamp(value: Any) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def frame_to_candles(frame: Any) -> list[Candle]:
    if frame is None or getattr(frame, "empty", True):
        return []
    frame = _flatten_columns(frame)
    candles: list[Candle] = []
    for index, row in frame.iterrows():
        try:
            candle = Candle(
                timestamp=_timestamp(index),
                open=_safe_float(_first_scalar(row["Open"])),
                high=_safe_float(_first_scalar(row["High"])),
                low=_safe_float(_first_scalar(row["Low"])),
                close=_safe_float(_first_scalar(row["Close"])),
                volume=_safe_float(_first_scalar(row.get("Volume", 0))),
            )
            if candle.close > 0 and candle.high >= candle.low > 0:
                candles.append(candle)
        except (KeyError, TypeError, ValueError):
            continue
    return candles


def _download_sync(ticker: str, period: str, interval: str) -> Any:
    return yf.download(
        ticker,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=False,
        threads=False,
    )


async def fetch_candles(
    symbol: str,
    interval: str = "5m",
    period: str = "5d",
    count: int | None = None,
) -> list[Candle]:
    ticker = INDEX_TICKERS.get(symbol.upper(), symbol)
    cache_key = (ticker, interval, period)
    loop_time = asyncio.get_running_loop().time()
    async with _CANDLE_CACHE_LOCK:
        cached = _CANDLE_CACHE.get(cache_key)
        if cached and loop_time - cached[0] < CANDLE_MIN_INTERVAL_SECONDS:
            candles = cached[1]
            return candles[-count:] if count else candles
    try:
        frame = await asyncio.wait_for(
            asyncio.to_thread(_download_sync, ticker, period, interval),
            timeout=DATA_TIMEOUT_SECONDS,
        )
        candles = frame_to_candles(frame)
        if candles:
            async with _CANDLE_CACHE_LOCK:
                _CANDLE_CACHE[cache_key] = (asyncio.get_running_loop().time(), candles)
        return candles[-count:] if count else candles
    except Exception as exc:
        logger.warning("Candle provider failed for %s/%s: %s", symbol, interval, exc)
        return []


async def _last_two_daily(ticker: str) -> tuple[float, float, datetime | None]:
    try:
        frame = await asyncio.wait_for(
            asyncio.to_thread(_download_sync, ticker, "5d", "1d"),
            timeout=DATA_TIMEOUT_SECONDS,
        )
        candles = frame_to_candles(frame)
        if not candles:
            return 0.0, 0.0, None
        current = candles[-1]
        previous = candles[-2] if len(candles) > 1 else current
        return current.close, previous.close, current.timestamp
    except Exception as exc:
        logger.warning("Daily provider failed for %s: %s", ticker, exc)
        return 0.0, 0.0, None


class FreeNseClient:
    """Small, rate-limited client for publicly available NSE JSON endpoints."""

    def __init__(self) -> None:
        self._last_call: dict[str, float] = {}
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/120.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
            "Origin": "https://www.nseindia.com",
        }

    async def get_json(self, url: str, cache_key: str) -> Any | None:
        loop_time = asyncio.get_running_loop().time()
        async with self._lock:
            previous = self._cache.get(cache_key)
            if previous and loop_time - previous[0] < OPTION_CHAIN_MIN_INTERVAL_SECONDS:
                return previous[1]
            last = self._last_call.get(cache_key, 0.0)
            wait_for = OPTION_CHAIN_MIN_INTERVAL_SECONDS - (loop_time - last)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_call[cache_key] = asyncio.get_running_loop().time()

        timeout = aiohttp.ClientTimeout(total=DATA_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(headers=self._headers, timeout=timeout) as session:
                # NSE commonly requires a landing-page cookie before the API call.
                async with session.get("https://www.nseindia.com/", allow_redirects=True) as landing:
                    await landing.read()
                async with session.get(url, allow_redirects=True) as response:
                    if response.status != 200:
                        logger.warning("NSE endpoint returned HTTP %s for %s", response.status, cache_key)
                        return None
                    payload = await response.json(content_type=None)
            async with self._lock:
                self._cache[cache_key] = (asyncio.get_running_loop().time(), payload)
            return payload
        except Exception as exc:
            logger.warning("NSE endpoint failed for %s: %s", cache_key, exc)
            return None


class MarketDataService:
    def __init__(self) -> None:
        self.nse = FreeNseClient()
        self._option_cache: dict[str, OptionChainSnapshot] = {}

    async def option_chain(self, symbol: str) -> OptionChainSnapshot | None:
        symbol = symbol.upper()
        nse_symbol = NSE_CHAIN_SYMBOLS.get(symbol)
        if not nse_symbol:
            return None
        url = NSE_OPTION_CHAIN_URL.format(symbol=nse_symbol)
        payload = await self.nse.get_json(url, f"option-chain:{nse_symbol}")
        if not isinstance(payload, dict):
            return self._option_cache.get(symbol)

        try:
            records = payload.get("records", {})
            expiry_dates = records.get("expiryDates") or []
            data = records.get("data") or []
            if not expiry_dates or not data:
                return self._option_cache.get(symbol)
            expiry = expiry_dates[0]
            quotes: list[OptionQuote] = []
            for row in data:
                if row.get("expiryDate") != expiry:
                    continue
                strike = _safe_float(row.get("strikePrice"))
                if strike <= 0:
                    continue
                for option_type in ("CE", "PE"):
                    quote = row.get(option_type) or {}
                    quotes.append(
                        OptionQuote(
                            strike=strike,
                            option_type=option_type,
                            expiry=str(expiry),
                            ltp=_safe_float(quote.get("lastPrice")),
                            oi=_safe_float(quote.get("openInterest")),
                            oi_change=_safe_float(quote.get("changeinOpenInterest")),
                            volume=_safe_float(quote.get("totalTradedVolume")),
                            iv=_safe_float(quote.get("impliedVolatility")),
                        )
                    )
            spot = _safe_float(records.get("underlyingValue"))
            if spot <= 0 or not quotes:
                return self._option_cache.get(symbol)
            snapshot = OptionChainSnapshot(
                symbol=symbol,
                spot=spot,
                expiry=str(expiry),
                quotes=tuple(quotes),
                fetched_at=datetime.now(timezone.utc),
                source="NSE_PUBLIC",
            )
            self._option_cache[symbol] = snapshot
            return snapshot
        except Exception as exc:
            logger.warning("Could not parse option chain for %s: %s", symbol, exc)
            return self._option_cache.get(symbol)

    async def snapshot(self, symbol: str) -> MarketSnapshot:
        symbol = symbol.upper()
        candles_5m, candles_15m, chain = await asyncio.gather(
            fetch_candles(symbol, "5m", "5d", 120),
            fetch_candles(symbol, "15m", "10d", 80),
            self.option_chain(symbol),
        )
        spot = chain.spot if chain else (candles_5m[-1].close if candles_5m else 0.0)
        return MarketSnapshot(
            symbol=symbol,
            spot=spot,
            candles_5m=tuple(candles_5m),
            candles_15m=tuple(candles_15m),
            option_chain=chain,
            fetched_at=datetime.now(timezone.utc),
            source="FREE_YFINANCE_NSE",
        )

    async def fii_dii(self) -> dict[str, Any]:
        url = "https://www.nseindia.com/api/fiidiiTradeReact"
        payload = await self.nse.get_json(url, "fii-dii")
        result: dict[str, Any] = {"fii_net": None, "dii_net": None, "date": None, "source": "NSE_PUBLIC"}
        if not isinstance(payload, list):
            return result
        for row in payload:
            category = str(row.get("category", "")).upper()
            net = _safe_float(row.get("netValue"), math.nan)
            if math.isnan(net):
                buy = _safe_float(row.get("buyValue"), math.nan)
                sell = _safe_float(row.get("sellValue"), math.nan)
                net = buy - sell if not math.isnan(buy) and not math.isnan(sell) else math.nan
            if "FII" in category or "FPI" in category:
                result["fii_net"] = None if math.isnan(net) else net
            elif "DII" in category:
                result["dii_net"] = None if math.isnan(net) else net
            result["date"] = row.get("date") or result["date"]
        return result

    async def morning_data(self) -> dict[str, Any]:
        tickers = {
            "gift_nifty": os.getenv("GIFT_NIFTY_TICKER", "^NSEI"),
            "dow_jones": "^DJI",
            "nasdaq": "^IXIC",
            "crude_oil": "CL=F",
            "usd_inr": "INR=X",
            "india_vix": "^INDIAVIX",
        }
        keys = list(tickers)
        values = await asyncio.gather(*(_last_two_daily(tickers[key]) for key in keys))
        data: dict[str, Any] = {"source": "YAHOO_FINANCE_FREE"}
        for key, (current, previous, timestamp) in zip(keys, values):
            valid = current > 0
            change = ((current - previous) / previous * 100) if valid and previous else None
            data[key] = current if valid else None
            data[f"{key}_change"] = change
            data[f"{key}_timestamp"] = timestamp.isoformat() if timestamp else None
        fii_dii = await self.fii_dii()
        data.update(fii_dii)
        nifty = data.get("gift_nifty")
        data.update(
            {
                "r1": round(nifty + 60, 1) if nifty else None,
                "r2": round(nifty + 120, 1) if nifty else None,
                "s1": round(nifty - 60, 1) if nifty else None,
                "s2": round(nifty - 120, 1) if nifty else None,
                "gift_nifty_label": (
                    "Gift Nifty" if tickers["gift_nifty"] != "^NSEI" else "Nifty pre-open proxy"
                ),
            }
        )
        return data


market_data = MarketDataService()


# Compatibility helper retained for existing integrations and admin scripts.
def get_real_candles(sym: str, count: int = 20) -> Any:
    ticker = INDEX_TICKERS.get(sym.upper(), sym)
    try:
        frame = _download_sync(ticker, "5d", "5m")
        return frame.tail(count) if frame is not None else None
    except Exception as exc:
        logger.warning("Compatibility candle fetch failed for %s: %s", sym, exc)
        return None
