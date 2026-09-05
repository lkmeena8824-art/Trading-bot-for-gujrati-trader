# Elite Sniper Telegram Trading Bot

This project is a zero-cost-by-default Telegram market-alert bot. It keeps the existing Telegram commands, Free/VIP channels, subscriptions, promotions and webhooks, while adding a quality-first signal pipeline.

## Phase 2 at a glance

- **Provider abstraction** — `ai_provider.py` implements a single interface with
  `OpenAIProvider`, `GeminiProvider` and a `DisabledProvider`. `AI_PROVIDER`
  selects one (`openai`, `gemini`, `auto`, `none`).
- **AI message generation with deterministic fallback** — `message_ai.py` asks the
  provider to rephrase the free template, then publishes AI text only if the
  fact guard approves. Any error, timeout or budget exhaustion silently falls
  back to the template.
- **Fact guard** — `ai_guard.py` mechanically blocks number changes, direction
  changes, dropped facts, profit promises and unsafe markup.
- **Poll-answer tracking and community memory** — `community.py` records who
  voted for which sentiment option and builds per-member and per-day
  participation memory.
- **Daily market state memory** — `market_memory.py` stores pre-open cues, the
  opening print, the engine's own verdict per slot, calls admitted and the
  closing result for the last `MARKET_MEMORY_DAYS` days.
- **Contextual human-style replies** — `replies.py` classifies a member message
  (greeting, tip request, loss, payment, VIP, poll, timing, market, complaint)
  and answers using community + market memory, in Hinglish, without ever giving
  a direction.
- **`/aistatus`** — admin-only view of provider, budget, guard rejections and
  memory counters.

### The hard AI rule

AI is a wording layer. It **never** decides BUY/SELL and **never** changes
entry, SL, targets, RRR, OI or P&L. Those come from `signal_engine.py` only, and
`ai_guard.py` rejects any AI text that violates the rule (the deterministic
message is published instead). With `AI_ENABLED=false` no AI call happens at
all, so the zero-cost mode is unchanged.

## Important zero-cost reality

- No paid OpenAI/Gemini API is required. `AI_ENABLED=false` is the default and deterministic Telegram templates are used.
- Candles use the free `yfinance` source and option-chain/FII-DII snapshots use the public NSE JSON endpoint.
- Free/public market data can be delayed, throttled or unavailable. The bot fails closed: if the option chain or required timeframes are not fresh, it does **not** publish a trade.
- This is an analysis/alert system, not a broker order executor. Telegram stop updates cannot guarantee that a user executed them.
- Public data sources are not a substitute for a licensed real-time broker/TrueData feed.

## New quality rules

- Maximum three automated trade decisions per Indian market day.
- One opportunity per slot: 09:15–10:15, 10:45–11:15, and 12:45–13:15 IST.
- Five-minute breakout/breakdown with volume confirmation.
- Fifteen-minute trend confirmation.
- VWAP and RSI confirmation.
- Near-ATM option-chain OI decay must agree with direction.
- Final target must be at least 1:3 RRR.
- 1R moves the stop to cost; 2R books 50%; 3R manages/closes the remaining 50%.
- No forced call when the market is choppy, ranging, stale or contradictory.

## Schedule

- 08:00 — Institutional morning brief
- 08:30 — Four-option smart-money poll
- 09:00 — Controlled pre-market hype
- 09:15 — Market open pulse
- 09:15–10:15 — First sniper window
- 10:45–11:15 — Second sniper window
- 12:00 — No-trade checkpoint when no valid setup exists
- 12:45–13:15 — Third sniper window
- 15:15 — Closing Bell Summary
- 15:30 — Final P&L report
- 15:45 and 20:30 — Existing promotion jobs

## Environment

Copy these values into a private `.env` file. Do not commit it.

```text
BOT_TOKEN=...
FREE_CHANNEL_ID=-100...
VIP_CHANNEL_ID=-100...
ADMIN_IDS=123456789
DATABASE_PATH=./data/bot.db
PORT=8080

# Optional webhook protection. If set, callers must send X-Webhook-Secret.
WEBHOOK_SECRET=...

# Zero-cost defaults:
AI_ENABLED=false
DATA_PROVIDER=free
SIGNAL_SYMBOL=NIFTY
AUTO_TRADE_CHANNEL=FREE
MAX_DAILY_TRADES=3
```

Optional AI wording is deliberately disabled by default. If enabled, it requires a separately billed provider key and falls back to templates on every error:

```text
# OpenAI
AI_ENABLED=true
AI_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini

# or Gemini
AI_ENABLED=true
AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-1.5-flash
```

Phase 2 variables (all optional, safe defaults):

```text
AI_PROVIDER=openai            # openai | gemini | auto | none
OPENAI_BASE_URL=https://api.openai.com/v1
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
AI_MIN_INTERVAL_SECONDS=15
AI_TIMEOUT_SECONDS=8
AI_MAX_TOKENS=350
AI_TEMPERATURE=0.3
AI_DAILY_CALL_BUDGET=200
AI_PERSONA_NAME=Sniper Bhai
AI_STYLE_LANGUAGE=hinglish     # hinglish | english | gujarati

REPLY_ENABLED=true
REPLY_COOLDOWN_SECONDS=20
REPLY_MAX_PER_USER_PER_DAY=15
REPLY_IN_GROUPS=false

COMMUNITY_MEMORY_ENABLED=true
POLL_TRACKING_ENABLED=true
MARKET_MEMORY_DAYS=5
```

The complete table, including which values are secrets, is in
[`DEPLOYMENT.md`](DEPLOYMENT.md#phase-2-environment-variables). `.env.example`
lists every variable with its default.

The default `GIFT_NIFTY_TICKER` is `^NSEI`, which is labelled in the message as a Nifty pre-open proxy. Configure a valid free ticker only if a reliable source is available.

## Run locally

The private environment file belongs here:

```text
/home/user/Trading-bot-for-gujrati-trader/.env
```

Do not paste its values into chat or commit it. `python-dotenv` loads it automatically when the bot is started from the repository root. `requirements.txt` is already in the repository root and is installed with the command below.

1. Create a Python 3.12 virtual environment.
2. Install `requirements.txt`.
3. Copy your private `.env` into the repository root.
4. Run the safe preflight: `python preflight.py`.
5. Optionally test free market endpoints without publishing a trade: `python preflight.py --network`.
6. Run `python bot.py`.
7. Keep `data/` persistent because it contains the trade ledger and subscription database.

Preflight never prints secrets and never creates a Telegram trade. It is the next command to run after placing `.env`.

## Live hosting

The complete deployment guide is in [`DEPLOYMENT.md`](DEPLOYMENT.md). The repository also includes `render.yaml` for the existing GitHub → Render workflow. If the Render service is on a paid always-on plan, use it normally; Render Free sleeps and has ephemeral local storage, so Oracle Cloud Always Free VM with a persistent `data/` directory is the safer zero-cost production path.

## Docker

Build the image and run it with a persistent host directory mounted at `/app/data`. Use one bot process so APScheduler has a single owner. Configure a staging bot/channel before enabling production alerts.

## Tests

```bash
python -m unittest discover -s tests
```

The suite covers the original signal/quality/database rules plus Phase 2:
provider selection and request shapes, deterministic fallback behaviour, the
fact guard (number, direction, dropped-fact, promise and markup rules),
poll-answer tracking, community and market memory, reply intent handling, and
an engine integration test proving the existing Telegram handlers, scheduler
jobs and zero-cost message text are unchanged.

## Operational notes

- The `trade_events`, `daily_sessions`, `trade_slots`, `message_log` and expanded `trades` fields provide restart-safe state and P&L history.
- `/forcecall` remains available to admins as a clearly labelled manual override, but it still obeys the active slot and three-trade daily policy.
- TradingView webhooks are re-checked against the local multi-confirmation engine before posting.
- Never treat an AI-generated message as market evidence. The signal engine owns all prices, direction, OI interpretation, stops and targets.
- Community poll sentiment is engagement data only; it is never a signal input.
- Contextual replies run in PTB handler group 1, so the existing spam guard in group 0 keeps full priority.
- Every AI call is logged to the `ai_usage` table with provider, status and latency for cost review.
- Always validate signals in shadow/paper mode before publishing them to a live channel.
