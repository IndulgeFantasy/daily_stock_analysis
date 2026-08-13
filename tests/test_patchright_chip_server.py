# -*- coding: utf-8 -*-
"""Contract tests for the Patchright server /chip route (no real browser)."""

import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

try:
    from fastapi.testclient import TestClient

    from src.patchright_server import cyq
    from src.patchright_server import server as patchright_server
except ImportError as exc:  # pragma: no cover - fastapi missing in odd envs
    raise unittest.SkipTest(f"fastapi not available: {exc}")

_OK_CHIP = {
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


class TestPatchrightChipServerContract(unittest.TestCase):
    def setUp(self) -> None:
        patchright_server._browser = None
        self.client = TestClient(patchright_server.app)

    def tearDown(self) -> None:
        patchright_server._browser = None

    def test_chip_empty_code(self) -> None:
        resp = self.client.post("/chip", json={"code": "  "})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"error": "empty_code"})

    def test_chip_without_browser_returns_browser_not_ready(self) -> None:
        resp = self.client.post("/chip", json={"code": "600519"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"error": "browser_not_ready"})

    @patch.object(patchright_server, "_browser", MagicMock(is_running=True))
    @patch.object(cyq, "supports_market", return_value=False)
    def test_chip_unsupported_market(self, mock_support) -> None:
        resp = self.client.post("/chip", json={"code": "AAPL"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"error": "unsupported_market"})
        mock_support.assert_called_once_with("AAPL")

    @patch.object(patchright_server, "_browser", MagicMock(is_running=True))
    @patch.object(cyq, "supports_market", return_value=True)
    @patch.object(cyq, "get_chip_distribution", new_callable=AsyncMock)
    def test_chip_success_passthrough(self, mock_cyq, mock_support) -> None:
        mock_cyq.return_value = _OK_CHIP
        resp = self.client.post("/chip", json={"code": "000858"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["code"], "000858")
        self.assertEqual(data["date"], "2026-08-13")
        self.assertAlmostEqual(data["profit_ratio"], 0.3838)
        self.assertAlmostEqual(data["concentration_90"], 0.1789)
        mock_cyq.assert_awaited_once()
        self.assertEqual(mock_cyq.call_args.args[1], "000858")
        self.assertEqual(mock_cyq.call_args.kwargs.get("fqt"), "1")

    @patch.object(patchright_server, "_browser", MagicMock(is_running=True))
    @patch.object(cyq, "supports_market", return_value=True)
    @patch.object(cyq, "get_chip_distribution", new_callable=AsyncMock)
    def test_chip_invalid_fqt_defaults_to_1(self, mock_cyq, mock_support) -> None:
        mock_cyq.return_value = _OK_CHIP
        resp = self.client.post("/chip", json={"code": "600519", "fqt": "9"})
        self.assertEqual(resp.status_code, 200)
        mock_cyq.assert_awaited_once()
        self.assertEqual(mock_cyq.call_args.kwargs.get("fqt"), "1")

    @patch.object(patchright_server, "_browser", MagicMock(is_running=True))
    @patch.object(cyq, "supports_market", return_value=True)
    @patch.object(cyq, "get_chip_distribution", new_callable=AsyncMock)
    def test_chip_exception_returns_error_payload(self, mock_cyq, mock_support) -> None:
        mock_cyq.side_effect = RuntimeError("parse failed")
        resp = self.client.post("/chip", json={"code": "600519"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["error"], "parse failed")

    @patch.object(patchright_server, "_browser", MagicMock(is_running=True))
    @patch.object(cyq, "supports_market", return_value=True)
    @patch.object(cyq, "get_chip_distribution", new_callable=AsyncMock)
    def test_chip_none_result_returns_no_data(self, mock_cyq, mock_support) -> None:
        mock_cyq.return_value = None
        resp = self.client.post("/chip", json={"code": "600519"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"error": "no_data"})


class TestCyqParseAndComputeOffline(unittest.TestCase):
    """cyq.py 解析与算法离线校验（含与 akshare JS 引擎的双模式对齐）。"""

    def test_parse_kline_records(self) -> None:
        body = '__cyqScrape({"data":{"klines":["2026-08-13,11.23,11.25,11.27,11.18,755981,848358766.49,0.80,0.00,0.00,0.39"]}});'
        records = cyq.parse_kline_records(body)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["date"], "2026-08-13")
        self.assertAlmostEqual(records[0]["close"], 11.25)
        self.assertAlmostEqual(records[0]["hsl"], 0.39)

    def test_parse_kline_records_invalid(self) -> None:
        with self.assertRaises(ValueError):
            cyq.parse_kline_records("<html>oops</html>")

    def test_compute_cyq_metrics_fixture(self) -> None:
        records = [
            {"date": "2026-08-07", "open": 10.0, "close": 10.5, "high": 10.8, "low": 9.9, "volume": 100000, "amount": 1e8, "zf": 2.0, "zdf": 5.0, "zde": 0.5, "hsl": 1.2},
            {"date": "2026-08-10", "open": 10.5, "close": 10.2, "high": 10.7, "low": 10.1, "volume": 80000, "amount": 8e7, "zf": 1.0, "zdf": -3.0, "zde": -0.3, "hsl": 0.9},
            {"date": "2026-08-11", "open": 10.2, "close": 10.6, "high": 10.9, "low": 10.0, "volume": 90000, "amount": 9e7, "zf": 1.5, "zdf": 4.0, "zde": 0.4, "hsl": 1.0},
            {"date": "2026-08-12", "open": 10.6, "close": 10.9, "high": 11.0, "low": 10.4, "volume": 110000, "amount": 1.1e8, "zf": 1.0, "zdf": 2.8, "zde": 0.3, "hsl": 1.1},
            {"date": "2026-08-13", "open": 10.9, "close": 10.7, "high": 11.1, "low": 10.6, "volume": 95000, "amount": 1e8, "zf": 0.8, "zdf": -1.8, "zde": -0.2, "hsl": 1.0},
        ]
        metrics = cyq.compute_cyq_metrics(records, range_bars=120)
        self.assertEqual(metrics["date"], "2026-08-13")
        self.assertAlmostEqual(metrics["profit_ratio"], 0.6765993361265807, places=10)
        self.assertEqual(metrics["avg_cost"], 10.54)
        self.assertEqual(metrics["cost_90_low"], 10.14)
        self.assertEqual(metrics["cost_90_high"], 10.93)
        self.assertAlmostEqual(metrics["concentration_90"], 0.03749406739439958, places=10)
        self.assertEqual(metrics["cost_70_low"], 10.27)
        self.assertEqual(metrics["cost_70_high"], 10.83)
        self.assertAlmostEqual(metrics["concentration_70"], 0.026540284360189594, places=10)

    def test_eastmoney_market(self) -> None:
        self.assertEqual(cyq._eastmoney_market("600519"), ("sh", "1"))
        self.assertEqual(cyq._eastmoney_market("688981"), ("sh", "1"))
        self.assertEqual(cyq._eastmoney_market("000001"), ("sz", "0"))
        self.assertEqual(cyq._eastmoney_market("300750"), ("sz", "0"))
        self.assertEqual(cyq._eastmoney_market("920748"), ("sz", "0"))

    def test_supports_market(self) -> None:
        self.assertTrue(cyq.supports_market("600519"))
        self.assertTrue(cyq.supports_market("000858"))
        self.assertFalse(cyq.supports_market("AAPL"))
        self.assertFalse(cyq.supports_market("HK00700"))
        self.assertFalse(cyq.supports_market("510300"))


if __name__ == "__main__":
    unittest.main()
