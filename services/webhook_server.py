import json, logging
from aiohttp import web
from config import WEBHOOK_HOST, WEBHOOK_PORT, WEBHOOK_SECRET
from database import db
from utils.risk_manager import RiskManager

logger = logging.getLogger(__name__)

async def handle_tv(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        app = request.app["telegram_app"]
        if not await app.bot_data["risk_manager"].can_trade(): return web.json_response({"status": "risk_blocked"})
        
        sym = body.get("ticker","NIFTY").split(":")[-1].replace("FUT","").strip()
        action = body.get("action","").lower()
        d = "BUY" if action in ("buy","long") else "SELL" if action in ("sell","short") else None
        if not d: return web.json_response({"status": "invalid_action"})
        
        p = float(body.get("price",0))
        if p<=0: return web.json_response({"status": "invalid_price"})
        
        rp = p*0.003
        sl = p-rp if d=="BUY" else p+rp
        t1 = p+rp if d=="BUY" else p-rp
        t2 = p+rp*2 if d=="BUY" else p-rp*2
        t3 = p+rp*3 if d=="BUY" else p-rp*3
        
        fc = await db.get_today_free_trade_count()
        ch = "FREE" if fc<2 else "VIP"
        tid = await app.bot_data["broadcaster"].post_trade_call(sym, d, p, round(sl,2), round(t1,2), round(t2,2), round(t3,2), "WEBHOOK", ch)
        return web.json_response({"status": "posted", "id": tid, "ch": ch})
    except Exception as e: return web.json_response({"status": "error", "reason": str(e)}, status=500)

async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "healthy"})

async def start_webhook_server(app):
    wa = web.Application()
    wa["telegram_app"] = app
    wa.router.add_post("/webhook/tradingview", handle_tv)
    wa.router.add_get("/health", handle_health)
    runner = web.AppRunner(wa)
    await runner.setup()
    site = web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook running on {WEBHOOK_PORT}")
