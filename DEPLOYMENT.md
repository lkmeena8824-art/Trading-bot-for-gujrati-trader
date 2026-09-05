# Live deployment — zero-cost route

## Short answer

For this bot, use an **Oracle Cloud Always Free Compute VM** as the primary host. The bot is a long-running Python process with APScheduler, Telegram polling, SQLite persistence and optional HTTP webhooks. A normal free serverless/web-service plan is not a good fit.

Oracle's official Always Free documentation currently lists up to two AMD micro VMs and a free Ampere A1 allowance of 1,500 OCPU-hours and 9,000 GB-hours per month, equivalent to 2 OCPUs and 12 GB for an Always Free tenancy. See the official limits at https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm. Availability and account verification are controlled by Oracle. Stay within the published limits and monitor the account for billing warnings.

Render is useful for a demo, but its official Free web-service documentation says services spin down after 15 minutes without inbound traffic and local filesystem data is ephemeral. That can stop scheduled jobs and lose the SQLite file, so it is not recommended for this production bot: https://render.com/docs/free.

No platform can promise unlimited 24/7 hosting at zero cost. Oracle is the closest fit, but the free account can have capacity, verification and idle-reclamation constraints.

## Option A — Oracle VM with Python systemd service (recommended)

### 1. Create the VM

In Oracle Cloud:

1. Create an Always Free Compute instance.
2. Choose Ubuntu 22.04/24.04 or Oracle Linux.
3. The AMD micro shape is the simplest architecture for this repository. An ARM A1 VM can also work, but use the Docker route only after verifying all Python wheels on that architecture.
4. Save the SSH private key locally. Never send it in chat.
5. Add an ingress rule for TCP 22. TCP 8080 is only needed if TradingView/payment webhooks will be called directly.

For normal Telegram polling, the bot does not need a public inbound Telegram port. The bot makes outbound HTTPS requests to Telegram and market-data services.

### 2. Connect and install base packages

Replace `VM_PUBLIC_IP` locally; do not put secrets into these commands.

```bash
ssh ubuntu@VM_PUBLIC_IP
sudo apt update
sudo apt install -y git python3-venv python3-pip ca-certificates
sudo useradd --system --create-home --shell /usr/sbin/nologin elitebot || true
sudo mkdir -p /opt/elite-sniper/data
sudo chown -R elitebot:elitebot /opt/elite-sniper
```

### 3. Clone this fixed session branch

```bash
sudo -u elitebot git clone -b arena/01a06f74-trading-bot-for-gujrati-trader \
  https://github.com/lkmeena8824-art/Trading-bot-for-gujrati-trader.git \
  /opt/elite-sniper
```

If the directory already exists during an update:

```bash
cd /opt/elite-sniper
sudo -u elitebot git fetch origin
sudo -u elitebot git checkout arena/01a06f74-trading-bot-for-gujrati-trader
sudo -u elitebot git pull --ff-only origin arena/01a06f74-trading-bot-for-gujrati-trader
```

### 4. Install Python dependencies

```bash
cd /opt/elite-sniper
sudo -u elitebot python3 -m venv .venv
sudo -u elitebot .venv/bin/pip install --upgrade pip
sudo -u elitebot .venv/bin/pip install -r requirements.txt
```

### 5. Copy the private `.env`

The file must be on the VM at:

```text
/opt/elite-sniper/.env
```

Copy it from your own computer with `scp`; do not paste the values into GitHub or chat:

```bash
scp /local/path/to/.env ubuntu@VM_PUBLIC_IP:/tmp/elite-sniper.env
ssh ubuntu@VM_PUBLIC_IP
sudo mv /tmp/elite-sniper.env /opt/elite-sniper/.env
sudo chown elitebot:elitebot /opt/elite-sniper/.env
sudo chmod 600 /opt/elite-sniper/.env
```

### 6. Run the safe preflight

```bash
cd /opt/elite-sniper
sudo -u elitebot .venv/bin/python preflight.py
sudo -u elitebot .venv/bin/python preflight.py --network
```

The preflight does not post Telegram calls. A warning from the network check means the free NSE/Yahoo provider is unavailable; the live bot will correctly fail closed rather than publish fake prices.

### 7. Install and start systemd

```bash
sudo cp deploy/elite-sniper.service /etc/systemd/system/elite-sniper.service
sudo systemctl daemon-reload
sudo systemctl enable --now elite-sniper
sudo systemctl status elite-sniper --no-pager
```

Live logs:

```bash
sudo journalctl -u elite-sniper -f
```

Stop/restart:

```bash
sudo systemctl restart elite-sniper
sudo systemctl stop elite-sniper
```

### 8. Verify the health endpoint

On the VM:

```bash
curl http://127.0.0.1:8080/health
```

Expected shape:

```json
{"status": "healthy", "quality_mode": "multi_confirmation"}
```

Keep port 8080 private unless a correctly authenticated HTTPS reverse proxy is configured. Set `WEBHOOK_SECRET` before exposing the webhook endpoints.

## Option B — Existing Render deployment

Yes, the same GitHub → Render process can be used. The repository now includes `render.yaml` configured for the fixed session branch and Docker health check.

In Render:

1. Open the existing service.
2. Confirm the service is connected to this repository.
3. Set the deploy branch to the branch you actually ship (`main` after the Phase 1 merge, or `arena/01a06fdf-trading-bot-for-gujrati-trader` while testing Phase 2). `render.yaml` currently points at the Phase 2 session branch.
4. Use Docker runtime and the repository `Dockerfile`, or create a new Blueprint from `render.yaml`.
5. Keep the existing secret environment variables in Render's Environment page; do not commit them and do not paste them into chat.
6. Confirm these non-secret values:
   - `DATABASE_PATH=/app/data/bot.db`
   - `MAX_DAILY_TRADES=3`
   - `DATA_PROVIDER=free`
   - `AI_ENABLED=false`
   - Phase 2 variables from the table in [Phase 2 environment variables](#phase-2-environment-variables)
7. Deploy the latest commit and inspect the deploy logs.
8. Check the Render health URL ending in `/health`.

The current Dockerfile already binds the aiohttp server to `0.0.0.0` and reads Render's `PORT` variable. Telegram polling does not require a public Telegram webhook port.

### Render free-plan warning

Render Free can run the container for testing, but its documented sleep and ephemeral-filesystem behavior means:

- APScheduler jobs can stop while the service sleeps.
- The SQLite file can disappear after restart/redeploy.
- Daily trade limits and subscription/trade history may reset.
- A free service is not a dependable 24/7 production host for this SQLite scheduler bot.

If the current Render service is on a paid always-on plan, the Render deployment path is suitable. If it is genuinely Free, use Render for staging or accept the persistence/uptime limitation; Oracle Always Free VM remains the better zero-cost production path.


## Phase 2 environment variables

Phase 2 adds an **optional** AI wording layer, poll/community memory, daily
market memory and contextual replies. Everything below is safe to leave at its
default: with `AI_ENABLED=false` the bot behaves exactly like Phase 1 and costs
nothing beyond hosting.

### Core (unchanged)

| Variable | Default | Notes |
| --- | --- | --- |
| `BOT_TOKEN` | — | Secret. Render → Environment. |
| `FREE_CHANNEL_ID` / `VIP_CHANNEL_ID` | — | Secret-ish channel IDs. |
| `ADMIN_IDS` | — | Comma separated Telegram user IDs. |
| `DATABASE_PATH` | `./data/bot.db` | Use `/app/data/bot.db` on Render/Docker. |
| `PORT` | `8080` | Render injects this automatically. |
| `WEBHOOK_SECRET` | empty | Required before exposing webhooks. |
| `MAX_DAILY_TRADES` | `3` | Hard quality cap, 1–3. |
| `DATA_PROVIDER` / `SIGNAL_SYMBOL` / `AUTO_TRADE_CHANNEL` | `free` / `NIFTY` / `FREE` | Unchanged. |

### AI provider abstraction (optional, billed by the provider)

| Variable | Default | Notes |
| --- | --- | --- |
| `AI_ENABLED` | `false` | Master switch. `false` = zero-cost deterministic templates, no API call is ever made. |
| `AI_PROVIDER` | `openai` | `openai`, `gemini`, `auto` (OpenAI first, then Gemini) or `none`. |
| `OPENAI_API_KEY` | empty | Secret. Required for the OpenAI provider. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Any chat-completions model. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Override for a compatible gateway. |
| `GEMINI_API_KEY` | empty | Secret. Required for the Gemini provider. |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Any `generateContent` model. |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta` | Override if needed. |
| `AI_MIN_INTERVAL_SECONDS` | `15` | Minimum gap between AI calls. |
| `AI_TIMEOUT_SECONDS` | `8` | Per-call timeout; on timeout the template is used. |
| `AI_MAX_TOKENS` | `350` | Output cap. |
| `AI_TEMPERATURE` | `0.3` | Lower is safer/steadier. |
| `AI_DAILY_CALL_BUDGET` | `200` | Hard daily spend guard; after this the bot returns to templates. |
| `AI_PERSONA_NAME` | `Sniper Bhai` | Persona name used in wording. |
| `AI_STYLE_LANGUAGE` | `hinglish` | `hinglish`, `english` or `gujarati`. |

If `AI_ENABLED=true` but no key is present, the bot logs a warning and keeps
using the free templates. It never fails to post because of AI.

### Contextual replies

| Variable | Default | Notes |
| --- | --- | --- |
| `REPLY_ENABLED` | `true` | Human-style replies. Works without AI (deterministic templates). |
| `REPLY_COOLDOWN_SECONDS` | `20` | Per-user cooldown. |
| `REPLY_MAX_PER_USER_PER_DAY` | `15` | Per-user daily cap. |
| `REPLY_IN_GROUPS` | `false` | When `true`, the bot also answers in Free/VIP groups, but only if it is mentioned or replied to. |

### Memory

| Variable | Default | Notes |
| --- | --- | --- |
| `COMMUNITY_MEMORY_ENABLED` | `true` | Stores per-member participation and usual poll leaning. |
| `POLL_TRACKING_ENABLED` | `true` | Stores who answered the daily sentiment poll and how. |
| `MARKET_MEMORY_DAYS` | `5` | How many recent market days the reply context summarises. |

New SQLite tables (`polls`, `poll_answers`, `community_memory`,
`market_memory`, `ai_usage`) are created automatically on startup. The
migration is additive; an existing `bot.db` from Phase 1 keeps working.

### Enabling AI on Render safely

1. Add `OPENAI_API_KEY` (or `GEMINI_API_KEY`) as a secret environment variable.
2. Set `AI_PROVIDER` accordingly and `AI_ENABLED=true`.
3. Keep `AI_DAILY_CALL_BUDGET` low (for example `50`) for the first day.
4. Redeploy and watch the logs for `AI text rejected by fact guard` lines — each
   one means the deterministic message was published instead.
5. Run `/aistatus` from an admin account to see provider, budget usage, guard
   rejections and memory counters.
6. To roll back to zero cost, set `AI_ENABLED=false` and redeploy. No other
   change is needed.

### What AI is not allowed to do

The guard in `ai_guard.py` mechanically rejects any AI output that:

- contains a number that is not already in the facts or the deterministic draft
  (so entry, SL, T1/T2/T3, RRR, OI and P&L can never be altered),
- introduces or flips a BUY/SELL/CE/PE direction,
- drops a critical number that the deterministic draft published,
- promises profit, guarantees accuracy or claims "no risk",
- uses markup outside the Telegram-safe subset.

Rejected output is replaced by the deterministic text and logged. The signal
engine remains the only component that decides direction, entry, SL, targets,
RRR and OI interpretation.

## Option C — Docker on the VM

From `/opt/elite-sniper`, after placing `.env` in the repository root:

```bash
sudo -u elitebot docker compose -f deploy/docker-compose.yml up -d --build
sudo docker compose -f deploy/docker-compose.yml logs -f
```

The compose file mounts `/opt/elite-sniper/data` into the container, so the SQLite database survives container recreation.

## Updating without losing the database

```bash
sudo systemctl stop elite-sniper
sudo cp /opt/elite-sniper/data/bot.db \
  /opt/elite-sniper/data/bot.db.backup.$(date +%Y%m%d-%H%M%S)
cd /opt/elite-sniper
sudo -u elitebot git pull --ff-only origin arena/01a06f74-trading-bot-for-gujrati-trader
sudo -u elitebot .venv/bin/pip install -r requirements.txt
sudo -u elitebot .venv/bin/python preflight.py
sudo systemctl start elite-sniper
sudo journalctl -u elite-sniper -n 100 --no-pager
```

## First-live checklist

- Use a staging Telegram bot/channel first.
- Confirm `.env` is mode `600` and is not tracked by Git.
- Confirm `MAX_DAILY_TRADES=3`.
- Keep `AI_ENABLED=false` for the zero-cost setup.
- If AI is enabled, confirm `/aistatus` shows the expected provider and budget.
- Confirm the daily poll appears and `/aistatus` shows voters after members vote.
- Confirm `/health` and `systemctl status`.
- Confirm morning, poll, open-pulse and no-trade jobs in logs.
- Confirm free option-chain data is fresh before enabling live calls.
- Remember that this bot publishes alerts; it does not place broker orders.
