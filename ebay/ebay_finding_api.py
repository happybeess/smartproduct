"""
eBay Finding API 搜索工具
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
基于 eBay Finding API (findItemsAdvanced) 进行商品搜索，
无需浏览器、无反爬问题，稳定获取 eBay 搜索结果。

官方文档: https://developer.ebay.com/devzone/finding/callref/findItemsAdvanced.htm

特点:
- 纯 HTTP API 调用，不需要 Playwright/浏览器
- 不会被反爬拦截，结果完整可靠
- 支持分页，每页最多 100 条
- 支持按价格/销量等排序
- 支持筛选：新品/二手、Free Shipping、条件等
- 返回丰富字段：标题/价格/运费/评分/销量/卖家/图片/链接
- 兼容 ebay_scraper.py 的输出格式，可直接替换

使用前需配置（你的 .env 已配置好）:
  EBAY_APP_ID     - eBay App ID (Client ID)
  RuName (可选)    - 如果需要 User Token 则配置

环境变量:
  EBAY_APP_ID=你的AppID

用法:
    python ebay_finding_api.py "Freshcut Paper Pop Up Cards"
    python ebay_finding_api.py "Honda Pilot" --pages 5 --sort price
    python ebay_finding_api.py "iPhone" --condition New --free-shipping --max-price 500
    python ebay_finding_api.py "keyword" --pages 0 --output result.json
"""

import os
import re
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlencode
from typing import List, Optional, Dict

import requests


class RateLimitError(Exception):
    """eBay API 配额耗尽"""
    pass

# ── 加载 .env ──
def _load_env_file():
    """从 .env 文件加载环境变量"""
    candidates = [Path(__file__).parent, Path(__file__).parent.parent]
    for base_dir in candidates:
        env_path = base_dir / ".env"
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key not in os.environ:
                        os.environ[key] = value

_load_env_file()


# ============================================================
# 配置
# ============================================================
APP_ID = os.environ.get("EBAY_APP_ID", "")

# Finding API 端点（Global ID 对应不同站点）
FINDING_API_URLS = {
    "EBAY-US": "https://svcs.ebay.com/services/search/FindingService/v1",
    "EBAY-GB": "https://svcs.ebay.com/services/search/FindingService/v1",
    "EBAY-DE": "https://svcs.ebay.com/services/search/FindingService/v1",
    "EBAY-AU": "https://svcs.ebay.com/services/search/FindingService/v1",
    "EBAY-CA": "https://svcs.ebay.com/services/search/FindingService/v1",
}

# Global ID 映射（Finding API 用 Global ID 区分站点）
GLOBAL_IDS = {
    "EBAY-US": "EBAY-US",
    "EBAY-GB": "EBAY-GB",
    "EBAY-DE": "EBAY-DE",
    "EBAY-AU": "EBAY-AU",
    "EBAY-CA": "EBAY-CA",
    "EBAY-FR": "EBAY-FR",
    "EBAY-IT": "EBAY-IT",
    "EBAY-ES": "EBAY-ES",
}

# 排序映射
SORT_MAP = {
    "best_match": "BestMatch",
    "price_low": "CurrentPriceLowest",
    "price_high": "CurrentPriceHighest",
    "newest": "StartTimeNewest",
    "ending": "EndingSoonest",
}

SORT_DESC = {
    "best_match": "Best Match",
    "price_low": "Price: Low to High",
    "price_high": "Price: High to Low",
    "newest": "Newly Listed",
    "ending": "Ending Soonest",
}


class eBayFindingAPI:
    """eBay Finding API 搜索客户端"""

    def __init__(self, app_id: str = None, marketplace: str = "EBAY-US"):
        self.app_id = app_id or APP_ID
        self.marketplace = marketplace
        self.global_id = GLOBAL_IDS.get(marketplace, "EBAY-US")
        self.api_url = FINDING_API_URLS.get(marketplace, FINDING_API_URLS["EBAY-US"])

        if not self.app_id:
            raise ValueError(
                "缺少 EBAY_APP_ID！\n"
                "请在 .env 中配置 EBAY_APP_ID，或通过参数传入。\n"
                "获取方式: https://developer.ebay.com/ 创建应用后复制 App ID。"
            )

    def _build_payload(self, keywords: str, **kwargs) -> dict:
        """构建 Finding API 请求参数"""
        payload = {
            "OPERATION-NAME": "findItemsAdvanced",
            "SERVICE-NAME": "FindingService",
            "SERVICE-VERSION": "1.13.0",
            "SECURITY-APPNAME": self.app_id,
            "RESPONSE-DATA-FORMAT": "JSON",
            "REST-PAYLOAD": "true",

            # 关键词搜索
            "keywords": keywords,

            # 全局站点 ID
            "GLOBAL-ID": self.global_id,

            # 每页返回数量 (1-100)
            "paginationInput.entriesPerPage": min(kwargs.get("entries_per_page", 100), 100),

            # 页码 (从 1 开始)
            "paginationInput.pageNumber": kwargs.get("page_number", 1),
        }

        # 排序
        sort_order = kwargs.get("sort_order")
        if sort_order and sort_order in SORT_MAP.values():
            payload["sortOrder"] = sort_order
        elif kwargs.get("sort") in SORT_MAP:
            payload["sortOrder"] = SORT_MAP[kwargs.get("sort")]

        # 筛选器列表
        filters = []

        # 条件筛选: New / Used / Unspecified
        condition = kwargs.get("condition")
        if condition:
            cond_map = {"New": "New", "Used": "Used", "Unspecified": "Unspecified"}
            if condition in cond_map:
                filters.append({"name": "Condition", "value": cond_map[condition]})

        # 免运费
        if kwargs.get("free_shipping"):
            filters.append({"name": "FreeShippingOnly", "value": "true"})

        # 最低价格
        max_price = kwargs.get("max_price")
        if max_price is not None:
            filters.append({"name": "MaxPrice", "value": str(max_price)})

        # 最高价格
        min_price = kwargs.get("min_price")
        if min_price is not None:
            filters.append({"name": "MinPrice", "value": str(min_price)})

        # 卖家
        seller = kwargs.get("seller")
        if seller:
            payload["seller"] = seller

        # 类目 ID
        category_id = kwargs.get("category_id")
        if category_id and category_id != "0":
            payload["categoryId"] = category_id

        # 添加筛选器到 payload
        for i, f in enumerate(filters):
            payload[f"itemFilter({i}).name"] = f["name"]
            payload[f"itemFilter({i}).value"] = f["value"]
            if f["name"] == "MaxPrice":
                payload[f"itemFilter({i}).paramName"] = "Currency"
                payload[f"itemFilter({i}).paramValue"] = "USD"
            elif f["name"] == "MinPrice":
                payload[f"itemFilter({i}).paramName"] = "Currency"
                payload[f"itemFilter({i}).paramValue"] = "USD"

        return payload

    def search(self, keywords: str, page_number: int = 1,
                entries_per_page: int = 100, sort: str = "best_match",
                condition: str = None, free_shipping: bool = False,
                min_price: float = None, max_price: float = None,
                category_id: str = None) -> dict:
        """
        执行单页搜索

        返回原始 API 响应 dict，包含:
          - findItemsAdvancedResponse[0].searchResult: 商品列表
          - findItemsAdvancedResponse[0].paginationOutput: 分页信息
          - findItemsAdvancedResponse[0].itemCount: 总数
        """
        payload = self._build_payload(
            keywords,
            page_number=page_number,
            entries_per_page=entries_per_page,
            sort=sort,
            condition=condition,
            free_shipping=free_shipping,
            min_price=min_price,
            max_price=max_price,
            category_id=category_id,
        )

        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; eBayFindingAPI/1.0)",
        }

        resp = requests.get(self.api_url, params=payload, headers=headers, timeout=30)

        # 处理 API 级别错误
        if resp.status_code == 500:
            try:
                err_data = resp.json()
                msg = ""
                for em in err_data.get("errorMessage", []):
                    for e in em.get("error", []):
                        domain = e.get("domain", [""])[0] if isinstance(e.get("domain"), list) else e.get("domain", "")
                        subdomain = e.get("subdomain", [""])[0] if isinstance(e.get("subdomain"), list) else e.get("subdomain", "")
                        errmsg = e.get("message", [""])[0] if isinstance(e.get("message"), list) else e.get("message", "")
                        if domain == "Security" and "RateLimiter" in str(subdomain):
                            raise RateLimitError(
                                f"eBay Finding API 配额已用完 (Rate Limit)\n"
                                f"  App ID: {self.app_id[:20]}...\n"
                                f"  原因: {errmsg}\n"
                                f"  解决方案: 等待明天配额重置(UTC)，或在 https://developer.ebay.com/my/keys 申请提高限额"
                            )
                        else:
                            msg = errmsg
                raise requests.HTTPError(f"API 500 错误: {msg}")
            except RateLimitError:
                raise
            except Exception:
                resp.raise_for_status()
        resp.raise_for_status()
        data = resp.json()

        return data

    def parse_items(self, api_response: dict) -> List[Dict]:
        """从 API 响应中提取商品列表"""
        items = []
        try:
            response = api_response.get("findItemsAdvancedResponse", [{}])
            search_result = response[0].get("searchResult", {})
            raw_items = search_result.get("@count", "0")
            if int(raw_items) == 0:
                return items

            item_list = search_result.get("item", [])
            if isinstance(item_list, dict):
                item_list = [item_list]

            for item in item_list:
                parsed = self._parse_single_item(item)
                if parsed:
                    items.append(parsed)

        except (KeyError, TypeError, ValueError) as e:
            print(f"[Finding API] 解析错误: {e}")

        return items

    @staticmethod
    def _parse_single_item(item: dict) -> Optional[dict]:
        """解析单个商品"""
        try:
            # 基础信息
            item_id = item.get("itemId", [None])
            item_id = item_id[0] if isinstance(item_id, list) else item_id

            title = item.get("title", [None])
            title = title[0] if isinstance(title, list) else title

            # 价格
            price_info = item.get("sellingStatus", {}).get("currentPrice", {})
            if isinstance(price_info, list):
                price_info = price_info[0] if price_info else {}
            price = float(price_info.get("__value__", 0) or price_info.get("value", 0))
            currency = price_info.get("@currencyId", "USD")

            # 运费
            shipping_info = item.get("shippingInfo", {})
            if isinstance(shipping_info, list):
                shipping_info = shipping_info[0] if shipping_info else {}

            shipping_cost = shipping_info.get("shippingServiceCost", {})
            if isinstance(shipping_cost, list):
                shipping_cost = shipping_cost[0] if shipping_cost else {}
            shipping = float(shipping_cost.get("__value__", 0) or shipping_cost.get("value", 0))
            free_shipping = str(shipping_info.get("shippingType", "")).lower() == "free"

            # 图片
            gallery_url = item.get("galleryURL", [None])
            gallery_url = gallery_url[0] if isinstance(gallery_url, list) else gallery_url
            # 获取更大尺寸的图片
            if gallery_url:
                gallery_url = re.sub(r'~\$[_\d]+', '', gallery_url).replace('_57.jpg', '_300.jpg')

            picture_url = item.get("pictureURLSuperSize", [None]) or item.get("pictureURLLarge", [None])
            picture_url = picture_url[0] if isinstance(picture_url, list) else picture_url
            image_url = picture_url or gallery_url or ""

            # 链接
            view_item_url = item.get("viewItemURL", [None])
            view_item_url = view_item_url[0] if isinstance(view_item_url, list) else view_item_url

            # 条件/状态
            condition = item.get("condition", [None])
            condition = condition[0] if isinstance(condition, list) else condition
            condition_display_name = item.get("conditionDisplayName", [None])
            condition_display_name = condition_display_name[0] if isinstance(condition_display_name, list) else condition_display_name

            # 列表类型 (FixedPrice / Auction / Classified)
            listing_type = item.get("listingType", [None])
            listing_type = listing_type[0] if isinstance(listing_type, list) else listing_type

            # 评分
            feedback = item.get("sellerFeedbackScore", [])
            seller_feedback_score = feedback[0] if isinstance(feedback, list) else (feedback or "0")

            positive_pct = item.get("positiveFeedbackPercent", [])
            positive_feedback_pct = positive_pct[0] if isinstance(positive_pct, list) else (positive_pct or "0")

            # 卖家名
            seller_name = item.get("sellerUserName", [])
            seller = seller_name[0] if isinstance(seller_name, list) else (seller_name or "")

            # 所在地
            location = item.get("location", [None])
            location = location[0] if isinstance(location, list) else location

            # 类目
            cat_id = item.get("primaryCategory", {}).get("categoryId", [None])
            cat_id = cat_id[0] if isinstance(cat_id, list) else cat_id

            cat_name = item.get("primaryCategory", {}).get("categoryName", [None])
            category_name = cat_name[0] if isinstance(cat_name, list) else cat_name

            # 当前出价数量 (竞拍)
            bid_count_obj = item.get("sellingStatus", {}).get("bidCount", {})
            if isinstance(bid_count_obj, list):
                bid_count_obj = bid_count_obj[0] if bid_count_obj else {}
            bid_count = bid_count_obj.get("__value__", 0) or bid_count_obj.get("value", 0)

            # 上架时间
            start_time = item.get("startTime", [None])
            start_time = start_time[0] if isinstance(start_time, list) else start_time

            # 结束时间
            end_time = item.get("endTime", [None])
            end_time = end_time[0] if isinstance(end_time, list) else end_time

            # 是否 Best Offer
            best_offer = "bestOfferEnabled" in item and str(item.get("bestOfferEnabled", "")).lower() == "true"

            # 返回统一格式
            return {
                "title": title or "",
                "price": round(price, 2) if price else 0,
                "currency": currency,
                "shipping": round(shipping, 2) if shipping else 0,
                "free_shipping": free_shipping,
                "total": round((price or 0) + (shipping or 0), 2),
                "image_url": image_url,
                "url": view_item_url or "",
                "item_id": item_id or "",
                "condition": condition_display_name or condition or "",
                "listing_type": listing_type or "",
                "seller": seller,
                "seller_feedback_score": int(seller_feedback_score or 0),
                "seller_positive_feedback_pct": positive_feedback_pct,
                "location": location or "",
                "category_id": cat_id or "",
                "category_name": category_name or "",
                "bid_count": int(bid_count or 0),
                "best_offer": best_offer,
                "start_time": start_time or "",
                "end_time": end_time or "",
            }

        except Exception as e:
            print(f"[Finding API] 解析商品失败: {e}")
            return None

    def get_total_count(self, api_response: dict) -> int:
        """获取搜索结果总数"""
        try:
            resp = api_response.get("findItemsAdvancedResponse", [{}])[0]
            return int(resp.get("paginationOutput", {}).get("totalEntries", ["0"])[0])
        except (IndexError, KeyError, ValueError, TypeError):
            return 0

    def get_total_pages(self, api_response: dict, per_page: int = 100) -> int:
        """获取总页数"""
        total = self.get_total_count(api_response)
        return (total + per_page - 1) // per_page if total > 0 else 0

    def has_more_pages(self, api_response: dict) -> bool:
        """检查是否还有下一页"""
        try:
            resp = api_response.get("findItemsAdvancedResponse", [{}])[0]
            pagination = resp.get("paginationOutput", {})
            current_page = int(pagination.get("pageNumber", ["1"])[0])
            total_pages = int(pagination.get("totalPages", ["1"])[0])
            return current_page < total_pages
        except (IndexError, KeyError, ValueError, TypeError):
            return False


def scrape_data(keyword: str, last_n: int = 999, max_pages: int = 4,
                sort: str = "best_match", app_id: str = None, **kwargs) -> dict:
    """
    兼容接口 — 与 ebay_scraper.scrape_data() 输出格式一致
    供 competitor_finder / app.py 直接调用替换
    """
    api = eBayFindingAPI(
        app_id=app_id,
        marketplace=kwargs.get("marketplace", "EBAY-US"),
    )
    all_items = []
    ebay_total_results = 0
    page_num = 0

    try:
        for p in range(1, max_pages + 1 if max_pages > 0 else 100):
            page_num = p
            print(f"  [Finding API p{p}] 正在搜索: {keyword}")

            resp = api.search(
                keyword,
                page_number=p,
                entries_per_page=100,
                sort=sort,
                condition=kwargs.get("condition"),
                free_shipping=kwargs.get("free_shipping", False),
                min_price=kwargs.get("min_price"),
                max_price=kwargs.get("max_price"),
                category_id=kwargs.get("category_id"),
            )

            # 第一页拿总数
            if p == 1:
                ebay_total_results = api.get_total_count(resp)
                total_pages = api.get_total_pages(resp)
                print(f"  Finding API 显示总结果: {ebay_total_results} 条, 共 {total_pages} 页")
                if max_pages == 0:
                    max_pages = min(total_pages, 100)  # 最多爬100页

            items = api.parse_items(resp)
            print(f"  本页提取 {len(items)} 个商品")

            if not items:
                print(f"  无数据，停止翻页")
                break

            all_items.extend(items)

            # 检查是否还有下一页
            if not api.has_more_pages(resp):
                print(f"  已到最后一页")
                break

            time.sleep(0.5)  # API 限流保护

    except RateLimitError as e:
        return {"success": False, "error": str(e), "rate_limited": True}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

    if not all_items:
        return {"success": False, "error": f"eBay Finding API 未找到 '{keyword}' 的商品"}

    # 去重
    seen_ids = set()
    unique_items = []
    dup_count = 0
    for item in all_items:
        iid = item.get("item_id", "")
        if iid and iid in seen_ids:
            dup_count += 1
            continue
        if iid:
            seen_ids.add(iid)
        unique_items.append(item)
    if dup_count:
        print(f"  去重: 移除 {dup_count} 个重复商品")

    # 按 total 排序
    unique_items.sort(key=lambda x: x.get("total", 0) or 99999)

    # 取最低N个算均价
    n = min(last_n, len(unique_items))
    lowest_n = unique_items[:n]
    avg = sum(x["total"] for x in lowest_n) / n

    result = {
        "success": True,
        "keyword": keyword,
        "sort": sort,
        "sort_desc": SORT_DESC.get(sort, sort),
        "ebay_total_results": ebay_total_results,
        "total_items": len(unique_items),
        "pages_scraped": page_num,
        "avg_price": round(avg, 2),
        "prices": [round(x["total"], 2) for x in lowest_n],
        "items": unique_items,
        "lowest_n": lowest_n,
        "source": "ebay_finding_api",
    }
    return result


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="eBay Finding API 搜索工具 — 无需浏览器，稳定可靠的 API 搜索",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ebay_finding_api.py "Freshcut Paper Pop Up Cards"
  python ebay_finding_api.py "iPhone 15" --pages 3 --sort price_low
  python ebay_finding_api.py "Nike shoes" --condition New --free-shipping
  python ebay_finding_api.py "camera" --min-price 50 --max-price 200
  python ebay_finding_api.py "keyword" --marketplace EBAY-GB --output result.json

排序方式:
  best_match   最佳匹配（默认）
  price_low    价格：低→高
  price_high   价格：高→低
  newest       最新上架
  ending       即将结束

注意: Finding API 不需要 Client Secret，只需要 App ID (EBAY_APP_ID)。
      你的 .env 中已配置此凭据。
""",
    )

    parser.add_argument("keyword", help="搜索关键词")
    parser.add_argument("--sort", default="best_match",
                        choices=list(SORT_MAP.keys()),
                        help="排序方式（默认: best_match）")
    parser.add_argument("--last", type=int, default=10,
                        help="取最低N个算均价（默认10）")
    parser.add_argument("--pages", type=int, default=4,
                        help="最多翻页数（默认4，0=全部）")
    parser.add_argument("--marketplace", "-m", default="EBAY-US",
                        help="目标市场（默认 EBAY-US）")
    parser.add_argument("--condition", choices=["New", "Used", "Unspecified"],
                        help="商品条件筛选")
    parser.add_argument("--free-shipping", action="store_true",
                        help="只显示免运费商品")
    parser.add_argument("--min-price", type=float, help="最低价格 ($)")
    parser.add_argument("--max-price", type=float, help="最高价格 ($)")
    parser.add_argument("--output", "-o", help="输出 JSON 文件路径")
    parser.add_argument("--app-id", help="eBay App ID（覆盖 .env 配置）")

    args = parser.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 70)
    print(f"  eBay Finding API 搜索工具")
    print(f"  关键词: {args.keyword}")
    print(f"  市场: {args.marketplace} | 排序: {SORT_DESC.get(args.sort, args.sort)}")
    print(f"  页数: {'全部' if args.pages == 0 else args.pages} | 取最低 {args.last} 个")
    print("=" * 70)

    try:
        result = scrape_data(
            args.keyword,
            last_n=args.last,
            max_pages=args.pages,
            sort=args.sort,
            app_id=args.app_id,
            marketplace=args.marketplace,
            condition=args.condition,
            free_shipping=args.free_shipping,
            min_price=args.min_price,
            max_price=args.max_price,
        )
    except ValueError as e:
        print(f"\n  错误: {e}")
        sys.exit(1)
    except RateLimitError as e:
        print(f"\n  ⚠️ {e}")
        sys.exit(2)

    if not result.get("success"):
        print(f"\n  失败: {result.get('error', '未知错误')}")
        sys.exit(1)

    items = result["items"]
    lowest = result.get("lowest_n", [])

    print(f"\n{'=' * 70}")
    ebay_total = result.get("ebay_total_results", 0)
    print(f"  搜索结果: {len(items)} 个商品 (API总数: {ebay_total}, 爬取 {result.get('pages_scraped', '?')} 页)")
    print(f"  排序: {result.get('sort_desc', '')}")
    print("=" * 70)

    for i, item in enumerate(items[:30], 1):
        ship_str = "FREE" if item.get("free_shipping") else f"${item.get('shipping', 0):.2f}"
        total = item.get("total", 0)
        cond = item.get("condition", "")[:12]
        seller = item.get("seller", "")[:15]
        fb = item.get("seller_feedback_score", 0)
        print(f"  [{i:2d}] ${total:<8.2f} (${item.get('price', 0):<7.2f}+{ship_str}) [{cond:>12s}] "
              f"seller:{seller}({fb})  {item['title'][:50]}")

    if len(items) > 30:
        print(f"  ... 还有 {len(items) - 30} 条未显示")

    print(f"\n  >>> 最低 {len(lowest)} 个均价: ${result['avg_price']:.2f} <<<")

    # 保存 JSON
    output = args.output or os.path.join(os.path.dirname(__file__), "_tmp_ebay_finding_result.json")
    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  结果已保存: {output}")


if __name__ == "__main__":
    main()
