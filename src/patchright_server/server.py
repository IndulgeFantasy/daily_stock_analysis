# -*- coding: utf-8 -*-
"""
Patchright 独立搜索服务（FastAPI）。

手动启动（默认 127.0.0.1:8931）：
    python -m src.patchright_server.server

接口：
- GET  /healthz              浏览器与引擎状态
- POST /search               {query, max_results, days} -> SearchResponse JSON
- POST /content              {url, timeout} -> {text, truncated}（预留，主流程不调用）
"""

import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from src.logging_config import setup_logging
from src.search_service import SearchResponse, SearchResult

from .browser import PatchrightBrowser
from .engines import ENGINES, is_blocked

logger = logging.getLogger(__name__)

HOST = os.getenv("PATCHRIGHT_SERVER_HOST", "127.0.0.1")
PORT = int(os.getenv("PATCHRIGHT_SERVER_PORT", "8931"))
DEFAULT_TIMEOUT_MS = 15_000
MAX_RESULTS_HARD_LIMIT = 20
# 全局页面并发信号量：限制同时执行的页面操作数（标签页数），
# 防止并发分析 × 多引擎造成页面堆积；超出部分排队等待。
MAX_CONCURRENT_PAGES = max(
    1,
    int(os.getenv("PATCHRIGHT_MAX_CONCURRENT_PAGES", "3")),
)

_browser: Optional[PatchrightBrowser] = None
_page_semaphore: Optional[asyncio.Semaphore] = None


def _get_page_semaphore() -> asyncio.Semaphore:
    """Return a semaphore bound to the current running loop.

    asyncio.Semaphore binds to the first loop that uses it; the FastAPI
    lifespan loop and test loops differ, so rebuild lazily per loop.
    """
    global _page_semaphore
    loop = asyncio.get_running_loop()
    if _page_semaphore is None or _page_semaphore._loop is not loop:
        _page_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
    return _page_semaphore


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _browser
    _browser = PatchrightBrowser()
    yield
    await _browser.close()
    _browser = None


app = FastAPI(
    title="Patchright Search Service",
    version="0.1.0",
    description="本地浏览器搜索服务（百度/夸克/360），供 daily_stock_analysis 主进程调用。",
    lifespan=_lifespan,
)


class SearchRequest(BaseModel):
    query: str
    max_results: int = 5
    days: int = 7


class ContentRequest(BaseModel):
    url: str
    timeout: float = 5.0


def _search_result_to_dict(result: SearchResult) -> Dict[str, Any]:
    return {
        "title": result.title,
        "snippet": result.snippet,
        "url": result.url,
        "source": result.source,
        "published_date": result.published_date,
        "relevance_score": result.relevance_score,
        "relevance_category": result.relevance_category,
        "relevance_reasons": result.relevance_reasons,
    }


def _search_response_to_dict(response: SearchResponse) -> Dict[str, Any]:
    return {
        "query": response.query,
        "results": [_search_result_to_dict(r) for r in response.results],
        "provider": response.provider,
        "success": response.success,
        "error_message": response.error_message,
        "search_time": response.search_time,
    }


def _dedupe_results(items: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    """去重并按发布时间优先排序（有日期的结果优先保留，避免被无日期结果挤掉）。"""
    # 有 published_date 的排前面，保持引擎内相对顺序稳定
    ordered = sorted(
        items,
        key=lambda item: 0 if (item.get("published_date") or "").strip() else 1,
    )
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for item in ordered:
        url = (item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(item)
        if len(deduped) >= max_results:
            break
    return deduped


async def _search_one_engine(
    engine_name: str,
    query: str,
    max_results: int,
    timeout_ms: int,
) -> Dict[str, Any]:
    """Search one engine using its reused page; failures degrade to empty.

    - 全局信号量限制同时执行的页面操作数（MAX_CONCURRENT_PAGES）
    - 每引擎互斥锁防止常驻页面被并发导航
    - 页面复用池：页面常驻不关闭，崩溃/导航失败时移除并下次重建
    - 抓取前等待结果稳定（百度 AI 总结为流式输出，需等其渲染完成）
    """
    engine = ENGINES[engine_name]
    if _browser is None:
        return {"engine": engine_name, "results": [], "error": "browser not initialized"}
    semaphore = _get_page_semaphore()
    async with semaphore:
        async with _browser.engine_lock(engine_name):
            page = None
            try:
                page = await _browser.get_engine_page(engine_name)
                await page.goto(
                    engine["build_url"](query),
                    timeout=timeout_ms,
                    wait_until="domcontentloaded",
                )
                await _wait_results_stable(page, engine_name, timeout_ms)
                html = await page.content()
                if is_blocked(html):
                    logger.warning("[%s] 结果页疑似风控，引擎降级为空", engine_name)
                    # 页面可能被风控污染（验证码/重定向），移出复用池以便下次重建
                    await _browser.drop_engine_page(engine_name)
                    return {"engine": engine_name, "results": [], "error": "blocked"}
                parsed = engine["parse"](html, max_results=max_results)
                return {"engine": engine_name, "results": parsed, "error": None}
            except Exception as exc:
                logger.warning("[%s] 搜索失败: %s", engine_name, exc)
                # 页面可能已崩溃/导航失败，移出复用池以便下次重建
                await _browser.drop_engine_page(engine_name)
                return {"engine": engine_name, "results": [], "error": str(exc)}


async def _wait_results_stable(page, engine_name: str, timeout_ms: int) -> None:
    """等待搜索结果渲染稳定：结果容器数量连续两次采样不变。

    百度 AI 总结为流式输出（一字一字渲染），domcontentloaded 后仍可能
    增长数秒；通过采样 div.result / cosc-card 数量判断渲染完成，
    避免抓到半截 AI 总结。超时按稳定处理（走现有降级/正常路径）。
    """
    if engine_name == "baidu":
        selector = "div.result, div.c-container, [class*='cosc-card']"
    elif engine_name == "quark":
        # ai.quark.cn：资讯卡片在 article [class*="result-"] 下，AI 总结流式生成
        selector = 'article [class*="result-"], .sgs-container'
    else:
        selector = "div.result, li.res-list, li.result, section"
    stable_rounds = 0
    prev_count = -1
    deadline = time.monotonic() + min(5.0, max(1.0, timeout_ms / 1000))
    while time.monotonic() < deadline:
        try:
            count = await page.locator(selector).count()
        except Exception:
            return
        if count == prev_count:
            stable_rounds += 1
            if stable_rounds >= 2:
                return
        else:
            stable_rounds = 0
            prev_count = count
        await page.wait_for_timeout(400)


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    """浏览器与引擎状态检查。"""
    browser_ok = bool(_browser is not None and _browser.is_running)
    semaphore = _get_page_semaphore()
    return {
        "status": "ok" if browser_ok else "browser_not_started",
        "browser_running": browser_ok,
        "cdp_url": _browser.cdp_url if _browser is not None else None,
        "engines": {name: spec["label"] for name, spec in ENGINES.items()},
        "max_concurrent_pages": MAX_CONCURRENT_PAGES,
        "pages_in_use": MAX_CONCURRENT_PAGES - semaphore._value,
        "reused_pages": _browser.engine_page_count if _browser is not None else 0,
        "service": "patchright",
    }


@app.post("/search")
async def search(req: SearchRequest) -> Dict[str, Any]:
    """并发搜索百度/夸克/360，聚合去重后返回标准 SearchResponse JSON。"""
    query = (req.query or "").strip()
    if not query:
        return _search_response_to_dict(
            SearchResponse(
                query=req.query,
                results=[],
                provider="Patchright",
                success=False,
                error_message="查询为空",
            )
        )

    max_results = max(1, min(int(req.max_results), MAX_RESULTS_HARD_LIMIT))
    per_engine = min(max_results * 2, MAX_RESULTS_HARD_LIMIT)
    timeout_ms = DEFAULT_TIMEOUT_MS
    started = time.monotonic()

    tasks = [
        _search_one_engine(name, query, per_engine, timeout_ms)
        for name in ENGINES
    ]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: List[Dict[str, Any]] = []
    blocked_count = 0
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            blocked_count += 1
            logger.warning("引擎并发执行异常: %s", outcome)
            continue
        all_items.extend(outcome.get("results", []))
        if outcome.get("error"):
            blocked_count += 1

    results = _dedupe_results(all_items, max_results)
    elapsed = time.monotonic() - started
    logger.info(
        "[Patchright] 搜索 '%s' 完成: 原始 %s 条, 去重后 %s 条, 降级引擎 %s 个, 耗时 %.2fs",
        query,
        len(all_items),
        len(results),
        blocked_count,
        elapsed,
    )

    return _search_response_to_dict(
        SearchResponse(
            query=req.query,
            results=[
                SearchResult(
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    url=item.get("url", ""),
                    source=item.get("source", ""),
                    published_date=item.get("published_date"),
                )
                for item in results
            ],
            provider="Patchright",
            success=True,
            search_time=elapsed,
        )
    )


@app.post("/content")
async def fetch_content(req: ContentRequest) -> Dict[str, Any]:
    """抓取网页正文（预留接口，主流程不调用）。

    浏览器渲染抓取，语义对齐 src.search_service.fetch_url_content：
    失败返回空文本而非 5xx，文本截断至 1500 字。
    """
    url = (req.url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"text": "", "truncated": False}
    timeout_ms = max(1000, min(int(req.timeout * 1000), 30_000))
    if _browser is None:
        return {"text": "", "truncated": False}
    semaphore = _get_page_semaphore()
    async with semaphore:
        context = None
        page = None
        owns_context = False
        try:
            context, page, owns_context = await _browser.new_page()
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
            truncated = len(text) > 1500
            return {"text": text[:1500], "truncated": truncated}
        except Exception as exc:
            logger.info("正文抓取失败 %s: %s", url, exc)
            return {"text": "", "truncated": False}
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


if __name__ == "__main__":
    import uvicorn

    setup_logging(log_prefix="patchright_server")
    logger.info("Patchright 搜索服务启动: http://%s:%s", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
