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
<div class="results">
<article>
  <div class="result-EzdYH">
    <a href="https://news.qq.com/rain/a/20260810A00AX00"><h3>夸克抓到的腾讯新闻标题</h3></a>
    <span>腾讯网 2026-08-10 腾讯新闻内容摘要 window._q_wl_sc_1_2 = Date.now();</span>
  </div>
</article>
<article>
  <div class="result-EzdYH">
    <a href="javascript:void(0)"><h3>无效链接应被跳过</h3></a>
  </div>
</article>
</div>
</body></html>
"""

QUARK_HTML_AI = """
<html><body>
<div class="results">
<article>
  <div class="result-EzdYH">
    <a href="https://page.sm.cn/blm/video-page-710/video?id=1"><h3>伊利股份！高盛最新评级：买入</h3></a>
    <span>高盛发布研报称，伊利股份600887因优然牧业扭亏为盈而面临业绩分化 2026-07-29</span>
  </div>
</article>
<article>
  <div class="result-EzdYH">
    <a href="https://www.sohu.com/a/20260512.html"><h3>业绩双增，伊利11万股东沸腾了</h3></a>
    <span>伊利股份在2025年和2026年第一季度实现了营收和净利润的双增 2026-05-12</span>
  </div>
</article>
<article>
  <div class="result-EzdYH">
    <a href="http://emweb.eastmoney.com/PC_HSF10/NewsBulletin/index?code=SH600887"><h3>伊利股份(600887.SH)资讯公告</h3></a>
    <span>内蒙古伊利实业集团股份有限公司关于境外全资子公司 2026-06-26</span>
  </div>
</article>
</div>
<div class="sgs-container">
  以上内容由AI生成以上内容由AI生成内容由AI生成 仅供参考收藏导出分享生成PPT
</div>
</body></html>
"""

QUARK_HTML_AI_REAL = """
<html><body>
<div class="sgs-container">
基于最新市场信息，伊利股份（600887）近期获得高盛买入评级，目标价上调。
高盛指出优然牧业扭亏为盈与澳优乳业亏损形成业绩分化。公司2025年营收与
净利润双增，多元化产品布局和渠道改革成效显著。机构普遍看好其成本控制
与周期底部盈利能力，建议关注乳制品行业复苏节奏与原材料价格走势。
</div>
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

    def test_parse_extracts_ai_summary(self) -> None:
        html = """
<html><body>
<div class="cosc-card dqa-layout_2uZOY baikan-pc-experiment_5wdpa">
  <div class="cosc-card-content-border">
    <div class="cosc-card-content">
      基于当前市场公开资料与主流券商研报，牧原股份（002714）作为全球生猪养殖龙头。
      综合评级：近三个月33位分析师中81.82%给予强力推荐。平均目标价55.96元。
    </div>
  </div>
</div>
<div class="result c-container">
  <h3 class="t"><a href="https://finance.eastmoney.com/a/1.html">牧原股份 研报</a></h3>
  <div class="c-abstract">2026-08-10 研报摘要</div>
</div>
</body></html>
"""
        results = parse_baidu(html, max_results=10)
        # AI 总结作为第一条，日期为当天
        self.assertEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first["source"], "baidu_ai_summary")
        self.assertEqual(first["title"], "[AI总结] 百度智能聚合分析")
        self.assertIn("牧原股份", first["snippet"])
        self.assertEqual(first["published_date"], datetime.now().strftime("%Y-%m-%d"))
        # 普通结果紧随其后
        self.assertEqual(results[1]["source"], "baidu.com")

    def test_parse_ignores_related_search_cards(self) -> None:
        """cosc-card 中的"相关搜索"/富途牛牛等普通卡片不应误判为 AI 总结。"""
        html = """
<html><body>
<div class="cosc-card">
  <div class="cosc-card-content">相关搜索\n牧原股份一季度业绩公告\n牧原股份深度分析</div>
</div>
<div class="cosc-card aladdin-struct_r13eS">
  <div class="cosc-card-content">牧原股份 (002714)股票预测和分析师评级 - 富途牛牛</div>
</div>
<div class="result c-container">
  <h3 class="t"><a href="https://finance.eastmoney.com/a/2.html">牧原股份 新闻</a></h3>
  <div class="c-abstract">2026-08-11 新闻摘要</div>
</div>
</body></html>
"""
        results = parse_baidu(html, max_results=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "baidu.com")
        self.assertNotEqual(results[0]["source"], "baidu_ai_summary")

    def test_ai_summary_counts_toward_max_results(self) -> None:
        html = """
<html><body>
<div class="cosc-card dqa-layout">
  <div class="cosc-card-content">基于当前市场公开资料，这是 AI 总结正文。</div>
</div>
<div class="result c-container">
  <h3 class="t"><a href="https://finance.eastmoney.com/a/3.html">牧原股份</a></h3>
  <div class="c-abstract">2026-08-11 摘要</div>
</div>
</body></html>
"""
        results = parse_baidu(html, max_results=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "baidu_ai_summary")


class TestQuarkParser(unittest.TestCase):
    def test_build_url(self) -> None:
        url = build_quark_url("第一创业 新闻")
        self.assertIn("ai.quark.cn/s/x", url)
        self.assertIn("q=", url)
        self.assertIn("by=submit", url)

    def test_parse_skips_invalid_links(self) -> None:
        results = parse_quark(QUARK_HTML, max_results=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "夸克抓到的腾讯新闻标题")
        self.assertEqual(results[0]["url"], "https://news.qq.com/rain/a/20260810A00AX00")
        self.assertEqual(results[0]["published_date"], "2026-08-10")

    def test_parse_ai_quark_cards_with_dates(self) -> None:
        """ai.quark.cn 卡片：日期从卡片文本提取，内部链接跳过，脚本噪声清理。"""
        results = parse_quark(QUARK_HTML_AI, max_results=10)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["title"], "伊利股份！高盛最新评级：买入")
        self.assertEqual(results[0]["published_date"], "2026-07-29")
        self.assertEqual(results[1]["published_date"], "2026-05-12")
        self.assertEqual(results[2]["published_date"], "2026-06-26")
        # 脚本噪声已清理
        self.assertNotIn("_q_wl_sc", results[0]["snippet"])

    def test_parse_ignores_ai_template_placeholder(self) -> None:
        """AI 总结为模板占位（"以上内容由AI生成"）时不提取。"""
        results = parse_quark(QUARK_HTML_AI, max_results=10)
        ai = [r for r in results if r["source"] == "quark_ai_summary"]
        self.assertEqual(ai, [])

    def test_parse_extracts_real_ai_summary(self) -> None:
        """AI 总结有真实内容（>100 字符且无模板标记）时提取，日期为当天。"""
        results = parse_quark(QUARK_HTML_AI_REAL, max_results=10)
        ai = [r for r in results if r["source"] == "quark_ai_summary"]
        self.assertEqual(len(ai), 1)
        self.assertEqual(ai[0]["title"], "[AI总结] 夸克智能聚合分析")
        self.assertIn("伊利股份", ai[0]["snippet"])
        self.assertEqual(ai[0]["published_date"], datetime.now().strftime("%Y-%m-%d"))


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
