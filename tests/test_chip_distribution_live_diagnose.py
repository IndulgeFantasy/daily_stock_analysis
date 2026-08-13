# -*- coding: utf-8 -*-
"""Live-network diagnostics for the 筹码分布 (chip distribution) pipeline.

Gated by @pytest.mark.network: runs only in the non-blocking "Network Smoke" cron
(`pytest -m network`), never in the blocking backend gate (`pytest -m "not network"`).

This file is a diagnosis aid for "筹码分布无法正常使用". It probes, in dependency
order, the two chip data paths used by DataFetcherManager.get_chip_distribution:

1. Akshare path: `ak.stock_cyq_em` -> eastmoney `push2his.eastmoney.com` historical
   kline endpoint (chips are computed locally from the kline feed).
2. Tushare path: `ts.pro_api().cyq_chips` + `trade_cal` (account permission required).

Each test fails LOUD with the concrete upstream error so the blocking layer can be
identified. Run locally with:

    python -m pytest tests/test_chip_distribution_live_diagnose.py -m network -v
"""

import os
import sys
import unittest

import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.akshare_fetcher import AkshareFetcher  # noqa: E402
from data_provider.base import DataFetcherManager  # noqa: E402
from data_provider.tushare_fetcher import TushareFetcher  # noqa: E402

_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_KLINE_PARAMS = {
    "secid": "1.600519",
    "fields1": "f1,f2,f3,f4,f5,f6",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    "klt": "101",
    "fqt": "0",
    "end": "20260813",
    "lmt": "5",
}


@pytest.mark.network
class TestChipDistributionLiveDiagnose(unittest.TestCase):
    """Probe each chip data path and fail LOUD with the upstream error."""

    def test_01_akshare_chip_source_endpoint_reachable(self):
        """Akshare computes chips from eastmoney push2his kline feed (no timeout in
        ak.stock_cyq_em, so probe with an explicit timeout)."""
        try:
            resp = requests.get(_KLINE_URL, params=_KLINE_PARAMS, timeout=15)
        except requests.exceptions.RequestException as exc:
            self.fail(
                "A股筹码数据源 push2his.eastmoney.com 不可达: %r。"
                "ak.stock_cyq_em 依赖该接口（且自身 requests.get 无 timeout，"
                "服务器挂起时会永久阻塞）。若实时行情( push2.eastmoney.com )正常而此处失败，"
                "说明当前网络/IP 被东财历史数据接口风控。可考虑代理或改用 Tushare 积分升级方案。"
                % (exc,)
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            self.fail(f"push2his 返回非 JSON（维护页/接口迁移？）: {exc}")
        data = payload.get("data") or {}
        klines = data.get("klines") or []
        self.assertGreater(
            len(klines),
            0,
            f"push2his 返回空 klines: {payload}",
        )

    def test_02_akshare_stock_cyq_em_live(self):
        """Full AkshareFetcher.get_chip_distribution call (may hang if the endpoint
        stalls; rely on the pytest timeout of the caller)."""
        fetcher = AkshareFetcher()
        chip = fetcher.get_chip_distribution("600519")
        self.assertIsNotNone(
            chip,
            "AkshareFetcher.get_chip_distribution('600519') 返回 None。"
            "日志中的 [API错误] 即为上游失败原因。",
        )

    def test_03_tushare_cyq_chips_permission(self):
        """Tushare cyq_chips requires 5000-point account tier; fail LOUD on
        permission/rate-limit errors so the account limitation is visible."""
        import re

        token = (os.getenv("TUSHARE_TOKEN") or "").strip()
        if not token:
            self.skipTest("TUSHARE_TOKEN 未配置")
        fetcher = TushareFetcher()
        try:
            df = fetcher._call_api_with_rate_limit(
                "cyq_chips",
                ts_code="600519.SH",
                start_date="20260812",
                end_date="20260812",
            )
            if df is None or df.empty:
                self.fail("cyq_chips 返回空数据")
        except Exception as exc:
            msg = str(exc)
            if "没有接口" in msg or "权限" in msg:
                self.fail(
                    "Tushare 账号无 cyq_chips 接口权限（需 5000 积分），"
                    f"get_chip_distribution 必然返回 None。接口报错: {msg}"
                )
            if "频率超限" in msg or "trade_cal" in msg:
                self.fail(
                    "Tushare trade_cal 频率超限（1 次/小时），get_trade_time 返回 None，"
                    f"筹码获取提前退出。接口报错: {msg}"
                )
            self.fail(f"cyq_chips 调用失败: {msg}")

    def test_04_manager_end_to_end(self):
        """End-to-end through the manager used by the analysis pipeline."""
        manager = DataFetcherManager()
        chip = manager.get_chip_distribution("600519")
        self.assertIsNotNone(
            chip,
            "DataFetcherManager.get_chip_distribution('600519') 返回 None（所有数据源均失败）。"
            "结合 test_01/test_03 判定是网络风控还是 Tushare 账号权限问题。",
        )


if __name__ == "__main__":
    unittest.main()
