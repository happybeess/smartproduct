"""
Amazon 商品评论爬虫 - CDP WebSocket 版
=======================================
通过 CDP 连接已打开的 Chrome 浏览器（需启动时加 --remote-debugging-port=9222），
复用已登录的 Amazon 会话，抓取 product-reviews 页面的1/2/3星差评。

Chrome 启动方式（任意一种）：
  方式1: 桌面快捷方式已配置 --remote-debugging-port=9222
  方式2: 命令行: chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\ChromeDebug
"""

import json
import re
import time
import asyncio
import urllib.request
from typing import Optional
from bs4 import BeautifulSoup

CDP_PORT = 9222
_msg_id = 0


# ============================================================
# CDP 基础工具（复用项目统一风格）
# ============================================================

def _get_cdp_tab(port: int = CDP_PORT) -> Optional[str]:
    """获取当前激活的浏览器标签 WebSocket URL"""
    try:
        url = f"http://localhost:{port}/json"
        with urllib.request.urlopen(url, timeout=5) as r:
            tabs = json.loads(r.read())
        # 优先找 Amazon 标签，没有则用第一个 page 类型
        for tab in tabs:
            if tab.get("type") == "page" and "amazon.com" in tab.get("url", ""):
                return tab["webSocketDebuggerUrl"]
        for tab in tabs:
            if tab.get("type") == "page":
                return tab["webSocketDebuggerUrl"]
    except Exception as e:
        print(f"[Amazon评论] 连接 CDP 失败（端口 {port}）: {e}")
    return None


async def _cdp_send(ws, method: str, params: dict = None) -> dict:
    """发送单条 CDP 命令并等待对应回复"""
    global _msg_id
    _msg_id += 1
    msg = {"id": _msg_id, "method": method}
    if params:
        msg["params"] = params
    await ws.send(json.dumps(msg))
    cur_id = _msg_id
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        r = json.loads(raw)
        if r.get("id") == cur_id:
            return r


async def _eval(ws, js: str):
    """在页面执行 JS 并返回值"""
    r = await _cdp_send(ws, "Runtime.evaluate", {
        "expression": js,
        "returnByValue": True,
        "awaitPromise": True,
    })
    return r.get("result", {}).get("result", {}).get("value")


async def _wait_ready(ws, timeout: int = 20):
    """等待页面 document.readyState == complete"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        state = await _eval(ws, "document.readyState")
        if state == "complete":
            return True
        await asyncio.sleep(0.5)
    return False


async def _wait_reviews(ws, timeout: int = 15) -> int:
    """等待评论列表渲染，返回当前页评论数"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        count = await _eval(ws, "document.querySelectorAll('[data-hook=\"review\"]').length")
        if count and int(count) > 0:
            return int(count)
        await asyncio.sleep(0.6)
    return 0


# ============================================================
# 解析工具
# ============================================================

def _parse_reviews(html: str) -> list:
    """从页面 HTML 解析评论列表"""
    soup = BeautifulSoup(html, "html.parser")
    reviews = []
    # Amazon 评论可能是 <li> 或 <div>，用通配匹配
    items = soup.find_all(attrs={"data-hook": "review"})
    for item in items:
        review = {}

        # 评分
        rating = 0.0
        for hook in ("review-star-rating", "cmps-review-star-rating"):
            star_el = item.find("i", {"data-hook": hook})
            if star_el:
                span = star_el.find("span", class_="a-icon-alt")
                if span:
                    m = re.search(r"([\d.]+)", span.get_text())
                    if m:
                        rating = float(m.group(1))
                        break
        review["rating"] = rating

        # 标题
        title_el = item.find("a", {"data-hook": "review-title"}) or \
                   item.find("span", {"data-hook": "review-title"})
        if title_el:
            for s in title_el.find_all("span", class_="a-icon-alt"):
                s.decompose()
            review["title"] = title_el.get_text(strip=True)
        else:
            review["title"] = ""

        # 日期
        date_el = item.find("span", {"data-hook": "review-date"})
        review["date"] = date_el.get_text(strip=True) if date_el else ""

        # 正文
        body_el = item.find("span", {"data-hook": "review-body"})
        if body_el:
            for btn in body_el.find_all("a"):
                btn.decompose()
            review["body"] = body_el.get_text(separator=" ", strip=True)
        else:
            review["body"] = ""

        # 有帮助
        helpful_el = item.find("span", {"data-hook": "helpful-vote-statement"})
        review["helpful"] = helpful_el.get_text(strip=True) if helpful_el else ""

        # 验证购买
        review["verified"] = bool(item.find("span", {"data-hook": "avp-badge"}))

        if review.get("body") or review.get("title"):
            reviews.append(review)

    return reviews


def _parse_summary(html: str) -> dict:
    """解析评分汇总（平均分、总数、星级分布）"""
    soup = BeautifulSoup(html, "html.parser")
    summary = {"total": 0, "avg_rating": 0.0, "star_pcts": {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}}
    try:
        # 平均分
        avg_el = soup.find(attrs={"data-hook": "rating-out-of-text"})
        if avg_el:
            m = re.search(r"([\d.]+)", avg_el.get_text())
            if m:
                summary["avg_rating"] = float(m.group(1))

        # 总数
        total_el = soup.find(attrs={"data-hook": "total-review-count"})
        if total_el:
            m = re.search(r"[\d,]+", total_el.get_text())
            if m:
                summary["total"] = int(m.group().replace(",", ""))

        # 星级分布 - Amazon 新版用 <ul id="histogramTable"> + <li> + aria-label
        # aria-label 格式: "5 stars represent 86% of rating"
        hist_ul = soup.find("ul", {"id": "histogramTable"})
        if hist_ul:
            for li in hist_ul.find_all("li"):
                a = li.find("a", attrs={"aria-label": True})
                if a:
                    label = a.get("aria-label", "")
                    ms = re.search(r"(\d) star", label)
                    mp = re.search(r"(\d+)%", label)
                    if ms and mp:
                        summary["star_pcts"][int(ms.group(1))] = int(mp.group(1))

        # 兼容旧版 <tr data-hook="rating-histogram-row">
        if all(v == 0 for v in summary["star_pcts"].values()):
            for row in soup.find_all(attrs={"data-hook": "rating-histogram-row"}):
                star_a = row.find("a", class_="a-size-base")
                pct_td = row.find("td", class_="aok-nowrap") or \
                         row.find("span", {"data-hook": "rating-percent"})
                if star_a and pct_td:
                    ms = re.search(r"(\d)", star_a.get_text())
                    mp = re.search(r"(\d+)", pct_td.get_text())
                    if ms and mp:
                        summary["star_pcts"][int(ms.group(1))] = int(mp.group(1))
    except Exception as e:
        print(f"[Amazon评论] 解析汇总失败: {e}")
    return summary


def _has_next_page(html: str) -> bool:
    """判断是否有下一页（支持 a-last 翻页和 show-more-button 加载更多两种形式）"""
    soup = BeautifulSoup(html, "html.parser")

    # 新版：Show 10 more reviews 按钮（data-hook="show-more-button"）
    show_more = soup.find("a", {"data-hook": "show-more-button"})
    if show_more:
        return True

    # 旧版：a-last 分页
    next_li = soup.find("li", class_="a-last")
    if next_li:
        if "a-disabled" in next_li.get("class", []):
            return False
        if next_li.find("a"):
            return True
    return False


# ============================================================
# 构建评论 URL
# ============================================================

FILTER_MAP = {
    "one_star":   "one_star",
    "two_star":   "two_star",
    "three_star": "three_star",
    "critical":   "critical",   # Amazon 内置：1+2星差评
    "all":        "",
}


def build_review_url(asin: str, page: int = 1, star_filter: str = "critical") -> str:
    """
    构建评论页 URL
    star_filter: one_star / two_star / three_star / critical(1-2星差评) / all
    """
    star_param = FILTER_MAP.get(star_filter, "critical")
    base = f"https://www.amazon.com/product-reviews/{asin}/ref=cm_cr_arp_d_viewopt_sr"
    params = f"?ie=UTF8&reviewerType=all_reviews&sortBy=recent&pageNumber={page}"
    if star_param:
        params += f"&filterByStar={star_param}"
    return base + params


# ============================================================
# 主抓取逻辑（异步）
# ============================================================

async def _scrape_async(
    asin: str,
    max_pages: int,
    star_filter: str,
    cdp_port: int,
) -> dict:
    ws_url = _get_cdp_tab(cdp_port)
    if not ws_url:
        return {
            "success": False,
            "asin": asin,
            "error": f"无法连接 Chrome CDP（端口 {cdp_port}）。请确保 Chrome 以 --remote-debugging-port={cdp_port} 启动。",
            "reviews": [],
            "summary": {},
        }

    _get_ws = None
    try:
        import websockets as _get_ws
    except ImportError:
        return {
            "success": False,
            "asin": asin,
            "error": "缺少 websockets 库，请运行: pip install websockets",
            "reviews": [],
            "summary": {},
        }

    all_reviews = []
    summary = {}

    async with _get_ws.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        print(f"[Amazon评论] CDP 已连接: {ws_url[:60]}...")

        for pg in range(1, max_pages + 1):
            url = build_review_url(asin, page=pg, star_filter=star_filter)
            print(f"[Amazon评论] 导航到第{pg}页 ({star_filter}): {url}")

            # 导航到评论页
            await _cdp_send(ws, "Page.navigate", {"url": url})

            # 等待页面加载完成
            await _wait_ready(ws, timeout=25)

            # 额外等待评论渲染
            review_count = await _wait_reviews(ws, timeout=15)
            print(f"[Amazon评论] 第{pg}页检测到 {review_count} 条评论")

            if review_count == 0:
                # 检查是否被重定向到验证/登录页
                current_url = await _eval(ws, "location.href")
                print(f"[Amazon评论] 当前URL: {current_url}")

                if current_url:
                    url_lower = current_url.lower()
                    if "signin" in url_lower:
                        return {
                            "success": False,
                            "asin": asin,
                            "error": "被重定向到登录页，请先在浏览器中登录 Amazon 账号",
                            "reviews": all_reviews,
                            "summary": summary,
                        }
                    # EdgeX / CAPTCHA / 机器人验证检测
                    if "edgex" in url_lower or "captcha" in url_lower or "verify" in url_lower:
                        return {
                            "success": False,
                            "asin": asin,
                            "error": (
                                "Amazon 触发了机器人验证 (EdgeX/CAPTCHA)。"
                                "请在 Chrome 中手动完成一次验证（可能需要滑块/图片验证），"
                                "然后重试。如果频繁触发，建议等待几小时后再试。"
                            ),
                            "reviews": all_reviews,
                            "summary": summary,
                        }

                print(f"[Amazon评论] 第{pg}页无评论，停止")
                break

            # 获取页面完整 HTML
            html_val = await _eval(ws, "document.documentElement.outerHTML")
            if not html_val:
                print(f"[Amazon评论] 第{pg}页获取HTML失败，跳过")
                break

            # 第1页解析汇总
            if pg == 1:
                summary = _parse_summary(html_val)
                print(f"[Amazon评论] 汇总: 总数={summary.get('total')}, 均分={summary.get('avg_rating')}")

            page_reviews = _parse_reviews(html_val)
            print(f"[Amazon评论] 第{pg}页解析到 {len(page_reviews)} 条评论")

            if not page_reviews:
                break

            all_reviews.extend(page_reviews)

            # 是否有下一页
            if not _has_next_page(html_val):
                print(f"[Amazon评论] 第{pg}页是最后一页，停止")
                break

            if pg < max_pages:
                # 随机等待，模拟人工翻页
                await asyncio.sleep(1.5 + (pg % 3) * 0.5)

    if not all_reviews and not summary:
        return {
            "success": False,
            "asin": asin,
            "error": "未能抓取到任何评论，请检查 ASIN 是否正确或该商品是否有评论",
            "reviews": [],
            "summary": {},
        }

    return {
        "success": True,
        "asin": asin,
        "summary": summary,
        "reviews": all_reviews,
        "total_fetched": len(all_reviews),
        "error": "",
    }


# ============================================================
# 公开接口（同步包装）
# ============================================================

def scrape_amazon_reviews(
    asin: str,
    max_pages: int = 10,
    star_filter: str = "critical",
    cdp_url: Optional[str] = None,
    cdp_port: int = CDP_PORT,
) -> dict:
    """
    通过 CDP 连接 Chrome 抓取 Amazon 商品评论

    :param asin:        商品 ASIN（10位）
    :param max_pages:   最大抓取页数，每页约10条，默认10页
    :param star_filter: 筛选星级 critical(1-2星)/one_star/two_star/three_star/all
    :param cdp_url:     已废弃，保留兼容（改用 cdp_port）
    :param cdp_port:    Chrome CDP 调试端口，默认 9222
    :return: {'success': bool, 'asin': str, 'summary': dict, 'reviews': list, 'error': str}
    """
    # 如果传了旧格式的 cdp_url（ws://localhost:9222/...），尝试解析端口
    if cdp_url and cdp_port == CDP_PORT:
        m = re.search(r":(\d+)/", cdp_url)
        if m:
            cdp_port = int(m.group(1))

    try:
        try:
            loop = asyncio.get_running_loop()
            # 已有事件循环（Flask 等环境）：用线程跑
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(
                    asyncio.run,
                    _scrape_async(asin, max_pages, star_filter, cdp_port)
                )
                return future.result(timeout=300)
        except RuntimeError:
            # 没有运行中的循环，直接 run
            return asyncio.run(_scrape_async(asin, max_pages, star_filter, cdp_port))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "asin": asin,
            "error": str(e)[:400],
            "reviews": [],
            "summary": {},
        }


# ============================================================
# 命令行入口
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Amazon 商品评论抓取（CDP版）")
    parser.add_argument("asin", help="商品ASIN，如 B01LR5RG08")
    parser.add_argument("--pages", type=int, default=5, help="最多抓取页数（默认5）")
    parser.add_argument("--filter", default="critical",
                        choices=["critical", "one_star", "two_star", "three_star", "all"],
                        help="筛选星级（默认critical=1-2星差评）")
    parser.add_argument("--port", type=int, default=9222, help="CDP 端口（默认9222）")
    args = parser.parse_args()

    result = scrape_amazon_reviews(args.asin, max_pages=args.pages,
                                   star_filter=args.filter, cdp_port=args.port)
    print(json.dumps(result, ensure_ascii=False, indent=2))
