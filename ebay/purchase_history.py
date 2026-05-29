"""
eBay 购买历史抓取模块
====================
通过 Playwright + cookies 获取 eBay 商品的购买历史记录
返回结构化数据：用户名、价格、数量、购买日期

支持两种模式：
  1. CDP 模式（推荐）：连接已打开的 Chrome 浏览器，复用真实登录状态
  2. Cookie 模式：通过导入的 cookies 直接访问（可能被 eBay 反爬拦截）

注意：所有 Playwright 操作使用同步 API，避免 Streamlit 事件循环冲突
"""

import json
import os
import re
import sys
import time
import random
from typing import Dict, List

from bs4 import BeautifulSoup


# ============================================================
# 日志（输出到 stderr，避免 Streamlit stdout 编码问题）
# ============================================================

def _log(msg: str):
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except Exception:
        pass


# ============================================================
# Cookie 工具
# ============================================================

def load_cookies_from_file(cookie_file: str) -> list:
    """从文件加载 cookies，返回 Playwright 格式"""
    with open(cookie_file, "r", encoding="utf-8") as f:
        cl = json.loads(f.read())

    cookies = []
    for c in cl:
        if "ebay" in c.get("domain", ""):
            cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
            }
            if c.get("secure"):
                cookie["secure"] = True
            if c.get("httpOnly"):
                cookie["httpOnly"] = True
            if c.get("expirationDate"):
                cookie["expires"] = c["expirationDate"]
            cookies.append(cookie)
    return cookies


# ============================================================
# HTML 解析
# ============================================================

def parse_purchase_table(html: str, item_id: str) -> Dict:
    """
    从购买历史页面 HTML 中解析表格数据

    真实 DOM 结构:
      .ph-main-container
        h1.ph-main-container__page-title  -> "物品购买记录"
        .app-item-card.ph
          .app-item-card__header -> 商品图片 + 标题链接
          .ui-labels-values-section -> 比较价格/立即购买价格/运费/数量/物品编号
        .ph-buy-history
          table.app-table__table
            thead -> 买家 | 价格 | 数量 | 日期
            tbody -> tr.app-table__row > td

    Returns:
        dict: {
            "item_id": "287218938334",
            "item_title": "商品标题",
            "image_url": "https://i.ebayimg.com/...",
            "compare_price": "US $7.99",
            "buy_it_now_price": "US $7.43",
            "total_purchases": 15,
            "records": [
                {"username": "6***1", "price": "US $7.43", "quantity": "1", "purchase_date": "2026-04-22 04:43:47"},
                ...
            ]
        }
    """
    soup = BeautifulSoup(html, "html.parser")

    result = {
        "item_id": item_id,
        "item_title": "",
        "image_url": "",
        "compare_price": "",
        "buy_it_now_price": "",
        "total_purchases": 0,
        "records": [],
    }

    # ---- 提取商品信息（.app-item-card.ph） ----
    item_card = soup.find("div", class_="app-item-card")
    if item_card:
        # 商品标题
        title_link = item_card.find("span", attrs={"data-testid": "app-item-card__header-item-title"})
        if title_link:
            result["item_title"] = title_link.get_text(strip=True)

        # 商品图片
        img = item_card.find("img")
        if img and img.get("src"):
            src = img["src"]
            # 小图 URL 转大图
            src = re.sub(r"/s-l\d+\.jpg", "/s-l1600.jpg", src)
            result["image_url"] = src

    # ---- 提取价格信息（.ui-labels-values-section） ----
    labels_section = soup.find("dl", attrs={"data-testid": "ui-labels-values-section__list"})
    if labels_section:
        dts = labels_section.find_all("dt")
        dds = labels_section.find_all("dd")
        for dt, dd in zip(dts, dds):
            label = dt.get_text(strip=True)
            value = dd.get_text(strip=True)
            if "比较价格" in label:
                result["compare_price"] = value
            elif "立即购买" in label:
                # 只取价格部分，去掉括号内的汇率
                price_text = re.sub(r"\(.*?\)", "", value).strip()
                result["buy_it_now_price"] = price_text

    # ---- 解析购买记录表格 ----
    table = soup.find("table", class_="app-table__table")
    if table:
        tbody = table.find("tbody")
        if tbody:
            rows = tbody.find_all("tr", class_="app-table__row")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 4:
                    continue

                username = cells[0].get_text(strip=True)
                price = cells[1].get_text(strip=True)
                quantity = cells[2].get_text(strip=True)
                raw_date = cells[3].get_text(strip=True)

                # 解析日期: "于2026 年 4 月 22 日 4:43:47 CST"
                purchase_date = raw_date
                date_match = re.search(
                    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2}:\d{2}:\d{2})",
                    raw_date,
                )
                if date_match:
                    purchase_date = (
                        f"{date_match.group(1)}-{int(date_match.group(2)):02d}"
                        f"-{int(date_match.group(3)):02d} {date_match.group(4)}"
                    )

                if "优惠折扣" in price or not price:
                    price = "优惠折扣"

                result["records"].append({
                    "username": username,
                    "price": price,
                    "quantity": quantity,
                    "purchase_date": purchase_date,
                })

    result["total_purchases"] = len(result["records"])
    return result


# ============================================================
# CDP WebSocket 工具（纯 WebSocket，不依赖 Playwright 进程）
# ============================================================

def _cdp_ws_connect(cdp_url: str):
    """获取页面的 WebSocket URL（优先找 eBay 页面 tab）"""
    import requests
    resp = requests.get(f"{cdp_url}/json", timeout=5)
    resp.raise_for_status()
    targets = resp.json()

    # 优先找 eBay 页面 tab
    ebay_ws = None
    first_ws = None
    for t in targets:
        if t.get("type") != "page":
            continue
        ws_url = t.get("webSocketDebuggerUrl")
        if not ws_url:
            continue
        if first_ws is None:
            first_ws = ws_url
        if "ebay" in t.get("url", "").lower():
            ebay_ws = ws_url
            break

    if ebay_ws:
        return ebay_ws
    if first_ws:
        return first_ws

    # fallback: 第一个有 ws 的 target
    for t in targets:
        ws_url = t.get("webSocketDebuggerUrl")
        if ws_url:
            return ws_url
    raise RuntimeError("No WebSocket target found")


# 全局递增 ID，避免 hash 碰撞
_cdp_id_counter = 0

def _cdp_send(ws, method: str, params: dict = None, timeout: int = 30) -> dict:
    """发送 CDP 命令并等待结果（只匹配自己 msg_id 的响应）"""
    import json
    global _cdp_id_counter
    _cdp_id_counter += 1
    msg_id = _cdp_id_counter
    cmd = {"id": msg_id, "method": method}
    if params:
        cmd["params"] = params
    ws.send(json.dumps(cmd))

    ws.settimeout(timeout)
    while True:
        raw = ws.recv()
        data = json.loads(raw)
        if data.get("id") == msg_id:
            return data
        # 忽略其他消息（事件、其他命令的响应）


def _cdp_get_cookies(ws) -> List[Dict]:
    """通过 CDP 获取所有 cookies"""
    result = _cdp_send(ws, "Network.getAllCookies")
    cookies = result.get("result", {}).get("cookies", [])
    # 过滤 eBay cookies 并转为 dict
    cookie_dict = {}
    for c in cookies:
        if "ebay" in c.get("domain", ""):
            cookie_dict[c["name"]] = c["value"]
    return cookie_dict


def _cdp_navigate_and_wait(ws, url: str, page_timeout: int = 30,
                            anti_bot_wait: bool = True, retries: int = 5) -> Dict:
    """
    导航到 URL 并等待购买历史页面加载完成，支持多次重试。

    eBay SPA 页面导航后可能不会立即渲染出购买历史内容，
    原因包括：用户手动切换了 tab、页面渲染延迟、反爬拦截等。
    通过多次重试 + 足够的等待时间来提高成功率。
    """
    import json

    last_html = ""
    last_url = url

    for attempt in range(retries):
        # 导航前随机等待（模拟人类操作间隔）
        if anti_bot_wait:
            wait_before = random.uniform(2.0, 5.0)
            _log(f"  [{attempt+1}/{retries}] 等待 {wait_before:.1f}s 后导航...")
            time.sleep(wait_before)
        else:
            _log(f"  [{attempt+1}/{retries}] 导航中...")

        # 发起导航（使用 CDP Page.navigate）
        _cdp_send(ws, "Page.enable")

        # 第 2 次及之后重试时，先用 JS 强制导航，确保 SPA 正常跳转
        if attempt >= 1:
            _log(f"  尝试 JS 强制导航...")
            try:
                _cdp_send(ws, "Runtime.evaluate", {
                    "expression": f"window.location.href = '{url}';",
                    "returnByValue": True,
                }, timeout=10)
                time.sleep(1.0)
            except Exception:
                pass

        nav_result = _cdp_send(ws, "Page.navigate", {"url": url}, timeout=page_timeout)
        frame_id = nav_result.get("result", {}).get("frameId")

        if not frame_id:
            # CDP navigate 失败，用 requests 兜底
            import requests as req
            r = req.get(url, timeout=page_timeout)
            return {"html": r.text, "status": r.status_code, "url": r.url}

        # 轮询等待 document.readyState == "complete"
        for _ in range(page_timeout * 2):
            time.sleep(0.5)
            try:
                ready_result = _cdp_send(ws, "Runtime.evaluate", {
                    "expression": "document.readyState",
                    "returnByValue": True,
                }, timeout=5)
                ready_val = ready_result.get("result", {}).get("result", {}).get("value")
                if ready_val == "complete":
                    break
            except Exception:
                pass

        # 额外等待 JS 渲染（eBay SPA 页面渲染需要较长时间）
        js_wait = random.uniform(3.0, 6.0)
        _log(f"  等待 JS 渲染 {js_wait:.1f}s...")
        time.sleep(js_wait)

        # 获取 HTML
        doc_result = _cdp_send(ws, "Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML",
            "returnByValue": True,
        }, timeout=30)
        html = doc_result.get("result", {}).get("result", {}).get("value", "")
        last_html = html

        # 获取最终 URL
        url_result = _cdp_send(ws, "Runtime.evaluate", {
            "expression": "window.location.href",
            "returnByValue": True,
        }, timeout=10)
        final_url = url_result.get("result", {}).get("result", {}).get("value", url)
        last_url = final_url

        # 检查登录状态
        if "signin" in final_url.lower():
            _log(f"  检测到登录页，停止重试")
            return {"html": html, "status": 200, "url": final_url, "login_expired": True}

        # 检查是否已渲染出购买历史内容
        if "ph-main-container" in html:
            # 验证页面确实是目标商品的页面（而不是上一个商品的残留）
            # 从目标 URL 提取期望的 item ID
            expected_item = ""
            url_match = re.search(r"item=(\d+)", url)
            if url_match:
                expected_item = url_match.group(1)
            if expected_item:
                # 检查页面中是否包含当前 item ID（eBay 会在页面中嵌入 item ID）
                if expected_item not in html:
                    _log(f"  ⚠️ 页面内容与目标商品不匹配（期望 item={expected_item}），继续重试")
                    if attempt < retries - 1:
                        # 下一次重试时强制刷新（清除缓存）
                        retry_wait = random.uniform(2.0, 4.0)
                        _log(f"  等待 {retry_wait:.1f}s 后强制刷新...")
                        time.sleep(retry_wait)
                        continue
            _log(f"  ✅ 购买历史页面加载成功（第 {attempt+1} 次尝试）")
            return {"html": html, "status": 200, "url": final_url}

        # 未检测到，准备重试
        _log(f"  ⚠️ 未检测到购买历史内容 (url={final_url[:60]}...)")
        if attempt < retries - 1:
            retry_wait = random.uniform(3.0, 6.0)
            _log(f"  等待 {retry_wait:.1f}s 后重试...")
            time.sleep(retry_wait)

    # 重试耗尽
    _log(f"  ❌ {retries} 次尝试均未检测到购买历史内容")
    return {"html": last_html, "status": 200, "url": last_url}

def _cdp_scroll_load(ws, max_sales: int = 20) -> str:
    """
    在当前页面（购买历史页）滚动到底部，等待动态加载更多记录，返回完整 HTML。

    eBay 购买历史页默认只显示 15 条，需要滚动触发加载更多。
    """
    # 先获取当前表格行数
    def _get_row_count():
        r = _cdp_send(ws, "Runtime.evaluate", {
            "expression": "document.querySelectorAll('table.app-table__table tbody tr.app-table__row').length",
            "returnByValue": True,
        }, timeout=10)
        val = r.get("result", {}).get("result", {}).get("value")
        return int(val) if val is not None else 0

    def _get_full_html():
        r = _cdp_send(ws, "Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML",
            "returnByValue": True,
        }, timeout=30)
        return r.get("result", {}).get("result", {}).get("value", "")

    initial_count = _get_row_count()
    _log(f"  初始加载 {initial_count} 条记录")

    # 如果已超过阈值，不需要滚动
    if initial_count >= max_sales:
        _log(f"  已达阈值 {max_sales}，无需滚动")
        return _get_full_html()

    # 滚动到底部，触发加载更多
    max_scroll_attempts = 5  # 最多滚动 5 次
    scroll_wait = random.uniform(2.0, 4.0)

    for attempt in range(max_scroll_attempts):
        current_count = _get_row_count()
        if current_count >= max_sales:
            _log(f"  已达阈值 {max_sales}，停止滚动")
            break

        # 滚动到表格底部
        _cdp_send(ws, "Runtime.evaluate", {
            "expression": """
                var table = document.querySelector('table.app-table__table');
                if (table) { table.scrollIntoView(false); window.scrollBy(0, 800); }
                else { window.scrollTo(0, document.body.scrollHeight); }
            """,
            "returnByValue": True,
        }, timeout=10)

        _log(f"  滚动第 {attempt+1} 次...等待 {scroll_wait:.1f}s")
        time.sleep(scroll_wait)

        new_count = _get_row_count()
        if new_count > current_count:
            _log(f"  加载更多: {current_count} → {new_count} 条")
        else:
            _log(f"  无新记录加载，停止滚动")
            break

        scroll_wait = random.uniform(2.0, 3.5)

    final_count = _get_row_count()
    _log(f"  最终: {final_count} 条记录")
    return _get_full_html()


def _cdp_ensure_connected(cdp_url: str, ws=None):
    """
    确保有一个可用的 WebSocket 连接。如果 ws 已断开或为 None，则重新连接。
    """
    import websocket

    # 检查现有连接是否可用
    if ws is not None:
        try:
            # 发一个 ping 检测连接
            ws.send('{"id":0,"method":"Runtime.evaluate","params":{"expression":"1","returnByValue":true}}')
            ws.settimeout(5)
            resp = json.loads(ws.recv())
            if resp.get("id") == 0:
                return ws
        except Exception:
            _log("  WebSocket 连接已断开，正在重新连接...")
            try:
                ws.close()
            except Exception:
                pass

    # 新建连接
    ws_url = _cdp_ws_connect(cdp_url)
    ws = websocket.create_connection(ws_url, timeout=60)
    _log(f"  WebSocket 已连接: {ws_url[:60]}...")
    return ws


def _batch_fetch_cdp(cdp_url: str, item_ids: List[str], max_sales: int = 20,
                      on_progress=None) -> List[Dict]:
    """
    通过 CDP WebSocket 连接 Chrome 浏览器批量获取购买历史

    Args:
        cdp_url: Chrome CDP 地址
        item_ids: 商品 ID 列表
        max_sales: 如果某个商品销量 >= 此值，直接记录为该值，不再继续翻页解析（默认 20）
        on_progress: 回调函数 on_progress(current, total, result_dict)，每完成一个商品调用
                     result_dict 包含 item_id, item_title, total_purchases, error 等字段
    """
    import websocket

    ws = _cdp_ensure_connected(cdp_url, ws=None)

    results = []
    try:
        for i, item_id in enumerate(item_ids):
            item_id = item_id.strip()
            if not item_id:
                continue
            _log(f"[{i+1}/{len(item_ids)}] {item_id}...")

            # 每个 item 查询前检查连接是否正常
            try:
                ws = _cdp_ensure_connected(cdp_url, ws)
            except Exception as conn_err:
                _log(f"  无法建立连接: {conn_err}")
                err_result = {
                    "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                    "total_purchases": 0, "records": [], "error": f"connection failed: {conn_err}",
                }
                results.append(err_result)
                if on_progress:
                    try:
                        on_progress(i + 1, len(item_ids), err_result)
                    except Exception:
                        pass
                continue

            try:
                url = f"https://www.ebay.com/bin/purchaseHistory?item={item_id}"
                page_data = _cdp_navigate_and_wait(ws, url, anti_bot_wait=True)

                # 检查登录状态
                if page_data.get("login_expired") or "signin" in page_data.get("url", "").lower():
                    results.append({
                        "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                        "total_purchases": 0, "records": [],
                        "error": "login expired",
                    })
                    _log(f"  ERROR: login expired")
                    if on_progress:
                        try:
                            on_progress(i + 1, len(item_ids), results[-1])
                        except Exception:
                            pass
                    continue

                # 检查是否确实是购买历史页面
                html = page_data.get("html", "")
                if "ph-main-container" not in html:
                    results.append({
                        "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                        "total_purchases": 0, "records": [],
                        "error": "not purchase history page",
                    })
                    _log(f"  ERROR: not purchase history page (url={page_data.get('url','')[:60]})")
                    if on_progress:
                        try:
                            on_progress(i + 1, len(item_ids), results[-1])
                        except Exception:
                            pass
                    continue

                # 滚动加载更多记录
                html = _cdp_scroll_load(ws, max_sales)
                result = parse_purchase_table(html, item_id)

                # 收集调试信息：帮助排查销量为 0 的原因
                if result["total_purchases"] == 0:
                    debug_info = []
                    if "ph-main-container" not in html:
                        debug_info.append("页面缺少 ph-main-container 容器")
                    table_tag = "app-table__table" in html
                    if table_tag:
                        debug_info.append("HTML 有 table.app-table__table 标签")
                    else:
                        debug_info.append("HTML 无 table.app-table__table 标签")
                    tbody_rows = len(re.findall(r'app-table__row', html))
                    debug_info.append(f"app-table__row 数量: {tbody_rows}")
                    # 检查页面中的 item_id 是否匹配
                    if item_id not in html:
                        debug_info.append(f"页面 HTML 中不包含目标 item_id={item_id}")
                    else:
                        debug_info.append(f"页面 HTML 包含目标 item_id={item_id}")
                    # 检查是否命中反爬
                    if "captcha" in html.lower() or "challenge" in html.lower():
                        debug_info.append("⚠️ 页面包含验证码/挑战页面")
                    result["debug"] = " | ".join(debug_info)

                if result["total_purchases"] == 0 and "purchaseHistory" not in html:
                    result["error"] = "no purchase records found"

                # 如果销量已达到阈值，标记为截断
                if result["total_purchases"] >= max_sales:
                    result["truncated_at"] = max_sales
                    _log(f"  OK: {result['item_title'][:40]} | {result['total_purchases']} records (达到 {max_sales} 条阈值，停止)")

                results.append(result)
                if result.get("total_purchases", 0) > 0:
                    _log(f"  OK: {result['item_title'][:40]} | {result['total_purchases']} records")
                elif result.get("error"):
                    _log(f"  ERROR: {result['error']}")

                # 回调通知进度
                if on_progress:
                    try:
                        on_progress(i + 1, len(item_ids), result)
                    except Exception:
                        pass

                # 随机延时（3-7 秒），模拟真人浏览间隔
                if i < len(item_ids) - 1:
                    delay = random.uniform(3.0, 7.0)
                    _log(f"  等待 {delay:.1f}s...")
                    time.sleep(delay)
            except Exception as e:
                err_result = {
                    "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                    "total_purchases": 0, "records": [], "error": str(e),
                }
                results.append(err_result)
                _log(f"  ERROR: {e}")
                # 回调通知进度
                if on_progress:
                    try:
                        on_progress(i + 1, len(item_ids), err_result)
                    except Exception:
                        pass
    finally:
        try:
            ws.close()
        except Exception:
            pass

    return results


# ============================================================
# Cookie 模式（备用，直接用 requests + cookies）
# ============================================================

def _batch_fetch_cookies(item_ids: List[str], cookie_file: str) -> List[Dict]:
    """通过 cookies 文件批量获取（使用 requests，无 Playwright）"""
    import requests as req

    cookies = load_cookies_from_file(cookie_file)
    # 构建 cookie dict
    cookie_dict = {c["name"]: c["value"] for c in cookies}

    session = req.Session()
    for n, v in cookie_dict.items():
        session.cookies.set(n, v, domain=".ebay.com")
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    results = []
    for i, item_id in enumerate(item_ids):
        item_id = item_id.strip()
        if not item_id:
            continue
        _log(f"[{i+1}/{len(item_ids)}] {item_id}...")
        try:
            url = f"https://www.ebay.com/bin/purchaseHistory?item={item_id}"
            resp = session.get(url, timeout=30, allow_redirects=True)

            if resp.status_code != 200:
                results.append({
                    "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                    "total_purchases": 0, "records": [],
                    "error": f"HTTP {resp.status_code}",
                })
                _log(f"  ERROR: HTTP {resp.status_code}")
                continue

            if "signin" in resp.url.lower():
                results.append({
                    "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                    "total_purchases": 0, "records": [],
                    "error": "login expired or captcha",
                })
                _log(f"  ERROR: login expired or captcha")
                continue

            result = parse_purchase_table(resp.text, item_id)
            if result["total_purchases"] == 0:
                result["error"] = "no purchase records found"

            results.append(result)
            if result.get("total_purchases", 0) > 0:
                _log(f"  OK: {result['item_title'][:40]} | {result['total_purchases']} records")
            elif result.get("error"):
                _log(f"  ERROR: {result['error']}")

            if i < len(item_ids) - 1:
                time.sleep(2 + __import__("random").random())
        except Exception as e:
            results.append({
                "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                "total_purchases": 0, "records": [], "error": str(e),
            })
            _log(f"  ERROR: {e}")

    return results


# ============================================================
# 同步入口
# ============================================================

def batch_fetch_purchase_history(
    item_ids,
    cookie_file: str = None,
    cdp_url: str = None,
    max_sales: int = 20,
    on_progress=None,
) -> List[Dict]:
    """
    批量获取购买历史（同步入口）

    Args:
        item_ids: 商品 ID 列表（逗号/空格/换行分隔的字符串或列表）
        cookie_file: cookies 文件路径，默认 purchase_cookies.json
        cdp_url: Chrome CDP 地址（如 http://localhost:9222），优先使用
        max_sales: 销量达到此值时标记为截断，默认 20
        on_progress: 回调函数 on_progress(current, total, result_dict)，每完成一个商品调用

    Returns:
        list: 购买历史数据列表
    """
    # 参数归一化
    if isinstance(item_ids, str):
        item_ids = re.split(r"[,\s\n]+", item_ids)
    item_ids = [x.strip() for x in item_ids if x.strip()]

    if not item_ids:
        return []

    # 优先使用 CDP 模式
    if cdp_url:
        return _batch_fetch_cdp(cdp_url, item_ids, max_sales=max_sales,
                                on_progress=on_progress)

    # Cookie 模式
    if not cookie_file:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cookie_file = os.path.join(script_dir, "purchase_cookies.json")

    if not os.path.exists(cookie_file):
        raise FileNotFoundError(f"Cookie file not found: {cookie_file}")

    return _batch_fetch_cookies(item_ids, cookie_file)


def purchase_history_to_csv_bytes(all_results: List[Dict]) -> bytes:
    """将购买历史数据转为 CSV bytes"""
    import csv as csv_mod
    import io as io_mod

    output = io_mod.StringIO()
    fieldnames = [
        "item_id", "item_title", "buy_it_now_price", "total_purchases",
        "username", "price", "quantity", "purchase_date",
    ]
    writer = csv_mod.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for result in all_results:
        for record in result.get("records", []):
            row = {
                "item_id": result.get("item_id", ""),
                "item_title": result.get("item_title", ""),
                "buy_it_now_price": result.get("buy_it_now_price", ""),
                "total_purchases": result.get("total_purchases", 0),
                "username": record.get("username", ""),
                "price": record.get("price", ""),
                "quantity": record.get("quantity", ""),
                "purchase_date": record.get("purchase_date", ""),
            }
            writer.writerow(row)

    return output.getvalue().encode("utf-8-sig")


# ============================================================
# 滚动加载（支持截止日期自动停止）
# ============================================================

def _cdp_scroll_load_with_cutoff(
    ws,
    max_sales: int = 20,
    cutoff_date: "datetime" = None,
) -> tuple[str, bool]:
    """
    在当前页面滚动加载购买记录，遇到超出 cutoff_date 的记录时立即停止。

    Returns:
        (html, stopped_early): HTML 字符串 和 是否因超截止日期提前停止
    """
    import re as re_mod

    def _get_rows_info():
        """返回 (row_count, oldest_date_str)"""
        r = _cdp_send(ws, "Runtime.evaluate", {
            "expression": """
                (function() {
                    var rows = document.querySelectorAll(
                        'table.app-table__table tbody tr.app-table__row'
                    );
                    var count = rows.length;
                    var lastDate = '';
                    if (rows.length > 0) {
                        var cells = rows[rows.length - 1].querySelectorAll('td');
                        if (cells.length >= 4) lastDate = cells[3].innerText.trim();
                    }
                    return { count: count, oldest: lastDate };
                })()
            """,
            "returnByValue": True,
        }, timeout=10)
        val = r.get("result", {}).get("result", {}).get("value", {})
        if isinstance(val, dict):
            return int(val.get("count", 0)), val.get("oldest", "")
        return 0, ""

    def _get_full_html():
        r = _cdp_send(ws, "Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML",
            "returnByValue": True,
        }, timeout=30)
        return r.get("result", {}).get("result", {}).get("value", "")

    def _parse_date_from_ebay_text(text: str):
        """解析 eBay 页面中的日期文本，返回 datetime 对象"""
        m = re_mod.search(
            r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2}:\d{2}:\d{2})",
            text,
        )
        if m:
            from datetime import datetime as dt
            return dt(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
                *[int(x) for x in m.group(4).split(":")],
            )
        return None

    initial_count, initial_oldest = _get_rows_info()
    _log(f"  初始 {initial_count} 条，最早: {initial_oldest}")

    # 快速判断：初始加载的第一条是否已超出截止日期
    if cutoff_date and initial_count > 0:
        oldest_dt = _parse_date_from_ebay_text(initial_oldest)
        if oldest_dt and oldest_dt < cutoff_date:
            _log(f"  ✅ 第一批记录已超出截止日期，无需滚动")
            return _get_full_html(), True

    if initial_count >= max_sales:
        return _get_full_html(), False

    scroll_wait_base = 2.0
    max_scroll_attempts = 6

    for attempt in range(max_scroll_attempts):
        current_count, _ = _get_rows_info()
        if current_count >= max_sales:
            _log(f"  达到上限 {max_sales}，停止")
            break

        # 滚动到表格底部
        _cdp_send(ws, "Runtime.evaluate", {
            "expression": """
                var table = document.querySelector('table.app-table__table');
                if (table) { table.scrollIntoView(false); window.scrollBy(0, 800); }
                else { window.scrollTo(0, document.body.scrollHeight); }
            """,
            "returnByValue": True,
        }, timeout=10)

        wait = random.uniform(scroll_wait_base, scroll_wait_base + 2.0)
        _log(f"  滚动 {attempt+1} 次...等待 {wait:.1f}s")
        time.sleep(wait)

        new_count, new_oldest = _get_rows_info()
        if new_count > current_count:
            _log(f"  加载更多: {current_count} → {new_count}")
        else:
            _log(f"  无新记录，停止")
            break

        # 截止日期判断：若最新滚入的记录超出截止日期，立即停止
        if cutoff_date:
            oldest_dt = _parse_date_from_ebay_text(new_oldest)
            if oldest_dt and oldest_dt < cutoff_date:
                _log(f"  ✅ 最旧记录 {new_oldest} 已超出截止日期，提前停止")
                return _get_full_html(), True

        scroll_wait_base = 2.0

    return _get_full_html(), False


# ============================================================
# 近 N 天专用抓取入口
# ============================================================

def batch_fetch_purchase_history_30d(
    item_ids,
    days: int = 30,
    cookie_file: str = None,
    cdp_url: str = None,
    max_sales: int = 20,
    on_progress=None,
) -> list[dict]:
    """
    批量获取近 N 天购买记录（只抓够截止日期为止的记录，滚动时遇超期即停）。

    Args:
        item_ids: 商品 ID 列表（逗号/空格/换行分隔的字符串或列表）
        days: 截止天数，默认 30
        cookie_file: cookies 文件路径，默认 purchase_cookies.json
        cdp_url: Chrome CDP 地址（如 http://localhost:9222），优先使用
        max_sales: 最大抓取条数（从最新往旧），默认 20
        on_progress: 回调 on_progress(current, total, result_dict)

    Returns:
        list: 每个商品 dict，含 truncated_early 字段标记是否提前停止
    """
    from datetime import datetime as dt
    cutoff = dt.now() - __import__("datetime").timedelta(days=days)

    def _batch_fetch_cdp_30d(cdp_url: str, item_ids: list, cutoff: "dt", max_sales: int, on_progress):
        import websocket as ws_mod

        ws = _cdp_ensure_connected(cdp_url, ws=None)
        results = []
        try:
            for i, item_id in enumerate(item_ids):
                item_id = item_id.strip()
                if not item_id:
                    continue
                _log(f"[{i+1}/{len(item_ids)}] {item_id}...")

                try:
                    ws = _cdp_ensure_connected(cdp_url, ws)
                except Exception as conn_err:
                    err_result = {
                        "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                        "total_purchases": 0, "records": [],
                        "error": f"connection failed: {conn_err}",
                        "truncated_early": False,
                    }
                    results.append(err_result)
                    if on_progress:
                        try:
                            on_progress(i + 1, len(item_ids), err_result)
                        except Exception:
                            pass
                    continue

                try:
                    url = f"https://www.ebay.com/bin/purchaseHistory?item={item_id}"
                    page_data = _cdp_navigate_and_wait(ws, url, anti_bot_wait=True)

                    if page_data.get("login_expired") or "signin" in page_data.get("url", "").lower():
                        results.append({
                            "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                            "total_purchases": 0, "records": [],
                            "error": "login expired",
                            "truncated_early": False,
                        })
                        _log(f"  ERROR: login expired")
                        if on_progress:
                            try:
                                on_progress(i + 1, len(item_ids), results[-1])
                            except Exception:
                                pass
                        continue

                    html = page_data.get("html", "")
                    if "ph-main-container" not in html:
                        results.append({
                            "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                            "total_purchases": 0, "records": [],
                            "error": "not purchase history page",
                            "truncated_early": False,
                        })
                        if on_progress:
                            try:
                                on_progress(i + 1, len(item_ids), results[-1])
                            except Exception:
                                pass
                        continue

                    # 滚动加载，遇超截止日期自动停止
                    html, stopped_early = _cdp_scroll_load_with_cutoff(
                        ws, max_sales=max_sales, cutoff_date=cutoff
                    )
                    result = parse_purchase_table(html, item_id)
                    result["truncated_early"] = stopped_early

                    if result["total_purchases"] >= max_sales:
                        result["truncated_at"] = max_sales
                        _log(f"  OK: {result['item_title'][:40]} | {result['total_purchases']} records (达到上限 {max_sales})")
                    elif stopped_early:
                        _log(f"  OK: {result['item_title'][:40]} | {result['total_purchases']} records (超截止日期，提前停止)")
                    elif result.get("total_purchases", 0) > 0:
                        _log(f"  OK: {result['item_title'][:40]} | {result['total_purchases']} records")
                    elif result.get("error"):
                        _log(f"  ERROR: {result['error']}")
                    else:
                        _log(f"  0 records")

                    results.append(result)
                    if on_progress:
                        try:
                            on_progress(i + 1, len(item_ids), result)
                        except Exception:
                            pass

                    if i < len(item_ids) - 1:
                        delay = random.uniform(3.0, 7.0)
                        _log(f"  等待 {delay:.1f}s...")
                        time.sleep(delay)

                except Exception as e:
                    err_result = {
                        "item_id": item_id, "item_title": "", "buy_it_now_price": "",
                        "total_purchases": 0, "records": [],
                        "error": str(e),
                        "truncated_early": False,
                    }
                    results.append(err_result)
                    _log(f"  ERROR: {e}")
                    if on_progress:
                        try:
                            on_progress(i + 1, len(item_ids), err_result)
                        except Exception:
                            pass
        finally:
            try:
                ws.close()
            except Exception:
                pass
        return results

    # 参数归一化
    if isinstance(item_ids, str):
        item_ids = re.split(r"[,\s\n]+", item_ids)
    item_ids = [x.strip() for x in item_ids if x.strip()]
    if not item_ids:
        return []

    if cdp_url:
        return _batch_fetch_cdp_30d(cdp_url, item_ids, cutoff, max_sales, on_progress)

    # Cookie 模式
    if not cookie_file:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cookie_file = os.path.join(script_dir, "purchase_cookies.json")
    if not os.path.exists(cookie_file):
        raise FileNotFoundError(f"Cookie file not found: {cookie_file}")

    results = _batch_fetch_cookies(item_ids, cookie_file)
    for r in results:
        r["truncated_early"] = False
    return results


# ============================================================
# 命令行入口
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="eBay purchase history scraper")
    parser.add_argument("item_ids", nargs="+", help="item ID list")
    parser.add_argument("--cookie-file", default=None, help="cookie file path")
    parser.add_argument("--cdp", default=None, help="Chrome CDP URL (recommended, e.g. http://localhost:9222)")
    args = parser.parse_args()

    results = batch_fetch_purchase_history(args.item_ids, args.cookie_file, args.cdp)

    for r in results:
        item_id = r["item_id"]
        title = r.get("item_title", "N/A")[:50]
        count = r.get("total_purchases", 0)
        error = r.get("error", "")
        if error:
            _log(f"[{item_id}] ERROR: {error}")
        else:
            _log(f"[{item_id}] {title} | {count} records")
            for rec in r["records"][:3]:
                _log(f"  {rec['username']} | {rec['price']} | x{rec['quantity']} | {rec['purchase_date']}")
            if count > 3:
                _log(f"  ... +{count - 3} more")
