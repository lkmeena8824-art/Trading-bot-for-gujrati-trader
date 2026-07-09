import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from config import PLANS, PAYMENT_TELEGRAM_LINK, PAYMENT_WHATSAPP_LINK
from database import db
from utils.formatters import format_payment_info

logger = logging.getLogger(__name__)

async def cmd_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = []
    for name in PLANS:
        data = PLANS[name]
        disc = int((1 - data["price"]/data["original_price"])*100)
        emoji = "💎" if name=="Diamond" else "🥇" if name=="Gold" else "🥈" if name=="Silver" else "🥉" if name=="Bronze" else "⚡"
        buttons.append([InlineKeyboardButton(f"{emoji} {name} — ₹{data['price']:,} ({disc}% OFF)", callback_data=f"plan_{name}")])
    
    text = (
        "<b>🔥 PREMIUM VIP PLANS 🔥</b>\n\n"
        "Market ko dominate karna hai? Sahi plan chuno:\n\n"
    )
    for name, data in PLANS.items():
        disc = int((1 - data["price"]/data["original_price"])*100)
        emoji = "💎" if name=="Diamond" else "🥇" if name=="Gold" else "🥈" if name=="Silver" else "🥉" if name=="Bronze" else "⚡"
        text += "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n{} <b>{} Plan ({})</b>\n~~₹{:,.0f}~~ ➡️ <b>₹{:,.0f}</b> (<i>{}% OFF</i>)\n".format(emoji, name, f"{data['duration_days']} Days", data["original_price"], data["price"], disc)
        for feat in data["features"]: text += "  ✅ {}\n".format(feat)
        text += "\n"
    
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)

async def plan_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan_name = query.data.replace("plan_", "").capitalize()
    if plan_name not in PLANS: await query.edit_message_text("Invalid plan."); return
    
    text = format_payment_info(plan_name)
    buttons = [
        [InlineKeyboardButton("📱 WHATSAPP PE PAYMENT KARO", url=PAYMENT_WHATSAPP_LINK)],
        [InlineKeyboardButton("💬 TELEGRAM PE PAYMENT KARO", url=PAYMENT_TELEGRAM_LINK)],
        [InlineKeyboardButton("◀️ BACK TO PLANS", callback_data="back_plans")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)

async def back_plans_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    fake_update = type('obj', (object,), {'message': query.message, 'effective_user': query.from_user})()
    await cmd_plans(fake_update, context)

def register_subscription_handlers(app):
    app.add_handler(CommandHandler("plans", cmd_plans))
    app.add_handler(CallbackQueryHandler(plan_callback_handler, pattern=r"^plan_"))
    app.add_handler(CallbackQueryHandler(back_plans_handler, pattern=r"^back_plans$"))
