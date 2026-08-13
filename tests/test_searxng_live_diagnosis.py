# -*- coding: utf-8 -*-
"""
Diagnostic tests: live validation of the local SearXNG instance used by the project.

Background (from logs/stock_analysis_20260810.log):
    [SearXNG] 搜索 'A股 大盘 复盘' 成功，实例=http://localhost:8080，返回 6 条结果
    [新闻过滤] market:SearXNG:stock_news: provider=SearXNG, total=6, kept=0,
               drop_unknown=6, drop_old=0, drop_future=0

The instance is reachable and returns results, but every result is dropped
because its published date is unknown (publishedDate is null). This file
reproduces the exact request parameters used by SearXNGSearchProvider._do_search
and inspects the raw payload to confirm where the date is lost.

These tests require a running local SearXNG instance (http://localhost:8080).
"""

import os
import unittest

import requests

SEARXNG_BASE = os.environ.get("SEARXNG_BASE_URL", "http://localhost:8080")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)


def fetch(query: str, params: dict, timeout: int = 25) -> dict:
    url = SEARXNG_BASE.rstrip("/") + "/search"
    resp = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": UA})
    resp.raise_for_status()
    return resp.json()


@unittest.skipUnless(
    os.environ.get("RUN_SEARXNG_LIVE") == "1",
    "live SearXNG instance test; set RUN_SEARXNG_LIVE=1 to run",
)
class TestSearXNGInstanceLive(unittest.TestCase):
    """Live checks against the local SearXNG instance used by the project."""

    def test_instance_reachable_and_json_enabled(self) -> None:
        resp = requests.get(
            SEARXNG_BASE.rstrip("/") + "/search",
            params={"q": "test", "format": "json", "pageno": 1},
            timeout=15,
            headers={"User-Agent": UA},
        )
        self.assertEqual(resp.status_code, 200)

    def test_query_used_by_provider_returns_results(self) -> None:
        """Project's exact params (time_range=week) should return results."""
        data = fetch(
            "第一创业 002797 最新 新闻 重大 事件",
            {"q": "第一创业 002797 最新 新闻 重大 事件", "format": "json", "pageno": 1, "time_range": "week"},
        )
        self.assertGreater(len(data.get("results", [])), 0)

    def test_results_have_published_date(self) -> None:
        """Results must carry a non-null publishedDate to survive news filtering."""
        data = fetch(
            "第一创业 002797 最新 新闻 重大 事件",
            {"q": "第一创业 002797 最新 新闻 重大 事件", "format": "json", "pageno": 1, "time_range": "week"},
        )
        results = data.get("results", [])
        self.assertTrue(results)
        for item in results:
            self.assertIsNotNone(
                item.get("publishedDate"),
                f"missing publishedDate in engine={item.get('engine')} url={item.get('url')}",
            )

    def test_bing_without_time_range_has_published_date(self) -> None:
        """Isolate whether missing dates are engine-specific or param-specific."""
        data = fetch(
            "第一创业 002797 最新 新闻 重大 事件",
            {"q": "第一创业 002797 最新 新闻 重大 事件", "format": "json", "pageno": 1},
        )
        results = data.get("results", [])
        self.assertTrue(results)
        engines = {item.get("engine") for item in results}
        self.assertEqual(engines, {"bing"})
        for item in results:
            self.assertIsNotNone(
                item.get("publishedDate"),
                f"missing publishedDate in engine={item.get('engine')} url={item.get('url')}",
            )


if __name__ == "__main__":
    unittest.main()
