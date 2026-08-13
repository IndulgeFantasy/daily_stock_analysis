# -*- coding: utf-8 -*-
"""
搜索引擎解析器：百度 / 夸克 / 360。

每个引擎提供：
- build_url(query) -> str
- parse(html) -> List[Dict]  (title/snippet/url/source/published_date)
- is_blocked(html) -> bool  风控特征检测

解析器为纯函数，便于离线单元测试。
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 风控特征（命中任一即视为被拦截/降级）
_BLOCK_MARKERS = (
    "captcha",
    "punish",
    "wappass",
    "安全验证",
    "请输入验证码",
    "访问异常",
    "antispider",
    "验证码",
    "x5sec",
)

# 日期提取：优先 YYYY-MM-DD / YYYY年MM月DD日 / YYYY/MM/DD；无年份的日期无法满足时效过滤，跳过
_DATE_PATTERN = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})日?")


# 日期提取：优先 YYYY-MM-DD / YYYY年MM月DD日 / YYYY/MM/DD；无年份日期无法满足时效过滤，跳过
_DATE_PATTERN = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})日?")
# 相对日期：N 分钟/小时/天/周前（如 "4天前"、"2小时前"）
_RELATIVE_DATE_PATTERN = re.compile(r"(\d{1,3})\s*(?:分钟|分钟前|小时|小时前|天|天前|日|日前|周|周前)前?")


def _extract_date(text: str, now=None) -> Optional[str]:
    """从文本中提取日期，格式化为 YYYY-MM-DD。

    支持绝对日期与相对日期（"N分钟前/N小时前/N天前/N周前"）。
    相对日期以当前时间推算，无年份日期返回 None。
    """
    if not text:
        return None
    match = _DATE_PATTERN.search(text)
    if match:
        year, month, day = match.groups()
        try:
            month_i, day_i = int(month), int(day)
        except ValueError:
            return None
        if not (1 <= month_i <= 12 and 1 <= day_i <= 31):
            return None
        return f"{year}-{month_i:02d}-{day_i:02d}"

    rel_match = _RELATIVE_DATE_PATTERN.search(text)
    if not rel_match:
        return None
    try:
        amount = int(rel_match.group(1))
    except ValueError:
        return None
    if amount <= 0:
        return None
    raw = rel_match.group(0)
    if "周" in raw:
        delta = timedelta(weeks=amount)
    elif "天" in raw or "日" in raw:
        delta = timedelta(days=amount)
    elif "小时" in raw:
        delta = timedelta(hours=amount)
    elif "分钟" in raw:
        delta = timedelta(minutes=amount)
    else:
        return None
    base = now or datetime.now()
    return (base - delta).strftime("%Y-%m-%d")


def is_blocked(html: str) -> bool:
    """检测结果页是否被风控拦截。"""
    if not html:
        return True
    lowered = html.lower()
    return any(marker in lowered for marker in _BLOCK_MARKERS)


def _norm_url(url: str) -> str:
    return (url or "").strip()


def _clean_text(text: str, limit: int = 500) -> str:
    """清理空白并截断。"""
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)
    return joined[:limit]


# ---------------------------------------------------------------------------
# 百度
# ---------------------------------------------------------------------------

BAIDU_URL = "https://www.baidu.com/s"
BAIDU_SOURCE = "baidu.com"


def build_baidu_url(query: str) -> str:
    from urllib.parse import urlencode

    return f"{BAIDU_URL}?{urlencode({'wd': query, 'rn': 20})}"


def parse_baidu(html: str, max_results: int = 10) -> List[Dict]:
    """解析百度搜索结果页（class="result c-container"）。"""
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict] = []
    containers = soup.select("div.result, div.c-container")
    for container in containers:
        if len(results) >= max_results:
            break
        anchor = container.find("h3")
        if anchor is None:
            anchor = container.find("a", href=True)
        if anchor is None:
            continue
        link_el = anchor if anchor.name == "a" else anchor.find("a", href=True)
        if link_el is None:
            continue
        link = link_el.get("href") or ""
        title = link_el.get_text(" ", strip=True)
        if not title or not link.startswith("http"):
            continue
        # 摘要：优先 c-abstract，其次 container 全部文本
        abstract_el = container.select_one(".c-abstract, .c-span-last, .content-right_8Zs40")
        if abstract_el is not None:
            snippet = _clean_text(abstract_el.get_text(" ", strip=True))
        else:
            snippet = _clean_text(container.get_text(" ", strip=True)[:500])
        if not snippet:
            snippet = ""
        source = BAIDU_SOURCE
        results.append(
            {
                "title": title,
                "snippet": snippet,
                "url": _norm_url(link),
                "source": source,
                "published_date": _extract_date(f"{title} {snippet}"),
            }
        )
    return results


# ---------------------------------------------------------------------------
# 夸克
# ---------------------------------------------------------------------------

QUARK_URL = "https://quark.sm.cn/s"
QUARK_SOURCE = "quark.sm.cn"


def build_quark_url(query: str) -> str:
    from urllib.parse import urlencode

    return f"{QUARK_URL}?{urlencode({'q': query})}"


def parse_quark(html: str, max_results: int = 10) -> List[Dict]:
    """解析夸克搜索结果页（结构较散，采用通用 a+h3 提取）。"""
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict] = []
    for anchor in soup.find_all("a", href=True):
        if len(results) >= max_results:
            break
        href = anchor["href"]
        if not href.startswith("http") or "sm.cn" in href or "quark" in href:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title or len(title) < 4:
            continue
        # 摘要：取链接容器的邻近文本
        container = anchor.find_parent(["div", "section", "li"])
        snippet = ""
        if container is not None:
            snippet = _clean_text(container.get_text(" ", strip=True))
        results.append(
            {
                "title": title[:200],
                "snippet": snippet,
                "url": _norm_url(href),
                "source": QUARK_SOURCE,
                "published_date": _extract_date(f"{title} {snippet}"),
            }
        )
    return results


# ---------------------------------------------------------------------------
# 360 搜索
# ---------------------------------------------------------------------------

SO_URL = "https://www.so.com/s"
SO_SOURCE = "so.com"


def build_360_url(query: str) -> str:
    from urllib.parse import urlencode

    return f"{SO_URL}?{urlencode({'q': query})}"


def parse_360(html: str, max_results: int = 10) -> List[Dict]:
    """解析 360 搜索结果页（li.res-list）。"""
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict] = []
    containers = soup.select("li.res-list, li.result")
    for container in containers:
        if len(results) >= max_results:
            break
        anchor = container.find("h3")
        if anchor is None:
            anchor = container.find("a", href=True)
        if anchor is None:
            continue
        link_el = anchor if anchor.name == "a" else anchor.find("a", href=True)
        if link_el is None:
            continue
        link = link_el.get("href") or ""
        title = link_el.get_text(" ", strip=True)
        if not title or not link.startswith("http"):
            continue
        desc_el = container.select_one(".res-list-summary, .res-desc, .res-desc-strong")
        snippet = ""
        if desc_el is not None:
            snippet = _clean_text(desc_el.get_text(" ", strip=True))
        # 360 新版把日期放在 .g-c-gray span 中（如 "2025年11月7日- "）
        date_el = container.select_one(".g-c-gray")
        date_text = date_el.get_text(" ", strip=True) if date_el is not None else ""
        if not snippet:
            snippet = _clean_text(container.get_text(" ", strip=True)[:500])
        results.append(
            {
                "title": title[:200],
                "snippet": snippet,
                "url": _norm_url(link),
                "source": SO_SOURCE,
                "published_date": _extract_date(f"{date_text} {title} {snippet}"),
            }
        )
    return results


# ---------------------------------------------------------------------------
# 引擎注册表
# ---------------------------------------------------------------------------

ENGINES: Dict[str, Dict] = {
    "baidu": {
        "name": "baidu",
        "label": "百度",
        "build_url": build_baidu_url,
        "parse": parse_baidu,
    },
    "quark": {
        "name": "quark",
        "label": "夸克",
        "build_url": build_quark_url,
        "parse": parse_quark,
    },
    "360": {
        "name": "360",
        "label": "360搜索",
        "build_url": build_360_url,
        "parse": parse_360,
    },
}
