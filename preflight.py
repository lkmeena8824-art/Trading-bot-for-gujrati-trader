"""Safe deployment preflight.

Usage from the repository root:
    python preflight.py
    python preflight.py --network

The script validates configuration and database migrations without printing
secrets. The optional network check only reads free market-data endpoints; it
never creates or publishes a trade.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from config import AI_ENABLED, OPENAI_API_KEY, validate_config
from database import Database
from engine import get_scheduler_jobs
from market_data import market_data

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


async def run(network: bool) -> int:
    print("Elite Sniper preflight")
    print("- Secrets are not printed.")

    required_files = ["bot.py", "engine.py", "database.py", "market_data.py", "signal_engine.py", "requirements.txt"]
    missing_files = [name for name in required_files if not Path(name).exists()]
    if missing_files:
        print("FAIL: missing project files: " + ", ".join(missing_files))
        return 1
    print("PASS: project files present")

    config_ok = validate_config()
    if not config_ok:
        print("FAIL: add the private .env file in the repository root")
        return 1
    print("PASS: Telegram/channel/admin configuration is present")

    if AI_ENABLED and not OPENAI_API_KEY:
        print("WARN: AI_ENABLED=true but OPENAI_API_KEY is missing; templates will be used")
    else:
        print("PASS: zero-cost deterministic messaging is configured")

    expected_jobs = {
        "morning", "poll", "premarket", "open_pulse", "scanner", "no_trade",
        "oi", "closing", "pnl", "promo", "expiry", "monitor",
    }
    jobs = set(get_scheduler_jobs())
    if not expected_jobs.issubset(jobs):
        print("FAIL: scheduler registry is incomplete")
        return 1
    print("PASS: named scheduler registry is complete")

    database = Database()
    try:
        await database.connect()
        state = await database.get_daily_state()
        print(f"PASS: database migration/connectivity; market day {state['market_day']}")
    except Exception as exc:
        print(f"FAIL: database migration/connectivity: {exc}")
        return 1
    finally:
        await database.close()

    if network:
        print("Network smoke check: reading free data only; no trade will be posted")
        morning = await market_data.morning_data()
        snapshot = await market_data.snapshot("NIFTY")
        if snapshot.candles_5m and snapshot.candles_15m and snapshot.option_chain:
            print("PASS: free candles and option-chain data are available")
        else:
            print("WARN: free provider did not return all required data; bot will fail closed")
        if morning.get("india_vix") is not None:
            print("PASS: free morning/VIX data is available")
        else:
            print("WARN: free morning/VIX data is unavailable")

    print("Preflight complete")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Elite Sniper deployment without publishing trades")
    parser.add_argument("--network", action="store_true", help="also perform read-only free-data smoke checks")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.network)))


if __name__ == "__main__":
    main()
