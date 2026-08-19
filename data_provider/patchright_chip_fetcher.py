# -*- coding: utf-8 -*-
"""Patchright 筹码分布数据源（通过本地 patchright 服务抓取东财页面口径数据）。

定位：Akshare/Tushare 筹码接口不可用时的兜底数据源。
- 数据口径：东财行情页 #fullScreenChart 同源（fqt=1 前复权、最近 120 个交易日
  CYQCalculator 算法），数值与页面显示一致。
- 依赖：本机运行 run-patchright-server.bat（真实 Chrome + patchright 服务）。
- 降级语义：服务不可达 -> 返回 None（不熔断，服务恢复即生效）；
  服务端业务错误 -> 抛 DataFetchError（走现有熔断）。
"""

import logging
from typing import Any, Optional

import pandas as pd
import requests

from data_provider.base import (
    BaseFetcher,
    DataFetchError,
    _is_etf_code,
    _is_hk_market,
    _is_us_market,
    normalize_stock_code,
)
from data_provider.realtime_types import ChipDistribution

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:8931"
# 服务端 /chip 冷处理实测 4~18s（导航 K 线接口 + 页面加载，与搜索共享浏览器），
# 超时阈值需覆盖最慢场景，避免分析主流程侧误判失败
_DEFAULT_TIMEOUT_SECONDS = 30.0


class PatchrightChipFetcher(BaseFetcher):
    """通过本地 patchright 服务抓取 A 股筹码分布（东财页面口径）。"""

    name: str = "PatchrightChipFetcher"
    priority: float = 1.5  # akshare(1) 之后、pytdx(2) 之前，作为筹码兜底

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ):
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    # ---- BaseFetcher 抽象方法占位（本数据源仅提供筹码接口，不提供日线）----
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df

    # ---- 筹码分布 ----
    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """
        获取 A 股筹码分布（带市场过滤，失败降级）。

        返回 ChipDistribution 对象，失败返回 None；服务端业务错误抛 DataFetchError。
        """
        code = normalize_stock_code(stock_code)

        # 美股/港股/ETF/指数无筹码分布数据（东财 CYQ 为 A 股专属）
        if _is_us_market(code) or _is_hk_market(code) or _is_etf_code(code):
            logger.debug("[PatchrightChip] %s 非 A 股个股，无筹码分布数据", code)
            return None

        try:
            resp = requests.post(
                f"{self.base_url}/chip",
                json={"code": code},
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            # 服务不可达：优雅降级（不熔断），主链路继续尝试其他数据源
            logger.warning(
                "[PatchrightChip] patchright 服务不可达 %s: %s",
                self.base_url,
                exc,
            )
            return None

        if resp.status_code != 200:
            logger.warning(
                "[PatchrightChip] %s 服务返回 HTTP %s", code, resp.status_code
            )
            return None

        try:
            payload = resp.json()
        except ValueError as exc:
            logger.warning("[PatchrightChip] %s 响应非 JSON: %s", code, exc)
            return None

        error = payload.get("error")
        if error:
            # 业务错误（接口解析失败/风控等）：抛异常走现有熔断降级
            raise DataFetchError(f"Patchright /chip 失败 {code}: {error}")

        chip = ChipDistribution(
            code=code,
            date=str(payload.get("date") or ""),
            source=str(payload.get("source") or "patchright_em"),
            profit_ratio=float(payload.get("profit_ratio") or 0.0),
            avg_cost=float(payload.get("avg_cost") or 0.0),
            cost_90_low=float(payload.get("cost_90_low") or 0.0),
            cost_90_high=float(payload.get("cost_90_high") or 0.0),
            concentration_90=float(payload.get("concentration_90") or 0.0),
            cost_70_low=float(payload.get("cost_70_low") or 0.0),
            cost_70_high=float(payload.get("cost_70_high") or 0.0),
            concentration_70=float(payload.get("concentration_70") or 0.0),
        )
        logger.info(
            "[PatchrightChip] %s 日期=%s: 获利比例=%.1f%%, 平均成本=%s",
            code,
            chip.date,
            chip.profit_ratio * 100,
            chip.avg_cost,
        )
        return chip
