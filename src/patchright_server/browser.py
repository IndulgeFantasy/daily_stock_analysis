# -*- coding: utf-8 -*-
"""
Patchright 浏览器管理（CDP 接管模式）。

浏览器由外部启动（真实 Chrome + 调试端口）：
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
        --remote-debugging-port=9228 --user-data-dir="E:\\ChromeAutomationProfile" ^
        --no-first-run --no-default-browser-check

本模块通过 CDP 接管该浏览器进程，复用其真实指纹、Cookie 与登录态；
浏览器生命周期完全独立于主进程。
"""

import asyncio
import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CDP_URL_DEFAULT = "http://127.0.0.1:9228"
_CONNECT_TIMEOUT_MS = 15_000


class PatchrightBrowser:
    """Thin async wrapper around a CDP-attached Patchright browser instance.

    页面复用池：每个引擎常驻一个标签页，导航复用，避免反复开关页面，
    降低本地浏览器负载与风控暴露面。
    """

    def __init__(self, cdp_url: Optional[str] = None):
        self._cdp_url = (cdp_url or os.getenv("PATCHRIGHT_CDP_URL") or _CDP_URL_DEFAULT).strip()
        if not self._cdp_url.startswith("http"):
            self._cdp_url = f"http://{self._cdp_url}"
        self._playwright: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()
        # 页面复用池：engine_name -> page
        self._engine_pages: Dict[str, Any] = {}
        # 每引擎互斥锁，防止同一引擎的常驻页面被并发导航
        self._engine_locks: Dict[str, asyncio.Lock] = {}
        self._engine_locks_guard = asyncio.Lock()

    @property
    def cdp_url(self) -> str:
        return self._cdp_url

    @property
    def is_running(self) -> bool:
        try:
            return self._browser is not None and self._browser.is_connected()
        except Exception:
            return False

    @property
    def engine_page_count(self) -> int:
        return len(self._engine_pages)

    async def get_browser(self) -> Any:
        """Return the CDP-attached browser, (re)connecting when needed."""
        async with self._lock:
            if not self.is_running:
                await self._start_locked()
            return self._browser

    async def _start_locked(self) -> None:
        """Connect over CDP to the externally-launched Chrome."""
        try:
            from patchright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "patchright 未安装，请运行: pip install patchright"
            ) from exc

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(
                self._cdp_url,
                timeout=_CONNECT_TIMEOUT_MS,
            )
            logger.info("Patchright 已接管 Chrome: %s", self._cdp_url)
        except Exception as exc:
            await self._shutdown_driver()
            raise RuntimeError(
                f"无法连接 Chrome CDP ({self._cdp_url})：请先以 "
                f"--remote-debugging-port 启动 Chrome，或运行 "
                f"run-patchright-server.bat。原始错误: {exc}"
            ) from exc

    def engine_lock(self, engine_name: str) -> asyncio.Lock:
        """Return the per-engine mutex (guards the reused page from concurrent navigation)."""
        if engine_name not in self._engine_locks:
            self._engine_locks[engine_name] = asyncio.Lock()
        return self._engine_locks[engine_name]

    async def get_engine_page(self, engine_name: str) -> Any:
        """Return the reusable page for an engine, creating or rebuilding as needed."""
        browser = await self.get_browser()
        async with self._engine_locks_guard:
            page = self._engine_pages.get(engine_name)
            if page is None or page.is_closed():
                context = browser.contexts[0] if browser.contexts else None
                if context is None:
                    context = await browser.new_context(locale="zh-CN")
                page = await context.new_page()
                self._engine_pages[engine_name] = page
                logger.debug("引擎 %s 页面已创建（复用池）", engine_name)
        return page

    async def drop_engine_page(self, engine_name: str) -> None:
        """Close and drop the engine's page (after a crash / failed navigation)."""
        async with self._engine_locks_guard:
            page = self._engine_pages.pop(engine_name, None)
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
                logger.info("引擎 %s 页面已失效并移除（复用池）", engine_name)

    async def new_page(self) -> Tuple[Any, Any, bool]:
        """Create a temporary page (for one-off fetches, e.g. /content).

        Returns (context, page, owns_context):
        - 复用浏览器默认 context（继承 profile 指纹/Cookie），owns_context=False，
          关闭时只关 page，不关 context（避免关闭整个 Chrome）
        - 无默认 context 时新建独立 context，owns_context=True，关闭时整体关闭
        """
        browser = await self.get_browser()
        if browser.contexts:
            context = browser.contexts[0]
            page = await context.new_page()
            return context, page, False
        context = await browser.new_context(locale="zh-CN")
        page = await context.new_page()
        return context, page, True

    async def close(self) -> None:
        """Detach from the browser and close the playwright driver (idempotent).

        仅断开 CDP 连接，不关闭外部 Chrome 进程。
        """
        async with self._lock:
            for page in list(self._engine_pages.values()):
                try:
                    await page.close()
                except Exception:
                    pass
            self._engine_pages.clear()
            await self._shutdown_driver()
            logger.info("Patchright 已断开 Chrome 连接")

    async def _shutdown_driver(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
