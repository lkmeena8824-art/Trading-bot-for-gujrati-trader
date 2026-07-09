import logging
from datetime import datetime
from config import MAX_DAILY_TRADES, ACTIVE_WINDOWS, NO_TRADE_WINDOWS

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, db): self.db = db

    async def can_trade(self) -> bool:
        if not self._is_weekday(): return False
        if self._is_no_trade_zone(): return False
        if not self._is_active_window(): return False
        if await self._is_daily_limit_reached(): return False
        return True

    def _is_weekday(self) -> bool: return datetime.now().weekday() < 5
    
    def _is_no_trade_zone(self) -> bool:
        now = datetime.now()
        for sh, sm, eh, em in NO_TRADE_WINDOWS:
            if now.replace(hour=sh, minute=sm, second=0) <= now <= now.replace(hour=eh, minute=em, second=0): return True
        return False

    def _is_active_window(self) -> bool:
        now = datetime.now()
        for sh, sm, eh, em in ACTIVE_WINDOWS:
            if now.replace(hour=sh, minute=sm, second=0) <= now <= now.replace(hour=eh, minute=em, second=0): return True
        return False

    async def _is_daily_limit_reached(self) -> bool:
        return await self.db.get_today_trade_count() >= MAX_DAILY_TRADES
