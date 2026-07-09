from config import PLANS, PAYMENT_DETAILS_TEXT, PAYMENT_TELEGRAM_LINK, PAYMENT_WHATSAPP_LINK, RISK_DISCLAIMER

def format_morning_setup(data: dict) -> str:
    gn, gn_ch, gn_e = data["gift_nifty"], data["gift_nifty_change"], "🟢" if data["gift_nifty_change"]>=0 else "🔴"
    d_e = "🟢" if data["dow_change"]>=0 else "🔴"
    n_e = "🟢" if data["nasdaq_change"]>=0 else "🔴"
    mood = "🟢 BULLISH BIAS" if gn_ch>=0 else "🔴 BEARISH BIAS"
    return (
        "<b>🔥 GOOD MORNING TRADERS! MARKET READY HAI PANGA LENE KE LIYE! 🔥</b>\n\n"
        "<b>📊 GIFT NIFTY:</b> <code>{:,.1f}</code> ({} <i>{:+.2f}%</i>)\n"
        "<b>🇺🇸 DOW JONES:</b> <code>{:,.1f}</code> ({} <i>{:+.2f}%</i>)\n"
        "<b>💻 NASDAQ:</b> <code>{:,.1f}</code> ({} <i>{:+.2f}%</i>)\n"
        "<b>🛢️ CRUDE OIL:</b> <code>${:,.2f}</code>\n"
        "<b>💰 USD/INR:</b> <code>{:.2f}</code>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>📐 NIFTY KEY LEVELS FOR TODAY</b>\n"
        "🔺 <b>R1:</b> <code>{:,.1f}</code> | 🔺 <b>R2:</b> <code>{:,.1f}</code>\n"
        "🔻 <b>S1:</b> <code>{:,.1f}</code> | 🔻 <b>S2:</b> <code>{:,.1f}</code>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>📈 Market Mood: {}</b>\n\n"
        "<i>⏰ First call 09:30 AM ke baad aayega. Channel pe set raho!</i>\n\n"
        "<i>{}</i>"
    ).format(gn, gn_e, gn_ch, data["dow_jones"], d_e, data["dow_change"], data["nasdaq"], n_e, data["nasdaq_change"], data["crude_oil"], data["usd_inr"], data["nifty_resistance_1"], data["nifty_resistance_2"], data["nifty_support_1"], data["nifty_support_2"], mood, RISK_DISCLAIMER)


def format_trade_call(symbol: str, direction: str, entry_price: float, sl: float, target1: float, target2: float, target3: float, strategy: str, status: str = "ACTIVE", **kwargs) -> str:
    risk = abs(entry_price - sl)
    rrr = abs(target2 - entry_price) / risk if risk > 0 else 0
    status_txt = {"ACTIVE": "🟢 LIVE", "T1_HIT": "✅ T1 DONE", "T2_HIT": "🎯 T2 DONE", "T3_HIT": "🏆 JACKPOT", "SL_HIT": "❌ SL HIT"}.get(status, status)
    dir_txt = "🟢 BUY (LONG)" if direction == "BUY" else "🔴 SELL (SHORT)"
    return (
        "<b>🚀 JACKPOT CALL — {}</b>\n\n"
        "<b>📍 TYPE:</b> {}\n"
        "<b>🎯 ENTRY:</b> <code>{:,.2f}</code>\n"
        "<b>🛡️ SL:</b> <code>{:,.2f}</code> <i>(Strict! No negotiation)</i>\n"
        "<b>✅ T1:</b> <code>{:,.2f}</code>\n"
        "<b>✅ T2:</b> <code>{:,.2f}</code>\n"
        "<b>✅ T3:</b> <code>{:,.2f}</code>\n\n"
        "<b>⚡ STRATEGY:</b> <i>{}</i>\n"
        "<b>📊 RISK:REWARD:</b> <code>1:{:.1f}+</code>\n"
        "<b>📌 STATUS:</b> {}\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>RULES:</b>\n"
        "✅ Entry ke baad SL modify mat karo\n"
        "✅ T1 hit pe half book, SL cost pe trail\n\n"
        "<i>{}</i>"
    ).format(symbol, dir_txt, entry_price, sl, target1, target2, target3, strategy.replace('_', ' '), rrr, status_txt, RISK_DISCLAIMER)


def format_trade_update(trade: dict, update_type: str, new_sl: float = None, points: float = None) -> str:
    s, d, e = trade["symbol"], trade["direction"], trade["entry_price"]
    pts_str = f"<code>+{points:,.1f}</code>" if points and points >= 0 else f"<code>{points:,.1f}</code>"
    
    if update_type == "T1_HIT":
        return "<b>✅ T1 HIT! — {}</b>\n\n📍 {} @ <code>{:,.2f}</code>\n🎯 T1: <code>{:,.2f}</code> <b>ACHIEVED!</b>\n💰 Points: {}\n\n🔄 <b>ACTION:</b> Half book karo\n🛡️ <b>SL TRAILED TO COST:</b> <code>{:,.2f}</code>\n\n<i>{}</i>".format(s, d, e, trade["target1"], pts_str, new_sl, RISK_DISCLAIMER)
    elif update_type == "T2_HIT":
        return "<b>🎯 T2 HIT! — {}</b>\n\n📍 {} @ <code>{:,.2f}</code>\n🎯 T2: <code>{:,.2f}</code> <b>ACHIEVED!</b>\n💰 Total Points: {}\n\n🔥 <b>TRAILING T3 KE LIYE!</b>\n\n<i>{}</i>".format(s, d, e, trade["target2"], pts_str, RISK_DISCLAIMER)
    elif update_type == "T3_HIT":
        return "<b>🏆 T3 HIT! JACKPOT! — {}</b>\n\n📍 {} @ <code>{:,.2f}</code>\n🎯 T3: <code>{:,.2f}</code> <b>ACHIEVED!</b>\n💰 <b>TOTAL POINTS: {}</b>\n\n🔥🔥🔥 <b>FULL TARGET! SAALI MARKET KA RAJA BAN GAYE!</b> 🔥🔥🔥\n\n<i>{}</i>".format(s, d, e, trade["target3"], pts_str, RISK_DISCLAIMER)
    elif update_type == "SL_HIT":
        return "<b>❌ SL HIT — {}</b>\n\n📍 {} @ <code>{:,.2f}</code>\n🛡️ SL: <code>{:,.2f}</code> <b>TRIGGERED</b>\n💰 Points: {}\n\n✅ <b>Discipline maintain karo, next call pe focus!</b>\n\n<i>{}</i>".format(s, d, e, trade["sl"], pts_str, RISK_DISCLAIMER)
    return f"<b>📋 Update:</b> {update_type} for {s}"

def format_fomo_post(trade: dict, update_type: str, points: float) -> str:
    t_lvl = "T2" if update_type == "T2_HIT" else "T3"
    return (
        "<b>🔥 VIP CALL RESULT 🔥</b>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>📊 {}</b> | {}\n"
        "📍 Entry: <b>[HIDDEN FOR VIPs 🔒]</b>\n"
        "🛡️ SL: <b>[HIDDEN FOR VIPs 🔒]</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>🏆 {} ACHIEVED!</b>\n"
        "<b>💰 Points Gained: +{:,.1f}</b>\n\n"
        "<i>Yeh call sirf VIP members ko milti hai.</i>\n"
        "<i>Free channel pe sirf results dikhte hain.</i>\n\n"
        "<i>{}</i>"
    ).format(trade["symbol"], trade["strategy"].replace('_',' '), t_lvl, points, RISK_DISCLAIMER)

def format_oi_data(data: dict) -> str:
    return (
        "<b>📊 LIVE OI DATA UPDATE</b>\n\n"
        "<b>⏰ Time:</b> <code>{}</code>\n\n"
        "<b>📞 Total Call OI:</b> <code>{:,}</code>\n"
        "<b>📞 Total Put OI:</b> <code>{:,}</code>\n\n"
        "<b>📊 PCR:</b> <code>{:.3f}</code>\n"
        "<b>📍 Max Call OI:</b> <code>{:,.1f}</code> <i>(Resistance)</i>\n"
        "<b>📍 Max Put OI:</b> <code>{:,.1f}</code> <i>(Support)</i>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>📈 Sentiment:</b> {}\n\n"
        "<i>{}</i>"
    ).format(data["timestamp"], data["total_call_oi"], data["total_put_oi"], data["pcr"], data["max_call_oi_strike"], data["max_put_oi_strike"], data["sentiment"], RISK_DISCLAIMER)

def format_marketing_post(post_type: str) -> str:
    plans_text = ""
    for n, d in PLANS.items():
        if n == "Trial": continue
        disc = int((1 - d["price"]/d["original_price"])*100)
        plans_text += "\n{} <b>{} ({})</b>\n~~₹{:,.0f}~~ ➡️ <b>₹{:,.0f}</b> (<i>{}% OFF</i>)\n".format("💎" if n=="Diamond" else "🥇" if n=="Gold" else "🥈" if n=="Silver" else "🥉", n, f"{d['duration_days']} Days", d["original_price"], d["price"], disc)
    
    return (
        "<b>🔥 SPECIAL OFFER — LIMITED TIME! 🔥</b>\n\n"
        "{}\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>PREMIUM PLANS:</b>{}\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>🚀 VIP me kya milta hai:</b>\n"
        "✅ 4 Algo Strategies se High-RRR Calls\n"
        "✅ Real-time Target Hit Updates\n"
        "✅ Trailing SL Management\n\n"
        "<i>{}</i>"
    ).format("Market abhi sideways ja raha hai!" if post_type=="mid_day" else "Kal market open hone se pehle ready ho jaao!", plans_text, RISK_DISCLAIMER)

def format_evening_summary(trades: list[dict]) -> str:
    if not trades:
        return "<b>📋 TODAY'S MARKET SUMMARY</b>\n\n📊 Aaj koi trade nahi li — quality over quantity!\n\n<i>{}</i>".format(RISK_DISCLAIMER)
    
    wins = sum(1 for t in trades if t["status"] in ("T1_HIT","T2_HIT","T3_HIT"))
    losses = sum(1 for t in trades if t["status"] == "SL_HIT")
    pts = sum(t.get("points_gained",0) for t in trades)
    wr = (wins/(wins+losses)*100) if (wins+losses)>0 else 0
    
    text = (
        "<b>📋 TODAY'S PERFORMANCE REPORT</b>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>📊 Total Trades:</b> <code>{}</code>\n"
        "<b>✅ Winners:</b> <code>{}</code>\n"
        "<b>❌ Losers:</b> <code>{}</code>\n"
        "<b>📈 Win Rate:</b> <code>{:.0f}%</code>\n"
        "<b>💰 Net Points:</b> <code>{:+,.1f}</code>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
    ).format(len(trades), wins, losses, wr, pts)
    
    for t in trades:
        p = t.get("points_gained",0)
        text += "{} <b>{} {}</b> | <code>{:+,.1f} pts</code>\n".format({"T1_HIT":"✅","T2_HIT":"🎯","T3_HIT":"🏆","SL_HIT":"❌"}.get(t["status"],"⏳"), t["symbol"], t["direction"], p)
    
    text += "\n💪 <b>Consistency is the key!</b>\n\n<i>{}</i>".format(RISK_DISCLAIMER)
    return text

def format_stats_message(stats: list[dict]) -> str:
    if not stats: return "<b>📊 No stats yet.</b>"
    tt = sum(s.get("total_trades",0) for s in stats)
    tw = sum(s.get("winning_trades",0) for s in stats)
    tl = sum(s.get("losing_trades",0) for s in stats)
    tp = sum(s.get("total_points",0) for s in stats)
    wr = (tw/(tw+tl)*100) if (tw+tl)>0 else 0
    return "<b>📊 BOT PERFORMANCE</b>\n\n<b>Trades:</b> <code>{}</code> | <b>W/L:</b> <code>{}/{}</code> | <b>WinRate:</b> <code>{:.0f}%</code> | <b>Points:</b> <code>{:+,.1f}</code>".format(tt, tw, tl, wr, tp)

def format_payment_info(plan_name: str) -> str:
    plan = PLANS.get(plan_name)
    if not plan: return "Invalid Plan."
    disc = int((1 - plan["price"]/plan["original_price"])*100)
    return (
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>💳 PAYMENT DETAILS</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>Plan:</b> <i>{} ({})</i>\n"
        "<b>Amount to Pay:</b> <code>₹{:,.0f}</code> <i>(~~₹{:,.0f}~~ | {}% OFF)</i>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "{}\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<i>📌 Payment ke baad neeche button se screenshot bhejna. Admin verify karke 2 min me VIP access activate kar dega.</i>\n\n"
        "<i>{}</i>"
    ).format(plan_name, f"{plan['duration_days']} Days", plan["price"], plan["original_price"], disc, PAYMENT_DETAILS_TEXT.replace('\n', '\n'), RISK_DISCLAIMER)
