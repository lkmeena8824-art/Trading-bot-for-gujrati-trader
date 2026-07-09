import math

def calculate_ema(data: list[float], period: int) -> list[float] | None:
    if len(data) < period: return None
    multiplier = 2 / (period + 1)
    seed = sum(data[:period]) / period
    result = [seed]
    for i in range(period, len(data)):
        result.append((data[i] - result[-1]) * multiplier + result[-1])
    return result

def calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        gains.append(change if change > 0 else 0)
        losses.append(abs(change) if change < 0 else 0)
    if len(gains) < period: return None
    avg_gain, avg_loss = sum(gains[:period])/period, sum(losses[:period])/period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain*(period-1)+gains[i])/period
        avg_loss = (avg_loss*(period-1)+losses[i])/period
    if avg_loss == 0: return 100.0
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def calculate_adx(highs: list, lows: list, closes: list, period: int = 14) -> float | None:
    if len(closes) < period * 2: return None
    tr_l, pdm_l, mdm_l = [], [], []
    for i in range(1, len(closes)):
        hd, ld = highs[i]-highs[i-1], lows[i-1]-lows[i]
        pdm_l.append(max(hd, 0) if hd > ld and hd > 0 else 0)
        mdm_l.append(max(ld, 0) if ld > hd and ld > 0 else 0)
        tr_l.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    if len(tr_l) < period: return None
    atr = sum(tr_l[:period])/period; sp_dm = sum(pdm_l[:period])/period; sm_dm = sum(mdm_l[:period])/period
    for i in range(period, len(tr_l)):
        atr = (atr*(period-1)+tr_l[i])/period; sp_dm = (sp_dm*(period-1)+pdm_l[i])/period; sm_dm = (sm_dm*(period-1)+mdm_l[i])/period
    if atr == 0: return None
    pdi, mdi = (sp_dm/atr)*100, (sm_dm/atr)*100
    di_sum = pdi + mdi
    return abs(pdi - mdi) / di_sum * 100 if di_sum != 0 else 0

def calculate_bollinger_bands(closes: list[float], period: int = 20, std_dev: float = 2) -> dict | None:
    if len(closes) < period: return None
    recent = closes[-period:]
    middle = sum(recent)/period
    std = math.sqrt(sum((x-middle)**2 for x in recent)/period)
    return {"upper": middle + std_dev*std, "middle": middle, "lower": middle - std_dev*std}

def calculate_volume_sma(volumes: list, period: int = 20) -> list[float] | None:
    if len(volumes) < period: return None
    return [sum(volumes[i-period+1:i+1])/period for i in range(period-1, len(volumes))]
