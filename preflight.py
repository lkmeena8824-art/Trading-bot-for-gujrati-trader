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

import config
from config import validate_config
from database import Database
from engine import get_scheduler_jobs
from market_data import market_data
from message_ai import ai_available

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

    active_provider = config.active_ai_provider()
    if not config.AI_ENABLED:
        print("PASS: zero-cost deterministic messaging is configured (AI_ENABLED=false)")
    elif active_provider == "none":
        print(
            "WARN: AI_ENABLED=true but no usable provider key "
            f"(AI_PROVIDER={config.AI_PROVIDER}); deterministic templates will be used"
        )
    else:
        model = config.OPENAI_MODEL if active_provider == "openai" else config.GEMINI_MODEL
        print(f"PASS: AI wording layer active via {active_provider} ({model}); key value not printed")
        print(f"      daily call budget: {config.AI_DAILY_CALL_BUDGET}, min interval: {config.AI_MIN_INTERVAL_SECONDS}s")
    print(f"PASS: AI can never decide direction/entry/SL/target/RRR/OI/P&L (guarded); live AI call possible: {ai_available()}")
    print(
        "PASS: contextual replies "
        f"{'enabled' if config.REPLY_ENABLED else 'disabled'} "
        f"(cooldown {config.REPLY_COOLDOWN_SECONDS}s, max {config.REPLY_MAX_PER_USER_PER_DAY}/user/day, "
        f"groups: {config.REPLY_IN_GROUPS})"
    )
    print(
        "PASS: memory "
        f"community={config.COMMUNITY_MEMORY_ENABLED}, polls={config.POLL_TRACKING_ENABLED}, "
        f"market history days={config.MARKET_MEMORY_DAYS}"
    )

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
        # Phase 2 tables must exist for polls, community and market memory.
        await database.get_market_memory()
        await database.community_stats()
        await database.ai_call_count()
        print("PASS: Phase 2 memory tables (polls, poll_answers, community_memory, market_memory, ai_usage)")
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
