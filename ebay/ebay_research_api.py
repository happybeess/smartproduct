
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

def _log(msg=""):
    """Safe print alternative to avoid Streamlit stdout pipe OSError"""
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except Exception:
        pass

# 修复 Windows 控制台编码
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

API_URL = "https://www.ebay.com/sh/research/api/search"


def load_cookies_cdp(cdp_url: str) -> Dict[str, str]:
    """通过 CDP HTTP 协议从已登录的 Chrome 浏览器提取 eBay cookies。"""
    import hashlib

    r = requests.get(f"{cdp_url}/json", timeout=5)
    r.raise_for_status()
    targets = r.json()

    ebay_target = None
    for t in targets:
        url = t.get("url", "")
        if "ebay.com" in url and t.get("type") == "page":
            ebay_target = t
            break

    if not ebay_target:
        for t in targets:
            if t.get("type") == "page":
                ebay_target = t
                break

    if not ebay_target:
        raise RuntimeError("未找到可用的浏览器页面，请确认 Chrome 已打开并访问了 eBay")

    ws_url = ebay_target["webSocketDebuggerUrl"]
    
    try:
        import websocket
    except ImportError:
        raise ImportError(
            "需要 websocket-client 库: pip install websocket-client\n"
            "(注意是 websocket-client，不是 websockets)"
        )

    ws = websocket.create_connection(ws_url, timeout=10)

    try:
        cmd_id = 1
        ws.send(json.dumps({
            "id": cmd_id,
            "method": "Network.getAllCookies",
            "params": {}
        }))

        response = json.loads(ws.recv())

        if "error" in response:
            raise RuntimeError(f"CDP 命令失败: {response['error']}")

        cookies = response.get("result", {}).get("cookies", [])
        
        cookie_dict = {}
        for c in cookies:
            domain = c.get("domain", "")
            if "ebay" in domain:
                cookie_dict[c["name"]] = c["value"]

        if not cookie_dict:
            raise RuntimeError(
                "CDP 连接成功但未找到 eBay cookies。\n"
                "请确认浏览器已登录 eBay。"
            )

        return cookie_dict

    finally:
        ws.close()


def extract_text(td) -> str:
    """从 eBay TextualDisplay 对象中提取纯文本，修复编码问题"""
    text = ''
    if isinstance(td, str):
        text = td
    elif isinstance(td, (int, float)):
        text = str(td)
    elif isinstance(td, dict):
        if 'text' in td:
            text = td.get('text', '')
        elif 'value' in td:
            text = td.get('value', '')
        else:
            spans = td.get('textSpans', [])
            text = ''.join(s.get('text', '') for s in spans if isinstance(s, dict))
    
    if text:
        try:
            if any(ord(c) > 127 for c in text[:20]):
                text = text.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    
    return text.strip()


def load_cookies(path: str) -> Dict[str, str]:
    """加载 cookies (支持 Cookie-Editor JSON 格式)"""
    with open(path, 'r', encoding='utf-8') as f:
        cl = json.loads(f.read())
    cookies = {}
    for c in cl:
        if c.get('domain', '').endswith('ebay.com'):
            cookies[c['name']] = c['value']
    return cookies


def fetch_research(
    keywords: str,
    cookies: Dict[str, str],
    marketplace: str = "EBAY-US",
    category_id: str = "0",
    day_range: int = 1095,
    limit: int = 50,
    tab_name: str = "SOLD",
    tz: str = "Asia/Shanghai",
    offset: int = 0,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    listing_format: Optional[str] = None,
    condition: Optional[str] = None,
    start_date: Optional[int] = None,
    end_date: Optional[int] = None,
) -> dict:
    """调用 eBay Research 内部 API"""
    s = requests.Session()
    for n, v in cookies.items():
        s.cookies.set(n, v, domain='.ebay.com')
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.ebay.com/sh/research',
        'X-EBAY-C-MARKETPLACE-ID': marketplace,
    })

    params = [
        ('marketplace', marketplace),
        ('keywords', keywords),
        ('dayRange', str(day_range)),
        ('categoryId', category_id),
        ('offset', str(offset)),
        ('limit', str(limit)),
        ('tabName', tab_name),
        ('tz', tz),
        ('modules', 'aggregates'),
        ('modules', 'searchResults'),
        ('modules', 'resultsHeader'),
    ]
    
    if price_min is not None:
        params.append(('priceMin', str(price_min)))
    if price_max is not None:
        params.append(('priceMax', str(price_max)))
    if listing_format:
        params.append(('listingFormat', listing_format))
    if condition:
        params.append(('condition', condition))
    if start_date is not None:
        params.append(('startDate', str(start_date)))
    if end_date is not None:
        params.append(('endDate', str(end_date)))

    resp = s.get(API_URL, params=params, timeout=30)
    
    if resp.encoding.lower() != 'utf-8':
        resp.encoding = 'utf-8'

    modules = []
    for chunk in resp.text.split('\n\n'):
        chunk = chunk.strip()
        if chunk:
            try:
                modules.append(json.loads(chunk))
            except json.JSONDecodeError:
                pass

    return {
        'status': resp.status_code,
        'modules': modules,
        'raw_text': resp.text,
    }


def parse_items(api_response: dict) -> List[Dict]:
    """解析 API 响应中的商品数据"""
    modules = api_response.get('modules', [])
    items = []

    search_module = None
    for m in modules:
        if m.get('_type') in ('SearchResultsModule', 'ActiveSearchResultsModule'):
            search_module = m
            break

    if not search_module:
        return items

    results = search_module.get('results', [])

    for row in results:
        item = {}
        listing = row.get('listing', {})

        item_id_obj = listing.get('itemId', {})
        item['item_id'] = item_id_obj.get('value', '') if isinstance(item_id_obj, dict) else ''

        item['title'] = extract_text(listing.get('title', ''))

        ext_title = listing.get('extendedTitle', {})
        item['extended_title'] = ext_title.get('value', '') if isinstance(ext_title, dict) else ''

        title_obj = listing.get('title', {})
        if isinstance(title_obj, dict):
            action = title_obj.get('action', {})
            item['url'] = action.get('URL', '') if isinstance(action, dict) else ''
        else:
            item['url'] = ''

        image = listing.get('image', {})
        item['image_url'] = ''
        if isinstance(image, dict):
            img_url = image.get('URL', '')
            if img_url and not img_url.startswith('http'):
                img_url = 'https:' + img_url
            item['image_url'] = img_url

        format_list = listing.get('formatList', [])
        if isinstance(format_list, list) and format_list:
            item['listing_format'] = extract_text(format_list[0])
        else:
            item['listing_format'] = ''

        is_sold = 'avgsalesprice' in row or 'itemssold' in row
        
        if is_sold:
            avg_price_obj = row.get('avgsalesprice', {})
            if isinstance(avg_price_obj, dict):
                item['avg_sold_price'] = extract_text(avg_price_obj.get('avgsalesprice', ''))
                item['avg_shipping'] = extract_text(avg_price_obj.get('averageshipping', ''))
                item['price_format'] = extract_text(avg_price_obj.get('format', ''))
            else:
                item['avg_sold_price'] = ''
                item['avg_shipping'] = ''
                item['price_format'] = ''

            item['sold_count'] = extract_text(row.get('itemssold', ''))
            item['total_sales'] = extract_text(row.get('totalsales', ''))
            item['last_sold_date'] = extract_text(row.get('datelastsold', ''))
            item['current_price'] = ''
            item['watchers'] = ''
        else:
            listing_price = row.get('listingPrice', {})
            if isinstance(listing_price, dict):
                item['current_price'] = extract_text(listing_price.get('listingPrice', ''))
                item['avg_shipping'] = extract_text(listing_price.get('listingShipping', ''))
            else:
                item['current_price'] = ''
                item['avg_shipping'] = ''
            
            item['avg_sold_price'] = ''
            item['price_format'] = ''
            item['sold_count'] = ''
            item['total_sales'] = ''
            item['last_sold_date'] = extract_text(row.get('startDate', ''))
            item['watchers'] = extract_text(row.get('watchers', ''))

        item['bids'] = extract_text(row.get('bids', ''))

        items.append(item)

    return items


def parse_aggregates(api_response: dict) -> dict:
    """解析汇总数据"""
    modules = api_response.get('modules', [])
    for m in modules:
        if m.get('_type') == 'ResearchAggregateModule':
            sections = m.get('sections', [])
            result = {}
            for section in sections:
                data_items = section.get('dataItems', [])
                for di in data_items:
                    label = extract_text(di.get('label', ''))
                    value = extract_text(di.get('value', ''))
                    if label:
                        result[label] = value
            return result
    return {}


def fetch_research_via_cdp(
    keywords: str,
    cdp_url: str = "http://localhost:9222",
    marketplace: str = "EBAY-US",
    category_id: str = "0",
    day_range: int = 180,
    tab_name: str = "SOLD",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """通过 CDP 在已登录的浏览器中用 JS fetch 调用 eBay Research API

    原理：找到已打开 eBay 页面的 Chrome tab，
    用 Runtime.evaluate 执行 fetch() 调用 Research API，
    浏览器自动携带 cookie，直接拿到 JSON 数据。
    """
    import websocket

    r = requests.get(f"{cdp_url}/json", timeout=5)
    r.raise_for_status()
    targets = r.json()

    target = None
    for t in targets:
        if "ebay.com" in t.get("url", "") and t.get("type") == "page":
            target = t
            break

    if not target:
        for t in targets:
            if t.get("type") == "page":
                target = t
                break

    if not target:
        raise RuntimeError("未找到可用的浏览器页面")

    ws_url = target["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, timeout=30)

    try:
        cmd_id = 0

        def send_cmd(method, params=None):
            nonlocal cmd_id
            cmd_id += 1
            msg = {"id": cmd_id, "method": method}
            if params:
                msg["params"] = params
            ws.send(json.dumps(msg))
            while True:
                resp = json.loads(ws.recv())
                if resp.get("id") == cmd_id:
                    return resp

        # 如果当前页面不在 eBay，先导航过去
        current_url = target.get("url", "")
        if "ebay.com" not in current_url:
            send_cmd("Page.navigate", {"url": "https://www.ebay.com/"})
            time.sleep(3)

        # 构建 API 参数（使用相对路径，fetch 自动用当前页面域名）
        # 根据市场选择对应货币
        currency_map = {
            "EBAY-US": "USD",
            "EBAY-GB": "GBP",
            "EBAY-DE": "EUR",
            "EBAY-AU": "AUD",
            "EBAY-CA": "CAD",
        }
        currency = currency_map.get(marketplace, "USD")

        api_path = (
            f"/sh/research/api/search?"
            f"marketplace={marketplace}"
            f"&keywords={requests.utils.quote(keywords)}"
            f"&dayRange={day_range}"
            f"&categoryId={category_id}"
            f"&offset={offset}"
            f"&limit={limit}"
            f"&tabName={tab_name}"
            f"&tz=Asia/Shanghai"
            f"&modules=aggregates"
            f"&modules=searchResults"
            f"&modules=resultsHeader"
            f"&currency={currency}"
        )

        js_code = f"""
        (async () => {{
            try {{
                const r = await fetch('{api_path}', {{credentials: 'include'}});
                const t = await r.text();
                return JSON.stringify({{status: r.status, body: t}});
            }} catch(e) {{
                return JSON.stringify({{status: 0, body: '', error: e.message}});
            }}
        }})()
        """

        result = send_cmd("Runtime.evaluate", {
            "expression": js_code,
            "returnByValue": True,
            "awaitPromise": True,
        })

        result_obj = result.get("result", {}).get("result", {})
        value = result_obj.get("value", "")

        if not value:
            exc = result.get("result", {}).get("exceptionDetails", {})
            exc_desc = exc.get("exception", {}).get("description", "未知错误")
            return {
                'status': 500,
                'raw_text': '',
                'modules': [],
            }

        fetch_result = json.loads(value) if isinstance(value, str) else value

        if fetch_result.get('error') or fetch_result.get('status') == 0:
            return {
                'status': 500,
                'raw_text': '',
                'modules': [],
                'error': fetch_result.get('error', 'fetch 失败')
            }

        body = fetch_result.get('body', '')
        status = fetch_result.get('status', 200)

        # 解析 NDJSON
        modules = []
        for chunk in body.split('\n\n'):
            chunk = chunk.strip()
            if chunk:
                try:
                    modules.append(json.loads(chunk))
                except json.JSONDecodeError:
                    pass

        return {
            'status': status,
            'raw_text': body,
            'modules': modules,
        }

    finally:
        ws.close()


def fetch_all_pages_via_cdp(
    keywords: str,
    cdp_url: str = "http://localhost:9222",
    marketplace: str = "EBAY-US",
    category_id: str = "0",
    day_range: int = 180,
    tab_name: str = "SOLD",
    limit: int = 50,
    page_size: int = 50,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    listing_format: Optional[str] = None,
    condition: Optional[str] = None,
    start_date: Optional[int] = None,
    end_date: Optional[int] = None,
    page_callback=None,
) -> List[Dict]:
    """通过 CDP 分页获取所有数据
    page_callback(page_num, page_items): 每抓完一页调用，用于实时推送"""
    all_items = []
    offset = 0
    max_iterations = (limit // page_size) + 2
    page_num = 0

    for i in range(max_iterations):
        page_num += 1
        print(f"[eBay搜索] 正在爬取第 {page_num} 页 (offset={offset})...")

        result = fetch_research_via_cdp(
            keywords=keywords,
            cdp_url=cdp_url,
            marketplace=marketplace,
            category_id=category_id,
            day_range=day_range,
            tab_name=tab_name,
            limit=page_size,
            offset=offset,
        )

        if result['status'] != 200:
            print(f"[eBay搜索] 第 {page_num} 页请求失败: HTTP {result['status']}")
            break

        items = parse_items(result)

        if not items:
            print(f"[eBay搜索] 第 {page_num} 页无数据，停止翻页")
            break

        all_items.extend(items)
        print(f"[eBay搜索] 第 {page_num} 页完成，当前累计 {len(all_items)} 条")

        # 实时推送该页数据
        if page_callback:
            page_callback(page_num, items)

        if len(all_items) >= limit:
            break

        for m in result.get('modules', []):
            if m.get('_type') == 'SearchResultsModule':
                pagination = m.get('pagination', {})
                next_page = pagination.get('next', {})
                if isinstance(next_page, dict) and next_page.get('disabled', True):
                    print(f"[eBay搜索] 已到最后一页")
                    return all_items[:limit]

        offset += page_size
        time.sleep(1)

    return all_items[:limit]


def fetch_all_pages(
    keywords: str,
    cookies: Dict[str, str],
    marketplace: str = "EBAY-US",
    category_id: str = "0",
    day_range: int = 1095,
    limit: int = 200,
    tab_name: str = "SOLD",
    tz: str = "Asia/Shanghai",
    page_size: int = 50,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    listing_format: Optional[str] = None,
    condition: Optional[str] = None,
    start_date: Optional[int] = None,
    end_date: Optional[int] = None,
    page_callback=None,
) -> List[Dict]:
    """分页获取所有数据
    page_callback(page_num, page_items): 每抓完一页调用，用于实时推送"""
    all_items = []
    offset = 0
    max_iterations = (limit // page_size) + 2
    page_num = 0

    for i in range(max_iterations):
        page_num += 1
        print(f"[eBay搜索] 正在爬取第 {page_num} 页 (offset={offset})...")

        result = fetch_research(
            keywords=keywords,
            cookies=cookies,
            marketplace=marketplace,
            category_id=category_id,
            day_range=day_range,
            limit=page_size,
            tab_name=tab_name,
            tz=tz,
            offset=offset,
            price_min=price_min,
            price_max=price_max,
            listing_format=listing_format,
            condition=condition,
            start_date=start_date,
            end_date=end_date,
        )

        if result['status'] != 200:
            print(f"[eBay搜索] 第 {page_num} 页请求失败: HTTP {result['status']}")
            break

        items = parse_items(result)

        if not items:
            print(f"[eBay搜索] 第 {page_num} 页无数据，停止翻页")
            break

        all_items.extend(items)
        print(f"[eBay搜索] 第 {page_num} 页完成，当前累计 {len(all_items)} 条")

        # 实时推送该页数据
        if page_callback:
            page_callback(page_num, items)

        if len(all_items) >= limit:
            break

        for m in result.get('modules', []):
            if m.get('_type') == 'SearchResultsModule':
                pagination = m.get('pagination', {})
                next_page = pagination.get('next', {})
                if isinstance(next_page, dict) and next_page.get('disabled', True):
                    print(f"[eBay搜索] 已到最后一页")
                    return all_items[:limit]

        offset += page_size
        time.sleep(1)

    return all_items[:limit]


def export_csv(items: List[Dict], output_file: str, aggregates: dict = None):
    """导出为 CSV"""
    if not items:
        print("没有数据可导出!")
        return

    fieldnames = [
        'title', 'item_id', 'avg_sold_price', 'avg_shipping', 'price_format',
        'sold_count', 'total_sales', 'bids', 'listing_format',
        'last_sold_date', 'extended_title', 'image_url', 'url',
    ]

    with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for item in items:
            clean = {k: (str(v) if v is not None else '') for k, v in item.items()}
            writer.writerow(clean)

    print(f"\n✅ 已导出 {len(items)} 条数据到: {output_file}")

    if aggregates:
        print(f"\n📊 汇总数据:")
        for k, v in aggregates.items():
            print(f"  {k}: {v}")


def calculate_aggregates(items: List[Dict]) -> dict:
    """从商品数据计算汇总统计"""
    def parse_number(val) -> float:
        if not val:
            return 0.0
        s = str(val).replace(',', '').replace('$', '').strip()
        m = re.search(r'[\d.]+', s)
        return float(m.group()) if m else 0.0

    total_sold = 0
    total_sales = 0.0
    prices = []
    sold_prices = []

    for item in items:
        sold = item.get('sold_count', '')
        if sold:
            total_sold += int(parse_number(sold))

        sales = item.get('total_sales', '')
        if sales:
            total_sales += parse_number(sales)

        curr_price = item.get('current_price', '')
        if curr_price:
            prices.append(parse_number(curr_price))

        avg_price = item.get('avg_sold_price', '')
        if avg_price:
            p = parse_number(avg_price)
            sold_prices.append(p)

    all_prices = prices + sold_prices
    if all_prices:
        avg_price = sum(all_prices) / len(all_prices)
        avg_price_str = f"${avg_price:.2f}"
    else:
        avg_price_str = "$0"

    return {
        'Total Sold': str(total_sold),
        'Total Sales': f"${total_sales:,.2f}",
        'Avg Sale Price': avg_price_str,
        'Average Price': avg_price_str,
        'Items Sold': str(total_sold),
    }


def search_products(
    keywords: str,
    cdp_url: str = "http://localhost:9222",
    marketplace: str = "EBAY-US",
    category_id: str = "0",
    day_range: int = 180,
    limit: int = 50,
    tab_name: str = "SOLD",
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    page_callback=None,
) -> dict:
    """
    通过 CDP 搜索 eBay 产品，供 Flask 后端调用。
    page_callback(page_num, page_items): 每抓完一页调用，用于实时推送。
    """
    try:
        print(f"[eBay搜索] 正在爬取: {keywords} (市场: {marketplace}, 类型: {tab_name}, 目标: {limit}条)")

        first_result = fetch_research_via_cdp(
            keywords=keywords,
            cdp_url=cdp_url,
            marketplace=marketplace,
            category_id=category_id,
            day_range=day_range,
            tab_name=tab_name,
            limit=50,
            offset=0,
        )

        if first_result['status'] != 200:
            return {
                'success': False,
                'items': [],
                'aggregates': {},
                'total': 0,
                'error': f"CDP 请求失败: HTTP {first_result['status']}",
            }

        aggregates = parse_aggregates(first_result)
        items = parse_items(first_result)

        print(f"[eBay搜索] 第 1 页完成，获取 {len(items)} 条数据")

        # 实时推送第1页
        if page_callback:
            page_callback(1, items)

        # 如果需要更多数据，翻页获取
        if len(items) < limit and len(items) >= 50:
            print(f"[eBay搜索] 继续获取更多数据...")
            more_items = fetch_all_pages_via_cdp(
                keywords=keywords,
                cdp_url=cdp_url,
                marketplace=marketplace,
                category_id=category_id,
                day_range=day_range,
                tab_name=tab_name,
                limit=limit,
                page_size=50,
                page_callback=page_callback,
            )
            items = more_items[:limit]

        print(f"[eBay搜索] 完成！共获取 {len(items)} 条数据")

        # 如果 API aggregates 为空，从商品数据计算
        if not aggregates or not aggregates.get('Total Sold'):
            aggregates = calculate_aggregates(items)

        return {
            'success': True,
            'items': items[:limit],
            'aggregates': aggregates,
            'total': len(items[:limit]),
            'error': None,
        }

    except Exception as e:
        return {
            'success': False,
            'items': [],
            'aggregates': {},
            'total': 0,
            'error': str(e)
        }


def main():
    parser = argparse.ArgumentParser(
        description="eBay Seller Hub Research 数据导出工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--keywords', required=True, help='搜索关键词')
    parser.add_argument('--marketplace', default='EBAY-US', help='eBay 市场')
    parser.add_argument('--category-id', default='0', help='分类 ID')
    parser.add_argument('--day-range', type=int, default=1095, help='时间范围(天)')
    parser.add_argument('--limit', type=int, default=50, help='获取数量上限')
    parser.add_argument('--tab-name', default='SOLD', choices=['SOLD', 'ACTIVE'], help='数据类型')
    parser.add_argument('--cdp-url', default='http://localhost:9222', help='CDP 地址')
    parser.add_argument('--output', default='', help='输出 CSV 文件路径')
    parser.add_argument('--save-raw', action='store_true', help='保存原始 API 响应')

    args = parser.parse_args()

    if not args.output:
        safe_kw = re.sub(r'[^\w]', '_', args.keywords)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        args.output = f'ebay_research_{safe_kw}_{ts}.csv'

    print("=" * 60)
    print("eBay Seller Hub Research 数据导出")
    print("=" * 60)
    print(f"  关键词: {args.keywords}")
    print(f"  市场: {args.marketplace}")
    print(f"  时间范围: {args.day_range} 天")
    print(f"  数量上限: {args.limit}")
    print(f"  数据类型: {args.tab_name}")
    print(f"  输出: {args.output}")
    print("=" * 60)

    result = search_products(
        keywords=args.keywords,
        cdp_url=args.cdp_url,
        marketplace=args.marketplace,
        category_id=args.category_id,
        day_range=args.day_range,
        limit=args.limit,
        tab_name=args.tab_name,
    )

    if not result['success']:
        print(f"\n❌ 搜索失败: {result['error']}")
        sys.exit(1)

    items = result['items']
    aggregates = result['aggregates']

    if items:
        export_csv(items, args.output, aggregates)
    else:
        print("\n❌ 未获取到任何数据!")


if __name__ == '__main__':
    main()
