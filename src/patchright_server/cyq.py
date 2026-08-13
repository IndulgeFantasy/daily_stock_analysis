# -*- coding: utf-8 -*-
"""东财筹码分布（CYQ）抓取与计算（patchright 浏览器路径）。

数值口径与 https://quote.eastmoney.com/{sz,sh}{code}.html#fullScreenChart
页面显示一致：
- 日 K：fqt=1 前复权、end=今日、lmt=210（最近 210 个交易日）
- 算法：东财 CYQCalculator（factor=150, range=120），与页面 quotekchart 同源

注意：请求 kline 接口时不要携带 smplmt 参数（会导致东财返回全历史等间隔
采样数据，筹码窗口严重失真）；页面筹码图为 canvas 渲染，DOM 无文本可读，
故采用「浏览器导航接口读取 JSONP 文本 + 本地同算法计算」。
"""

import json
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from data_provider.base import (
    _is_etf_code,
    _is_hk_market,
    _is_us_market,
    normalize_stock_code,
)

logger = logging.getLogger(__name__)

_UT_TOKEN = "fa5fd1943c7b386f172d6893dbfba10b"
_FACTOR = 150  # CYQCalculator accuracyFactor，与页面一致
_DEFAULT_RANGE_BARS = 120  # 页面 range=120（最近 120 个交易日）

# 服务端轻量缓存：key = f"{code}:{fqt}" -> (expire_ts, records)
_cache: Dict[str, Tuple[float, List[Dict[str, float]]]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 300.0  # 盘中数据 5 分钟失效，跨日自动过期


def _eastmoney_market(code: str) -> Tuple[str, str]:
    """返回 (行情页交易所前缀, 东财 secid market)。

    东财 secid 约定：market=0 深市（00/002/300/北交所），market=1 沪市（60/68）。
    """
    if code.startswith("6"):
        return "sh", "1"
    return "sz", "0"


def parse_kline_records(jsonp_text: str) -> List[Dict[str, float]]:
    """解析 push2his kline/get 的 JSONP 文本（如 '__cyqScrape({...});'）为记录列表。"""
    start = jsonp_text.find("(")
    end = jsonp_text.rfind(")")
    if start < 0 or end <= start:
        raise ValueError(f"非法 JSONP 响应: {jsonp_text[:80]!r}")
    data = json.loads(jsonp_text[start + 1 : end])
    records: List[Dict[str, float]] = []
    for item in data["data"]["klines"]:
        p = item.split(",")
        if len(p) < 11:
            continue
        records.append(
            {
                "date": p[0],
                "open": float(p[1]),
                "close": float(p[2]),
                "high": float(p[3]),
                "low": float(p[4]),
                "volume": float(p[5]),
                "amount": float(p[6]),
                "zf": float(p[7]),
                "zdf": float(p[8]),
                "zde": float(p[9]),
                "hsl": float(p[10]),
            }
        )
    if not records:
        raise ValueError("K 线记录为空")
    return records


def compute_cyq_metrics(records: List[Dict[str, float]], range_bars: int = _DEFAULT_RANGE_BARS) -> Dict[str, Any]:
    """Python 移植版 CYQCalculator（东财页面算法，已与页面 JS 双引擎对齐）。

    :param records: 日 K 记录列表（升序），每项含 open/close/high/low/hsl
    :param range_bars: 参与计算的 K 线窗口（页面为 120）
    """
    start = max(0, len(records) - range_bars) if range_bars else 0
    kdata = records[start:]
    if not kdata:
        raise ValueError("invaild index")

    maxprice = max(r["high"] for r in kdata)
    minprice = min(r["low"] for r in kdata)
    accuracy = max(0.01, (maxprice - minprice) / (_FACTOR - 1))
    xdata = [0.0] * _FACTOR

    for ele in kdata:
        o, c, h, l = ele["open"], ele["close"], ele["high"], ele["low"]
        avg = (o + c + h + l) / 4
        turnover = min(1.0, (ele.get("hsl") or 0) / 100.0)
        high_idx = int((h - minprice) // accuracy)
        low_idx = int(math.ceil((l - minprice) / accuracy))
        g0 = _FACTOR - 1 if h == l else 2.0 / (h - l)
        g1 = int((avg - minprice) // accuracy)
        for n in range(_FACTOR):
            xdata[n] *= 1 - turnover
        if h == l:
            xdata[g1] += g0 * turnover / 2
        else:
            for j in range(low_idx, high_idx + 1):
                cur = minprice + accuracy * j
                if cur <= avg:
                    xdata[j] += (
                        (cur - l) / (avg - l) * g0 * turnover
                        if abs(avg - l) >= 1e-8
                        else g0 * turnover
                    )
                else:
                    xdata[j] += (
                        (h - cur) / (h - avg) * g0 * turnover
                        if abs(h - avg) >= 1e-8
                        else g0 * turnover
                    )

    current = kdata[-1]["close"]
    total = sum(xdata)
    x12 = [float(f"{x:.12g}") for x in xdata]  # 对齐 JS toPrecision(12)

    def get_cost(chip: float) -> float:
        acc = 0.0
        for i, x in enumerate(x12):
            if acc + x > chip:
                return minprice + i * accuracy
            acc += x
        return 0.0

    below = sum(x12[i] for i in range(_FACTOR) if current >= minprice + accuracy * i)
    benefit = below / total if total else 0.0
    avg_cost = round(get_cost(total * 0.5), 2)

    def _pct(percent: float):
        lo_p, hi_p = (1 - percent) / 2, (1 + percent) / 2
        lo, hi = get_cost(total * lo_p), get_cost(total * hi_p)
        conc = 0.0 if (lo + hi) == 0 else (hi - lo) / (lo + hi)
        return [round(lo, 2), round(hi, 2)], conc

    p90, c90 = _pct(0.9)
    p70, c70 = _pct(0.7)
    return {
        "date": kdata[-1]["date"],
        "profit_ratio": benefit,
        "avg_cost": avg_cost,
        "cost_90_low": p90[0],
        "cost_90_high": p90[1],
        "concentration_90": c90,
        "cost_70_low": p70[0],
        "cost_70_high": p70[1],
        "concentration_70": c70,
    }


async def _fetch_kline_text(page: Any, code: str, fqt: str) -> str:
    """先打开行情页（建立东财 referer 上下文），再直接导航到日 K JSONP 接口读取响应文本。

    注：在页面内注入 script 标签做 JSONP 回调在行情页环境下不可靠
    （实测 onload 触发但回调不执行）；直接导航读取文档文本最稳定。
    """
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo

    exchange, market = _eastmoney_market(code)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    await page.goto(
        f"https://quote.eastmoney.com/{exchange}{code}.html",
        timeout=30000,
        wait_until="domcontentloaded",
    )
    await asyncio.sleep(1.5)
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?cb=__cyqScrape&secid={market}.{code}"
        f"&ut={_UT_TOKEN}"
        "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt={fqt}&end={today}&lmt=210"
    )
    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
    await asyncio.sleep(1.0)
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    if not text.lstrip().startswith("__cyqScrape("):
        raise ValueError(f"日 K 接口响应异常: {text[:120]!r}")
    return text


def _cache_get(key: str) -> Optional[List[Dict[str, float]]]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expire_ts, records = entry
        if time.monotonic() >= expire_ts:
            _cache.pop(key, None)
            return None
        return records


def _cache_put(key: str, records: List[Dict[str, float]]) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, records)


def supports_market(code: str) -> bool:
    """A 股个股才支持（东财筹码为 A 股专属；美股/港股/ETF/指数无数据）。"""
    normalized = normalize_stock_code(code)
    if _is_us_market(normalized) or _is_hk_market(normalized) or _is_etf_code(normalized):
        return False
    return True


async def get_chip_distribution(
    browser: Any,
    stock_code: str,
    fqt: str = "1",
) -> Optional[Dict[str, Any]]:
    """抓取并计算一只 A 股的筹码分布，返回 ChipDistribution 同构字典。

    失败路径：
    - 不支持的市场（美股/港股/ETF）-> None
    - 浏览器导航/解析/计算异常 -> 抛出异常（由调用方记录并降级）
    """
    code = normalize_stock_code(stock_code)
    if not supports_market(code):
        logger.debug("[cyq] %s 非 A 股个股，无筹码分布数据", code)
        return None

    cache_key = f"{code}:{fqt}"
    records = _cache_get(cache_key)
    if records is None:
        context = None
        page = None
        owns_context = False
        try:
            context, page, owns_context = await browser.new_page()
            text = await _fetch_kline_text(page, code, fqt)
            records = parse_kline_records(text)
            _cache_put(cache_key, records)
            logger.info("[cyq] %s 抓取成功: %s 根K线 (fqt=%s)", code, len(records), fqt)
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            if owns_context and context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    metrics = compute_cyq_metrics(records, range_bars=_DEFAULT_RANGE_BARS)
    metrics.update(
        {
            "code": code,
            "source": "patchright_em",
        }
    )
    return metrics
