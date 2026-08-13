# -*- coding: utf-8 -*-
"""Feasibility tests: scrape 筹码分布 (chip distribution) from the eastmoney quote page
via the project's existing Patchright (CDP-takeover real Chrome) infrastructure.

背景（2026-08 实测）:
- 本项目已有的 Akshare 路径 `ak.stock_cyq_em` 与 Tushare 路径 `ts.pro_api().cyq_chips`
  在当前网络/账号下均不可用：akshare 依赖的 push2his.eastmoney.com 对纯 Python
  requests 连接直接断开（IP/TLS 指纹风控，但真实 Chrome 不受影响）；
  Tushare 账号无 cyq_chips 接口权限（需 5000 积分）。
- 东财行情页 https://quote.eastmoney.com/sz000001.html 的筹码分布图（canvas 渲染，
  无文本/DOM 数值），由页面 JS 用「最近 120 根日 K + 换手率」在本地计算（quotekchart
  的 CYQCalculator，accuracyFactor=150, range=120）。因此网页爬取的正确姿势是：
  1. 用 patchright（真实 Chrome）打开行情页 —— 绕过 requests 被风控的问题；
  2. 浏览器直接导航到日 K JSONP 接口（fqt=1 前复权，与 #fullScreenChart 页面默认口径
     一致；lmt=210 取最近 210 个交易日），从文档文本读取响应 —— push2his 对真实
     Chrome 不设防，且规避了在页面内注入 script 回调在此页面环境下不触发的问题；
     注意不要携带 smplmt 参数（会导致东财返回全历史等间隔采样数据，窗口失真）；
  3. 用与页面相同的算法在本地计算筹码指标（本文件中的 Python 移植版）。

  注意：akshare 的 stock_cyq_em 源码中 `this.range` 未定义，实际使用全部 K 线
  （range=0），与页面显示（range=120）数值不同；本测试两种模式都验证。

运行（依赖 workdaily 环境：patchright + akshare + py_mini_racer + 运行中的
run-patchright-server.bat / Chrome CDP）:
    E:\\Anaconda\\envs\\workdaily\\python.exe -m pytest tests/test_chip_distribution_patchright_scrape.py -v

离线部分（无浏览器/无网络）会在 CI 阻断门禁执行；在线部分全部带 @pytest.mark.network。
"""

import json
import math
import os
import re
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_FACTOR = 150  # 与页面 CYQCalculator 一致


def _akshare_cyq_js() -> str:
    """从 akshare 源码提取东财 CYQCalculator JS（与页面 quotekchart 同源算法）。"""
    import inspect

    import akshare as ak

    src = inspect.getsource(ak.stock_cyq_em)
    m = re.search(r'html_str = """\n(.*?)"""', src, re.S)
    if not m:
        raise RuntimeError("无法从 akshare.stock_cyq_em 提取 CYQCalculator JS")
    return m.group(1)


def compute_cyq_metrics(records, range_bars: int = 120) -> dict:
    """Python 移植版 CYQCalculator（东财页面算法）。

    :param records: 日 K 记录列表，每项含 open/close/high/low/hsl 字段
    :param range_bars: 参与计算的 K 线窗口。页面为 120；akshare 源码实际为 0（全部）
    :return: benefit_part(获利比例), avg_cost, 90/70 成本区间与集中度
    """
    start = max(0, len(records) - range_bars) if range_bars else 0
    kdata = records[start:]
    if not kdata:
        raise ValueError("invaild index")

    maxprice = max(r["high"] for r in kdata)
    minprice = min(r["low"] for r in kdata)
    accuracy = max(0.01, (maxprice - minprice) / (_FACTOR - 1))
    xdata = [0.0] * _FACTOR

    for ele in kdata:
        o, c, h, l = ele["open"], ele["close"], ele["high"], ele["low"]
        avg = (o + c + h + l) / 4
        turnover = min(1.0, (ele.get("hsl") or 0) / 100.0)
        high_idx = int((h - minprice) // accuracy)
        low_idx = int(math.ceil((l - minprice) / accuracy))
        g0 = _FACTOR - 1 if h == l else 2.0 / (h - l)
        g1 = int((avg - minprice) // accuracy)
        for n in range(_FACTOR):
            xdata[n] *= 1 - turnover
        if h == l:
            xdata[g1] += g0 * turnover / 2
        else:
            for j in range(low_idx, high_idx + 1):
                cur = minprice + accuracy * j
                if cur <= avg:
                    xdata[j] += ((cur - l) / (avg - l) * g0 * turnover) if abs(avg - l) >= 1e-8 else g0 * turnover
                else:
                    xdata[j] += ((h - cur) / (h - avg) * g0 * turnover) if abs(h - avg) >= 1e-8 else g0 * turnover

    current = kdata[-1]["close"]
    total = sum(xdata)
    x12 = [float(f"{x:.12g}") for x in xdata]  # 对齐 JS toPrecision(12)

    def get_cost(chip: float) -> float:
        acc = 0.0
        for i, x in enumerate(x12):
            if acc + x > chip:
                return minprice + i * accuracy
            acc += x
        return 0.0

    below = sum(x12[i] for i in range(_FACTOR) if current >= minprice + accuracy * i)
    benefit = below / total if total else 0.0
    avg_cost = round(get_cost(total * 0.5), 2)

    def _pct(percent: float):
        lo_p, hi_p = (1 - percent) / 2, (1 + percent) / 2
        lo, hi = get_cost(total * lo_p), get_cost(total * hi_p)
        conc = 0.0 if (lo + hi) == 0 else (hi - lo) / (lo + hi)
        return [round(lo, 2), round(hi, 2)], conc

    p90, c90 = _pct(0.9)
    p70, c70 = _pct(0.7)
    return {
        "benefit_part": benefit,
        "avg_cost": avg_cost,
        "90_low": p90[0], "90_high": p90[1], "c90": c90,
        "70_low": p70[0], "70_high": p70[1], "c70": c70,
    }


def _parse_kline_jsonp(body: str) -> list:
    """解析 push2his kline/get 的 JSONP 文本（如 '__cyqScrape({...});'）为记录列表。"""
    start = body.find("(")
    end = body.rfind(")")
    if start < 0 or end <= start:
        raise ValueError(f"非法 JSONP 响应: {body[:80]!r}")
    data = json.loads(body[start + 1 : end])
    records = []
    for item in data["data"]["klines"]:
        p = item.split(",")
        records.append(
            {
                "date": p[0],
                "open": float(p[1]),
                "close": float(p[2]),
                "high": float(p[3]),
                "low": float(p[4]),
                "volume": float(p[5]),
                "amount": float(p[6]),
                "zf": float(p[7]),
                "zdf": float(p[8]),
                "zde": float(p[9]),
                "hsl": float(p[10]),
            }
        )
    return records


def _eastmoney_market(code: str) -> tuple:
    """返回 (行情页交易所前缀, 东财 secid market)。

    东财 secid 约定：market=0 深市（00/002/300/北交所），market=1 沪市（60/68）。
    """
    if code.startswith("6"):
        return "sh", "1"
    return "sz", "0"


# 多股核对清单：沪深主板/创业板/科创板/北交所
_MULTI_STOCK_CASES = [
    ("600519", "贵州茅台 沪主板"),
    ("600036", "招商银行 沪主板"),
    ("000001", "平安银行 深主板"),
    ("000858", "五粮液 深主板"),
    ("002594", "比亚迪 深主板(原中小板)"),
    ("300750", "宁德时代 深创业板"),
    ("688981", "中芯国际 沪科创板"),
    ("920748", "北交所示例"),
]


# 固定夹具：5 根日 K（确定性，与 akshare JS 引擎在 r=120/全部 下均一致）
_FIXTURE_RECORDS = [
    {"open": 10.0, "close": 10.5, "high": 10.8, "low": 9.9, "volume": 100000, "amount": 1e8, "zf": 2.0, "zdf": 5.0, "zde": 0.5, "hsl": 1.2},
    {"open": 10.5, "close": 10.2, "high": 10.7, "low": 10.1, "volume": 80000, "amount": 8e7, "zf": 1.0, "zdf": -3.0, "zde": -0.3, "hsl": 0.9},
    {"open": 10.2, "close": 10.6, "high": 10.9, "low": 10.0, "volume": 90000, "amount": 9e7, "zf": 1.5, "zdf": 4.0, "zde": 0.4, "hsl": 1.0},
    {"open": 10.6, "close": 10.9, "high": 11.0, "low": 10.4, "volume": 110000, "amount": 1.1e8, "zf": 1.0, "zdf": 2.8, "zde": 0.3, "hsl": 1.1},
    {"open": 10.9, "close": 10.7, "high": 11.1, "low": 10.6, "volume": 95000, "amount": 1e8, "zf": 0.8, "zdf": -1.8, "zde": -0.2, "hsl": 1.0},
]

# 快照值：由 akshare 的 CYQCalculator JS（py_mini_racer）对夹具计算得到
_FIXTURE_EXPECTED = {
    "benefit_part": 0.6765993361265807,
    "avg_cost": 10.54,
    "90_low": 10.14, "90_high": 10.93, "c90": 0.03749406739439958,
    "70_low": 10.27, "70_high": 10.83, "c70": 0.026540284360189594,
}


class TestCyqPythonPortOffline(unittest.TestCase):
    """离线验证 Python 移植版算法（CI 阻断门禁内执行，无需浏览器/网络）。"""

    def test_fixture_matches_snapshot(self) -> None:
        out = compute_cyq_metrics(_FIXTURE_RECORDS)
        for key, expected in _FIXTURE_EXPECTED.items():
            self.assertAlmostEqual(out[key], expected, places=6, msg=key)

    def test_fixture_sanity(self) -> None:
        out = compute_cyq_metrics(_FIXTURE_RECORDS)
        self.assertGreater(out["avg_cost"], 0)
        self.assertGreaterEqual(out["benefit_part"], 0.0)
        self.assertLessEqual(out["benefit_part"], 1.0)
        self.assertLessEqual(out["70_low"], out["70_high"])
        self.assertLessEqual(out["90_low"], out["90_high"])
        self.assertGreaterEqual(out["c90"], 0.0)
        self.assertGreaterEqual(out["c70"], 0.0)

    def test_port_matches_akshare_js_both_modes(self) -> None:
        """Python 移植版 == akshare 的 CYQCalculator JS（r=120 页面语义 / r=0 akshare 语义）。"""
        try:
            from py_mini_racer import MiniRacer
        except ImportError:
            self.skipTest("py_mini_racer not installed")
        try:
            js = _akshare_cyq_js()
        except Exception as exc:
            self.skipTest(f"akshare 不可用: {exc}")
        mr = MiniRacer()
        mr.eval(js)
        records_json = json.dumps(_FIXTURE_RECORDS)
        for range_bars in (120, 0):
            js_res = mr.eval(
                "var __r = %s; JSON.parse(JSON.stringify(CYQCalculator.call({range: %d}, %d, __r)))"
                % (records_json, range_bars, len(_FIXTURE_RECORDS) - 1)
            )
            py_res = compute_cyq_metrics(_FIXTURE_RECORDS, range_bars=range_bars)
            self.assertAlmostEqual(py_res["benefit_part"], js_res["benefitPart"], places=12)
            self.assertAlmostEqual(py_res["avg_cost"], float(js_res["avgCost"]), places=9)
            for key in ("70", "90"):
                self.assertAlmostEqual(
                    py_res[key + "_low"], float(js_res["percentChips"][key]["priceRange"][0]), places=9
                )
                self.assertAlmostEqual(
                    py_res[key + "_high"], float(js_res["percentChips"][key]["priceRange"][1]), places=9
                )
                self.assertAlmostEqual(py_res["c" + key], js_res["percentChips"][key]["concentration"], places=9)


def _resolve_cdp_url() -> str:
    """依次尝试：PATCHRIGHT_CDP_URL 环境变量 -> patchright 服务 healthz -> 默认 9228 -> 9222。"""
    env = (os.getenv("PATCHRIGHT_CDP_URL") or "").strip()
    if env:
        return env
    try:
        import requests

        resp = requests.get(
            f"http://127.0.0.1:{os.getenv('PATCHRIGHT_SERVER_PORT', '8931')}/healthz",
            timeout=2,
        )
        cdp = (resp.json().get("cdp_url") or "").strip()
        if cdp:
            return cdp
    except Exception:
        pass
    return "http://127.0.0.1:9228"


@pytest.mark.network
class TestChipPatchrightScrapeLive(unittest.IsolatedAsyncioTestCase):
    """在线验证：patchright 打开东财页面 -> JSONP 拉日 K -> 本地算筹码指标。"""

    async def _browser_and_page(self):
        from src.patchright_server.browser import PatchrightBrowser

        browser = PatchrightBrowser(cdp_url=_resolve_cdp_url())
        try:
            _, page, _ = await browser.new_page()
        except Exception as exc:
            await browser.close()
            self.skipTest(
                f"无法连接 Chrome CDP（{browser.cdp_url}）：{exc}。"
                "请先运行 run-patchright-server.bat 或手动启动带 --remote-debugging-port 的 Chrome。"
            )
        return browser, page

    async def _scrape_kline_text(self, page, code: str = "000001", fqt: str = "1") -> str:
        """先打开行情页（建立东财 referer 上下文），再直接导航到日 K JSONP 接口读取响应文本。

        注：在页面内注入 script 标签做 JSONP 回调在此页面环境下不可靠
        （实测 onload 触发但回调不执行）；直接导航读取文档文本最稳定。
        """
        exchange, market = _eastmoney_market(code)
        await page.goto(
            f"https://quote.eastmoney.com/{exchange}{code}.html",
            timeout=30000,
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(2000)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?cb=__cyqScrape&secid={market}.{code}"
            "&ut=fa5fd1943c7b386f172d6893dbfba10b"
            "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt=101&fqt={fqt}&end={today}&lmt=210"
        )
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if not text.lstrip().startswith("__cyqScrape("):
            raise AssertionError(f"日 K 接口响应异常: {text[:120]!r}")
        return text

    async def test_01_quote_page_loads_in_browser(self) -> None:
        """真实 Chrome 能打开东财行情页（纯 Python requests 被断连，浏览器不受影响）。"""
        try:
            from src.patchright_server.browser import PatchrightBrowser  # noqa: F401
        except ImportError:
            self.skipTest("patchright not installed (workdaily env)")
        browser, page = await self._browser_and_page()
        try:
            await page.goto(
                "https://quote.eastmoney.com/sz000001.html",
                timeout=30000,
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(5000)
            title = await page.title()
            self.assertIn("平安银行", title, f"页面标题异常: {title}")
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await browser.close()

    async def test_02_scrape_kline_via_browser(self) -> None:
        """浏览器导航到日 K JSONP 接口（fqt=1 前复权，lmt=210），解析出标准记录。"""
        try:
            from src.patchright_server.browser import PatchrightBrowser  # noqa: F401
        except ImportError:
            self.skipTest("patchright not installed (workdaily env)")
        browser, page = await self._browser_and_page()
        try:
            text = await self._scrape_kline_text(page)
            records = _parse_kline_jsonp(text)
            self.assertGreater(len(records), 120, "K 线数量不足")
            self.assertLessEqual(len(records), 212, "K 线数量超限（疑似 smplmt 采样 bug 回归）")
            last = records[-1]
            for field in ("open", "close", "high", "low", "hsl"):
                self.assertGreater(last[field], 0, f"字段 {field} 异常")
            self.assertGreaterEqual(len(last["date"]), 8, "日期格式异常")
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await browser.close()

    async def test_03_compute_chip_metrics_from_scraped_data(self) -> None:
        """基于抓取的日 K 计算筹码指标，并校验数值合理（满足项目 ChipDistribution 契约）。

        同时校验数据窗口正确：最近约一年的日 K（防止接口参数回归导致返回
        全历史采样数据，窗口价格必须与现价同量级）。
        """
        try:
            from src.patchright_server.browser import PatchrightBrowser  # noqa: F401
        except ImportError:
            self.skipTest("patchright not installed (workdaily env)")
        browser, page = await self._browser_and_page()
        try:
            text = await self._scrape_kline_text(page)
            records = _parse_kline_jsonp(text)
            # 窗口正确性：数据跨度约 1 年（210 个交易日），价格与现价同量级
            first_date = datetime.strptime(records[0]["date"], "%Y-%m-%d")
            last_date = datetime.strptime(records[-1]["date"], "%Y-%m-%d")
            self.assertLessEqual((last_date - first_date).days, 400, "K 线时间跨度异常（疑似采样数据）")
            close = records[-1]["close"]
            window_high = max(r["high"] for r in records[-120:])
            window_low = min(r["low"] for r in records[-120:])
            self.assertLessEqual(window_high, close * 4, "窗口最高价与现价量级不符（疑似采样数据）")
            self.assertGreater(window_low, 0)
            metrics = compute_cyq_metrics(records, range_bars=120)
            self.assertGreater(metrics["avg_cost"], 0, "平均成本异常")
            self.assertGreaterEqual(metrics["benefit_part"], 0.0)
            self.assertLessEqual(metrics["benefit_part"], 1.0)
            self.assertLessEqual(metrics["70_low"], metrics["70_high"])
            self.assertLessEqual(metrics["90_low"], metrics["90_high"])
            self.assertGreaterEqual(metrics["c90"], 0.0)
            self.assertGreaterEqual(metrics["c70"], 0.0)
            self.assertTrue(
                0.0 <= metrics["benefit_part"] <= 0.9,
                f"获利比例超出合理范围: {metrics['benefit_part']}",
            )
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await browser.close()

    async def test_04_page_js_calculation_matches_python_port(self) -> None:
        """页面同源算法（range=120）在页面内计算结果 == Python 移植版（同一份抓取数据）。"""
        try:
            from src.patchright_server.browser import PatchrightBrowser  # noqa: F401
        except ImportError:
            self.skipTest("patchright not installed (workdaily env)")
        try:
            js = _akshare_cyq_js()
        except Exception as exc:
            self.skipTest(f"akshare 不可用: {exc}")
        browser, page = await self._browser_and_page()
        try:
            text = await self._scrape_kline_text(page)
            records = _parse_kline_jsonp(text)
            idx = len(records) - 1
            js_res = await page.evaluate(
                "(args) => { const records = args.records; eval(args.jsCode); "
                "const res = CYQCalculator.call({range: 120}, args.idx, records); "
                "return JSON.parse(JSON.stringify(res)); }",
                {"records": records, "jsCode": js, "idx": idx},
            )
            py_res = compute_cyq_metrics(records, range_bars=120)
            self.assertAlmostEqual(py_res["benefit_part"], js_res["benefitPart"], places=12)
            self.assertAlmostEqual(py_res["avg_cost"], float(js_res["avgCost"]), places=9)
            for key in ("70", "90"):
                self.assertAlmostEqual(
                    py_res[key + "_low"], float(js_res["percentChips"][key]["priceRange"][0]), places=9
                )
                self.assertAlmostEqual(
                    py_res[key + "_high"], float(js_res["percentChips"][key]["priceRange"][1]), places=9
                )
                self.assertAlmostEqual(py_res["c" + key], js_res["percentChips"][key]["concentration"], places=9)
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await browser.close()

    async def test_05_multi_stock_returns_chip_data(self) -> None:
        """多股（沪深主板/创业板/科创板/北交所）逐一抓取并计算筹码指标。

        每只股票打印一行供人工与东财页面显示核对（执行时加 -s 显示 print 输出）。
        """
        try:
            from src.patchright_server.browser import PatchrightBrowser  # noqa: F401
        except ImportError:
            self.skipTest("patchright not installed (workdaily env)")
        browser, page = await self._browser_and_page()
        try:
            print(
                "\n{:<7}{:<20}{:<12}{:>8}{:>10}{:>9}{:>17}{:>10}{:>17}{:>10}".format(
                    "code", "name", "date", "close", "获利比例", "平均成本",
                    "90成本区间", "90集中度", "70成本区间", "70集中度",
                )
            )
            for code, name in _MULTI_STOCK_CASES:
                with self.subTest(code=code):
                    text = await self._scrape_kline_text(page, code)
                    records = _parse_kline_jsonp(text)
                    self.assertGreater(len(records), 120, f"{code} K 线数量不足")
                    self.assertLessEqual(len(records), 212, f"{code} K 线数量超限（疑似采样 bug）")
                    first_date = datetime.strptime(records[0]["date"], "%Y-%m-%d")
                    last_date = datetime.strptime(records[-1]["date"], "%Y-%m-%d")
                    self.assertLessEqual(
                        (last_date - first_date).days, 400, f"{code} K 线时间跨度异常（疑似采样数据）"
                    )
                    close = records[-1]["close"]
                    window_high = max(r["high"] for r in records[-120:])
                    self.assertLessEqual(window_high, close * 4, f"{code} 窗口价格与现价量级不符（疑似采样数据）")
                    metrics = compute_cyq_metrics(records, range_bars=120)
                    self.assertGreater(metrics["avg_cost"], 0)
                    self.assertGreaterEqual(metrics["benefit_part"], 0.0)
                    self.assertLessEqual(metrics["benefit_part"], 1.0)
                    self.assertLessEqual(metrics["70_low"], metrics["70_high"])
                    self.assertLessEqual(metrics["90_low"], metrics["90_high"])
                    self.assertGreaterEqual(metrics["c90"], 0.0)
                    self.assertGreaterEqual(metrics["c70"], 0.0)
                    print(
                        "{:<7}{:<20}{:<12}{:>8.2f}{:>10.2%}{:>9.2f}"
                        "[{:>7.2f},{:>7.2f}]{:>10.2%}[{:>7.2f},{:>7.2f}]{:>10.2%}".format(
                            code, name, records[-1]["date"], records[-1]["close"],
                            metrics["benefit_part"], metrics["avg_cost"],
                            metrics["90_low"], metrics["90_high"], metrics["c90"],
                            metrics["70_low"], metrics["70_high"], metrics["c70"],
                        )
                    )
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await browser.close()


if __name__ == "__main__":
    unittest.main()
