# -*- coding: utf-8 -*-
"""Unit tests for the Patchright engine parsers (offline HTML fixtures)."""

import unittest
from datetime import datetime

from src.patchright_server.engines import (
    _extract_date,
    build_360_url,
    build_baidu_url,
    build_quark_url,
    is_blocked,
    parse_360,
    parse_baidu,
    parse_quark,
)

BAIDU_HTML = """
<html><body>
<div class="result c-container">
  <h3 class="t"><a href="https://finance.eastmoney.com/a/202608103836631044.html">
    3.29亿主力资金净流入，乳业概念涨3.82%</a></h3>
  <div class="c-abstract">2026-08-10 一鸣食品9.98 益生股份10.02 皇氏集团10.16</div>
</div>
<div class="result c-container">
  <h3 class="t"><a href="https://finance.sina.com.cn/stock/600887.shtml">
    伊利股份 2026年8月11日 最新公告</a></h3>
  <div class="c-abstract">伊利股份发布最新公告，营收同比增长</div>
</div>
<div class="result c-container">
  <h3><a href="https://example.com/no-date">无日期标题</a></h3>
  <div class="c-abstract">只有摘要没有日期</div>
</div>
</body></html>
"""

BAIDU_BLOCKED_HTML = """
<html><body><script>window.location='https://wappass.baidu.com/static/captcha'</script>
安全验证页面</body></html>
"""

QUARK_HTML = """
<html><body>
<section>
  <a href="https://news.qq.com/rain/a/20260810A00AX00"><h3>夸克抓到的腾讯新闻标题</h3></a>
  <div>2026-08-10 腾讯新闻内容摘要</div>
</section>
<section>
  <a href="https://quark.sm.cn/internal"><h3>内部链接应被跳过</h3></a>
</section>
</body></html>
"""

SO360_HTML = """
<html><body>
<li class="res-list">
  <h3 class="res-title"><a href="https://www.163.com/dy/article/KRKII92J0519D4UH.html">
    弘业期货2026年8月10日一季度净利增长</a></h3>
  <p></p>
  <span class="g-c-gray">2026年8月10日- </span>
  <span class="res-list-summary">网易财经 弘业期货业绩预告 净利润同比增长</span>
</li>
<li class="res-list">
  <h3 class="res-title"><a href="https://finance.jrj.com.cn/2025/01/27015647835421.shtml">
    弘业期货2024年业绩预告</a></h3>
  <p></p>
  <span class="res-desc">财经理财 净利润同比增长</span>
</li>
</body></html>
"""

SO360_BLOCKED_HTML = """
<html><body><div>请输入验证码以继续访问</div></body></html>
"""


class TestBlockDetection(unittest.TestCase):
    def test_blocked_markers(self) -> None:
        self.assertTrue(is_blocked(BAIDU_BLOCKED_HTML))
        self.assertTrue(is_blocked(SO360_BLOCKED_HTML))
        self.assertTrue(is_blocked(""))

    def test_normal_pages_not_blocked(self) -> None:
        self.assertFalse(is_blocked(BAIDU_HTML))
        self.assertFalse(is_blocked(SO360_HTML))
        self.assertFalse(is_blocked(QUARK_HTML))


class TestBaiduParser(unittest.TestCase):
    def test_build_url(self) -> None:
        url = build_baidu_url("第一创业 新闻")
        self.assertIn("baidu.com/s", url)
        self.assertIn("wd=", url)

    def test_parse_results_with_dates(self) -> None:
        results = parse_baidu(BAIDU_HTML, max_results=10)
        self.assertEqual(len(results), 3)
        first = results[0]
        self.assertEqual(first["source"], "baidu.com")
        self.assertEqual(first["published_date"], "2026-08-10")
        second = results[1]
        self.assertEqual(second["published_date"], "2026-08-11")
        # 无日期条目 published_date 为 None
        self.assertIsNone(results[2]["published_date"])

    def test_parse_respects_max_results(self) -> None:
        results = parse_baidu(BAIDU_HTML, max_results=1)
        self.assertEqual(len(results), 1)

    def test_parse_skips_invalid_links(self) -> None:
        html = '<div class="result"><h3><a href="javascript:void(0)">x</a></h3></div>'
        self.assertEqual(parse_baidu(html), [])


class TestQuarkParser(unittest.TestCase):
    def test_build_url(self) -> None:
        url = build_quark_url("第一创业 新闻")
        self.assertIn("quark.sm.cn/s", url)
        self.assertIn("q=", url)

    def test_parse_skips_internal_links(self) -> None:
        results = parse_quark(QUARK_HTML, max_results=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "夸克抓到的腾讯新闻标题")
        self.assertEqual(results[0]["url"], "https://news.qq.com/rain/a/20260810A00AX00")
        self.assertEqual(results[0]["published_date"], "2026-08-10")


class Test360Parser(unittest.TestCase):
    def test_build_url(self) -> None:
        url = build_360_url("第一创业 新闻")
        self.assertIn("so.com/s", url)
        self.assertIn("q=", url)

    def test_parse_results(self) -> None:
        results = parse_360(SO360_HTML, max_results=10)
        self.assertEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first["source"], "so.com")
        # 空 <p></p> 不吞摘要：摘要来自 .res-list-summary
        self.assertIn("网易财经", first["snippet"])
        # 日期来自 .g-c-gray span
        self.assertEqual(first["published_date"], "2026-08-10")
        second = results[1]
        self.assertIn("财经理财", second["snippet"])
        # 旧版 .res-desc 仍兼容；标题/摘要中无日期时 published_date 为 None
        self.assertIsNone(second["published_date"])

    def test_parse_empty(self) -> None:
        self.assertEqual(parse_360("<html></html>"), [])


class TestExtractDate(unittest.TestCase):
    def test_absolute_dates(self) -> None:
        self.assertEqual(_extract_date("2026-08-10 新闻"), "2026-08-10")
        self.assertEqual(_extract_date("2026年8月11日 公告"), "2026-08-11")
        self.assertEqual(_extract_date("2026/8/5 更新"), "2026-08-05")

    def test_no_date_returns_none(self) -> None:
        self.assertIsNone(_extract_date("只有摘要没有日期"))
        self.assertIsNone(_extract_date(""))

    def test_relative_dates(self) -> None:
        now = datetime(2026, 8, 13, 12, 0, 0)
        self.assertEqual(_extract_date("4天前 伊利股份新闻", now=now), "2026-08-09")
        self.assertEqual(_extract_date("发布于2小时前", now=now), "2026-08-13")
        self.assertEqual(_extract_date("30分钟前 快讯", now=now), "2026-08-13")
        self.assertEqual(_extract_date("1周前 报道", now=now), "2026-08-06")

    def test_relative_invalid_returns_none(self) -> None:
        now = datetime(2026, 8, 13, 12, 0, 0)
        self.assertIsNone(_extract_date("0天前", now=now))
        self.assertIsNone(_extract_date("很久以前", now=now))


if __name__ == "__main__":
    unittest.main()
