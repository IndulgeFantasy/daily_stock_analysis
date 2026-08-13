# -*- coding: utf-8 -*-
"""Tests for PatchrightChipFetcher (offline: mocked HTTP; live: real patchright service)."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

from data_provider.patchright_chip_fetcher import PatchrightChipFetcher  # noqa: E402
from data_provider.realtime_types import ChipDistribution  # noqa: E402


def _ok_payload() -> dict:
    return {
        "code": "000858",
        "date": "2026-08-13",
        "source": "patchright_em",
        "profit_ratio": 0.3838,
        "avg_cost": 76.24,
        "cost_90_low": 70.28,
        "cost_90_high": 100.9,
        "concentration_90": 0.1789,
        "cost_70_low": 71.57,
        "cost_70_high": 91.04,
        "concentration_70": 0.1197,
    }


class TestPatchrightChipFetcherOffline(unittest.TestCase):
    def _fetcher(self, base_url="http://127.0.0.1:8931") -> PatchrightChipFetcher:
        return PatchrightChipFetcher(base_url)

    @patch("data_provider.patchright_chip_fetcher.requests.post")
    def test_success_maps_to_chip_distribution(self, mock_post: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _ok_payload()
        mock_post.return_value = resp

        chip = self._fetcher().get_chip_distribution("000858")

        self.assertIsInstance(chip, ChipDistribution)
        self.assertEqual(chip.code, "000858")
        self.assertEqual(chip.date, "2026-08-13")
        self.assertEqual(chip.source, "patchright_em")
        self.assertAlmostEqual(chip.profit_ratio, 0.3838)
        self.assertAlmostEqual(chip.avg_cost, 76.24)
        self.assertAlmostEqual(chip.cost_90_low, 70.28)
        self.assertAlmostEqual(chip.cost_90_high, 100.9)
        self.assertAlmostEqual(chip.concentration_90, 0.1789)
        self.assertAlmostEqual(chip.cost_70_low, 71.57)
        self.assertAlmostEqual(chip.cost_70_high, 91.04)
        self.assertAlmostEqual(chip.concentration_70, 0.1197)
        mock_post.assert_called_once()
        call = mock_post.call_args
        self.assertEqual(call.args[0], "http://127.0.0.1:8931/chip")
        self.assertEqual(call.kwargs["json"]["code"], "000858")

    def test_non_ashare_markets_skip_without_http(self) -> None:
        """美股/港股/ETF 直接返回 None，不发 HTTP 请求。"""
        with patch(
            "data_provider.patchright_chip_fetcher.requests.post"
        ) as mock_post:
            for code in ("AAPL", "HK00700", "510300", "159919"):
                chip = self._fetcher().get_chip_distribution(code)
                self.assertIsNone(chip, code)
            mock_post.assert_not_called()

    def test_normalizes_sz_prefix(self) -> None:
        with patch(
            "data_provider.patchright_chip_fetcher.requests.post"
        ) as mock_post:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _ok_payload()
            mock_post.return_value = resp
            chip = self._fetcher().get_chip_distribution("SZ000858")
            self.assertIsNotNone(chip)
            self.assertEqual(chip.code, "000858")

    @patch("data_provider.patchright_chip_fetcher.requests.post")
    def test_http_error_returns_none(self, mock_post: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 503
        mock_post.return_value = resp
        self.assertIsNone(self._fetcher().get_chip_distribution("000858"))

    @patch("data_provider.patchright_chip_fetcher.requests.post")
    def test_connection_error_returns_none_without_breaker(self, mock_post: MagicMock) -> None:
        """服务不可达返回 None（不抛异常，避免熔断后服务恢复的空窗）。"""
        import requests

        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        self.assertIsNone(self._fetcher().get_chip_distribution("000858"))

    @patch("data_provider.patchright_chip_fetcher.requests.post")
    def test_timeout_returns_none(self, mock_post: MagicMock) -> None:
        import requests

        mock_post.side_effect = requests.exceptions.Timeout()
        self.assertIsNone(self._fetcher().get_chip_distribution("000858"))

    @patch("data_provider.patchright_chip_fetcher.requests.post")
    def test_malformed_json_returns_none(self, mock_post: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("bad json")
        mock_post.return_value = resp
        self.assertIsNone(self._fetcher().get_chip_distribution("000858"))

    @patch("data_provider.patchright_chip_fetcher.requests.post")
    def test_business_error_raises_data_fetch_error(self, mock_post: MagicMock) -> None:
        """服务端业务错误（如接口被风控）抛 DataFetchError，走现有熔断降级。"""
        from data_provider.base import DataFetchError

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"error": "no_data"}
        mock_post.return_value = resp
        with self.assertRaises(DataFetchError):
            self._fetcher().get_chip_distribution("000858")


@pytest.mark.network
class TestPatchrightChipFetcherLive(unittest.TestCase):
    """在线验证：真实 patchright 服务（需运行 run-patchright-server.bat）。"""

    def setUp(self) -> None:
        import requests

        base_url = os.getenv("PATCHRIGHT_BASE_URL", "http://127.0.0.1:8931").rstrip("/")
        try:
            resp = requests.get(f"{base_url}/healthz", timeout=3)
            if resp.status_code != 200 or not resp.json().get("browser_running"):
                self.skipTest("patchright 服务未运行或浏览器未就绪")
        except requests.exceptions.RequestException:
            self.skipTest("patchright 服务未运行")
        self.fetcher = PatchrightChipFetcher(base_url=base_url)

    def test_live_600519_returns_meaningful_chip(self) -> None:
        chip = self.fetcher.get_chip_distribution("600519")
        self.assertIsNotNone(chip)
        self.assertGreater(chip.avg_cost, 0)
        self.assertGreaterEqual(chip.profit_ratio, 0.0)
        self.assertLessEqual(chip.profit_ratio, 1.0)
        self.assertLessEqual(chip.cost_70_low, chip.cost_70_high)
        self.assertLessEqual(chip.cost_90_low, chip.cost_90_high)
        self.assertGreaterEqual(chip.concentration_90, 0.0)
        self.assertGreaterEqual(len(chip.date), 8, f"日期异常: {chip.date}")

    def test_live_000858_matches_page_reference(self) -> None:
        """与 #fullScreenChart 页面显示对照（2026-08-13 用户核对值：38.38%/76.24/90%[70.28,100.90] 17.89%）。"""
        chip = self.fetcher.get_chip_distribution("000858")
        self.assertIsNotNone(chip)
        # 盘中数据允许小幅浮动，区间放宽用于口径回归校验
        self.assertGreater(chip.profit_ratio, 0.25, f"获利比例异常: {chip.profit_ratio:.2%}")
        self.assertLess(chip.profit_ratio, 0.55, f"获利比例异常: {chip.profit_ratio:.2%}")
        self.assertGreater(chip.avg_cost, 60, f"平均成本异常: {chip.avg_cost}")
        self.assertLess(chip.avg_cost, 95, f"平均成本异常: {chip.avg_cost}")
        print(
            f"[核对] 000858: 获利比例={chip.profit_ratio:.2%} 平均成本={chip.avg_cost} "
            f"90%=[{chip.cost_90_low},{chip.cost_90_high}] {chip.concentration_90:.2%} "
            f"70%=[{chip.cost_70_low},{chip.cost_70_high}] {chip.concentration_70:.2%}"
        )


if __name__ == "__main__":
    unittest.main()
