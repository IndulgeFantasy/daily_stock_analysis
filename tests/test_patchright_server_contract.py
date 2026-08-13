# -*- coding: utf-8 -*-
"""Contract tests for the Patchright standalone server routes (no real browser)."""

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

    from src.patchright_server import server as patchright_server
except ImportError as exc:  # pragma: no cover - fastapi missing in odd envs
    raise unittest.SkipTest(f"fastapi not available: {exc}")


class TestPatchrightServerContract(unittest.TestCase):
    def setUp(self) -> None:
        # 隔离测试进程内模块级浏览器状态
        patchright_server._browser = None
        self.client = TestClient(patchright_server.app)

    def tearDown(self) -> None:
        patchright_server._browser = None

    def test_healthz_before_browser_start(self) -> None:
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["service"], "patchright")
        self.assertFalse(data["browser_running"])
        self.assertIn("baidu", data["engines"])
        self.assertIn("quark", data["engines"])
        self.assertIn("360", data["engines"])

    def test_search_empty_query(self) -> None:
        resp = self.client.post("/search", json={"query": "  ", "max_results": 5, "days": 3})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["results"], [])

    @patch.object(patchright_server, "_browser", None)
    def test_search_without_browser_returns_success_empty(self) -> None:
        # _search_one_engine 对 _browser is None 返回降级空结果，聚合后仍 success=True
        resp = self.client.post(
            "/search",
            json={"query": "第一创业 002797 最新 新闻", "max_results": 5, "days": 3},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["provider"], "Patchright")
        self.assertEqual(data["results"], [])
        self.assertGreaterEqual(data["search_time"], 0)

    @patch.object(patchright_server, "_browser", None)
    def test_content_without_browser_returns_empty_text(self) -> None:
        resp = self.client.post(
            "/content",
            json={"url": "https://example.com/article", "timeout": 5},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data, {"text": "", "truncated": False})

    def test_content_rejects_invalid_url(self) -> None:
        resp = self.client.post("/content", json={"url": "javascript:alert(1)", "timeout": 5})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"text": "", "truncated": False})

    def test_search_response_schema_roundtrip(self) -> None:
        """校验 SearchResponse <-> dict 序列化往返与主进程字段契约一致。"""
        from src.search_service import SearchResponse, SearchResult

        response = SearchResponse(
            query="q",
            results=[
                SearchResult(
                    title="t",
                    snippet="s",
                    url="https://example.com/1",
                    source="example.com",
                    published_date="2026-08-10",
                )
            ],
            provider="Patchright",
            success=True,
            search_time=0.5,
        )
        data = patchright_server._search_response_to_dict(response)
        self.assertEqual(data["query"], "q")
        self.assertEqual(data["results"][0]["published_date"], "2026-08-10")
        self.assertEqual(data["provider"], "Patchright")
        self.assertEqual(data["search_time"], 0.5)

    def test_dedupe_results(self) -> None:
        items = [
            {"url": "https://a.com/1", "title": "a"},
            {"url": "https://a.com/1", "title": "a-dup"},
            {"url": "https://b.com/2", "title": "b"},
            {"url": "", "title": "no-url"},
        ]
        deduped = patchright_server._dedupe_results(items, max_results=10)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["title"], "a")
        self.assertEqual(deduped[1]["title"], "b")

    def test_dedupe_respects_max_results(self) -> None:
        items = [
            {"url": f"https://a.com/{i}", "title": str(i)} for i in range(5)
        ]
        deduped = patchright_server._dedupe_results(items, max_results=2)
        self.assertEqual(len(deduped), 2)

    def test_dedupe_prefers_dated_results(self) -> None:
        items = [
            {"url": "https://a.com/1", "title": "no-date", "published_date": None},
            {"url": "https://b.com/2", "title": "dated", "published_date": "2026-08-10"},
            {"url": "https://c.com/3", "title": "dated2", "published_date": "2026-08-11"},
        ]
        deduped = patchright_server._dedupe_results(items, max_results=2)
        self.assertEqual(len(deduped), 2)
        # 有日期的结果优先于无日期结果；同日期状态保持原始相对顺序（稳定排序）
        self.assertEqual(deduped[0]["title"], "dated")
        self.assertEqual(deduped[1]["title"], "dated2")

    def test_dedupe_all_no_date_keeps_order(self) -> None:
        items = [
            {"url": "https://a.com/1", "title": "a", "published_date": None},
            {"url": "https://b.com/2", "title": "b", "published_date": None},
            {"url": "https://c.com/3", "title": "c", "published_date": None},
        ]
        deduped = patchright_server._dedupe_results(items, max_results=3)
        self.assertEqual([r["title"] for r in deduped], ["a", "b", "c"])


class TestPatchrightBrowserCDP(unittest.IsolatedAsyncioTestCase):
    """CDP 接管模式的浏览器管理单测（mock patchright driver）。"""

    async def test_new_page_reuses_default_context(self) -> None:
        from src.patchright_server.browser import PatchrightBrowser

        browser = PatchrightBrowser(cdp_url="http://127.0.0.1:9228")
        driver = MagicMock()
        driver.chromium.connect_over_cdp = AsyncMock(return_value=MagicMock())
        browser._playwright = driver
        connected = MagicMock()
        connected.is_connected.return_value = True
        context = MagicMock()
        page = MagicMock()
        connected.contexts = [context]
        context.new_page = AsyncMock(return_value=page)
        browser._browser = connected

        ctx, pg, owns = await browser.new_page()
        self.assertIs(ctx, context)
        self.assertIs(pg, page)
        self.assertFalse(owns)  # 默认 context 不归搜索调用方所有

    async def test_new_page_creates_context_when_no_default(self) -> None:
        from src.patchright_server.browser import PatchrightBrowser

        browser = PatchrightBrowser(cdp_url="http://127.0.0.1:9228")
        driver = MagicMock()
        driver.chromium.connect_over_cdp = AsyncMock(return_value=MagicMock())
        browser._playwright = driver
        connected = MagicMock()
        connected.is_connected.return_value = True
        connected.contexts = []
        new_context = MagicMock()
        page = MagicMock()
        new_context.new_page = AsyncMock(return_value=page)
        connected.new_context = AsyncMock(return_value=new_context)
        browser._browser = connected

        ctx, pg, owns = await browser.new_page()
        self.assertIs(ctx, new_context)
        self.assertIs(pg, page)
        self.assertTrue(owns)  # 自建 context 归调用方所有，可整体关闭

    async def test_connect_failure_raises_clear_error(self) -> None:
        from src.patchright_server.browser import PatchrightBrowser

        browser = PatchrightBrowser(cdp_url="http://127.0.0.1:9228")
        driver = MagicMock()
        driver.chromium.connect_over_cdp = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )
        driver.stop = AsyncMock()
        browser._playwright = driver

        with self.assertRaisesRegex(RuntimeError, "无法连接 Chrome CDP"):
            await browser.get_browser()

    async def test_engine_page_reuse_and_rebuild(self) -> None:
        """引擎页面复用：第二次获取同一引擎返回同一 page；崩溃后重建。"""
        from src.patchright_server.browser import PatchrightBrowser

        browser = PatchrightBrowser(cdp_url="http://127.0.0.1:9228")
        driver = MagicMock()
        driver.chromium.connect_over_cdp = AsyncMock(return_value=MagicMock())
        browser._playwright = driver
        connected = MagicMock()
        connected.is_connected.return_value = True
        context = MagicMock()
        page1 = MagicMock()
        page1.is_closed.return_value = False
        page2 = MagicMock()
        page2.is_closed.return_value = False
        page3 = MagicMock()
        page3.is_closed.return_value = False
        context.new_page = AsyncMock(side_effect=[page1, page2, page3])
        connected.contexts = [context]
        browser._browser = connected

        got1 = await browser.get_engine_page("baidu")
        got2 = await browser.get_engine_page("baidu")
        self.assertIs(got1, got2)  # 复用同一页面
        self.assertEqual(context.new_page.await_count, 1)

        # 页面崩溃后重建
        page1.is_closed.return_value = True
        got3 = await browser.get_engine_page("baidu")
        self.assertIs(got3, page2)
        self.assertEqual(context.new_page.await_count, 2)

        # drop 后移出池，下次重建
        await browser.drop_engine_page("baidu")
        got4 = await browser.get_engine_page("baidu")
        self.assertIs(got4, page3)
        self.assertEqual(context.new_page.await_count, 3)


class TestEngineConcurrency(unittest.IsolatedAsyncioTestCase):
    async def test_search_one_engine_without_browser_degrades(self) -> None:
        patchright_server._browser = None
        outcome = await patchright_server._search_one_engine(
            "baidu", "第一创业 新闻", max_results=5, timeout_ms=15000
        )
        self.assertEqual(outcome["engine"], "baidu")
        self.assertEqual(outcome["results"], [])
        self.assertIsNotNone(outcome["error"])

    async def test_search_one_engine_browser_exception_degrades(self) -> None:
        browser = MagicMock()
        browser.engine_lock.return_value = asyncio.Lock()
        browser.get_engine_page = AsyncMock(side_effect=RuntimeError("browser gone"))
        browser.drop_engine_page = AsyncMock()
        patchright_server._browser = browser
        outcome = await patchright_server._search_one_engine(
            "quark", "测试", max_results=5, timeout_ms=15000
        )
        self.assertEqual(outcome["engine"], "quark")
        self.assertEqual(outcome["results"], [])
        self.assertIn("browser gone", outcome["error"])
        browser.drop_engine_page.assert_awaited_once_with("quark")

    async def test_search_one_engine_reuses_page(self) -> None:
        """常驻页面被复用：成功后不关闭页面，也不重建。"""
        browser = MagicMock()
        browser.engine_lock.return_value = asyncio.Lock()
        page = MagicMock()
        page.goto = AsyncMock()
        page.content = AsyncMock(return_value="<html>ok</html>")
        page.is_closed.return_value = False
        browser.get_engine_page = AsyncMock(return_value=page)
        browser.drop_engine_page = AsyncMock()
        patchright_server._browser = browser
        outcome = await patchright_server._search_one_engine(
            "baidu", "伊利股份 新闻", max_results=5, timeout_ms=15000
        )
        self.assertEqual(outcome["engine"], "baidu")
        self.assertEqual(outcome["error"], None)
        browser.get_engine_page.assert_awaited_once_with("baidu")
        browser.drop_engine_page.assert_not_awaited()

    async def test_search_one_engine_blocked_drops_page(self) -> None:
        """风控命中时页面移除出池（下次重建），避免污染后续请求。"""
        browser = MagicMock()
        browser.engine_lock.return_value = asyncio.Lock()
        page = MagicMock()
        page.goto = AsyncMock()
        page.content = AsyncMock(return_value="<html>captcha punish</html>")
        browser.get_engine_page = AsyncMock(return_value=page)
        browser.drop_engine_page = AsyncMock()
        patchright_server._browser = browser
        outcome = await patchright_server._search_one_engine(
            "quark", "测试", max_results=5, timeout_ms=15000
        )
        self.assertEqual(outcome["error"], "blocked")
        browser.drop_engine_page.assert_awaited_once_with("quark")


if __name__ == "__main__":
    unittest.main()
