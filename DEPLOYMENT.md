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

## Option B — Docker on the VM

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
- Confirm `/health` and `systemctl status`.
- Confirm morning, poll, open-pulse and no-trade jobs in logs.
- Confirm free option-chain data is fresh before enabling live calls.
- Remember that this bot publishes alerts; it does not place broker orders.
