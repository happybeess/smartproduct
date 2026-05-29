"""
eBay 类目卖家统计分析工具

功能：
- 统计某个关键词/类目下的不同卖家数量
- 展示卖家销售占比分布
- 分析竞争密度

依赖：ebay_search.py 中的 eBaySearcher
"""

import sys
import argparse
from pathlib import Path
from collections import Counter

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from ebay.ebay_search import eBaySearcher, SearchFilters


def analyze_sellers(
    searcher: eBaySearcher,
    q: str = None,
    category_ids: str = None,
    gtin: str = None,
    epid: str = None,
    price_min: float = None,
    price_max: float = None,
    price_currency: str = "USD",
    condition_ids: list = None,
    buying_options: list = None,
    location_country: str = None,
    max_items: int = 500,
    delay: float = 0.3,
) -> dict:
    """
    分析搜索结果中的卖家分布

    Returns:
        dict: {
            'total_items': int,        # 总商品数
            'unique_sellers': int,     # 不同卖家数
            'seller_distribution': list, # 卖家销售占比 [{'username': str, 'count': int, 'pct': float}, ...]
            'top_sellers': list,       # Top 10 卖家
            'items': list,             # 所有商品列表
            'search_type': str,        # 搜索类型
        }
    """
    if not any([q, category_ids, gtin, epid]):
        raise ValueError("至少需要提供 q / category_ids / gtin / epid 之一")

    search_type = "keyword"
    if category_ids:
        search_type = "category"
    elif gtin:
        search_type = "gtin"
    elif epid:
        search_type = "epid"

    # 构建筛选器
    f = SearchFilters()
    if price_min is not None or price_max is not None:
        f.price_range(price_min, price_max, price_currency)
    if condition_ids:
        f.conditions(condition_ids=condition_ids)
    if buying_options:
        f.buying_options(buying_options)
    if location_country:
        f.location_country(location_country)

    # 自动翻页获取数据
    all_items = searcher.search_all_pages(
        q=q,
        category_ids=category_ids,
        gtin=gtin,
        epid=epid,
        limit=100,
        max_items=max_items,
        filters=f,
        delay=delay,
    )

    # 统计卖家
    seller_counter = Counter()
    seller_items = {}

    for item in all_items:
        seller = item.get("seller", {}) or {}
        username = seller.get("username", "")
        if username:
            seller_counter[username] += 1
            if username not in seller_items:
                seller_items[username] = {
                    "username": username,
                    "feedback_score": seller.get("feedbackScore", 0),
                    "feedback_percent": seller.get("feedbackPercentage", 0),
                    "count": 0,
                    "avg_price": 0,
                    "total_price": 0,
                }
            seller_items[username]["count"] += 1

            price_info = item.get("price", {})
            price = float(price_info.get("value", 0) or 0)
            if price > 0:
                seller_items[username]["total_price"] += price

    # 计算平均价格
    for username, data in seller_items.items():
        if data["count"] > 0:
            data["avg_price"] = data["total_price"] / data["count"]

    total_items = len(all_items)
    unique_sellers = len(seller_counter)

    # Top 卖家
    top_sellers = seller_counter.most_common(10)

    # 分布列表
    seller_distribution = []
    for username, count in seller_counter.most_common(20):
        pct = (count / total_items * 100) if total_items > 0 else 0
        seller_info = seller_items.get(username, {})
        seller_distribution.append({
            "rank": len(seller_distribution) + 1,
            "username": username,
            "count": count,
            "pct": round(pct, 1),
            "feedback_score": seller_info.get("feedback_score", 0),
            "avg_price": round(seller_info.get("avg_price", 0), 2),
        })

    # 竞争密度
    competition_index = round(unique_sellers / total_items * 100, 1) if total_items > 0 else 0

    return {
        "total_items": total_items,
        "unique_sellers": unique_sellers,
        "competition_index": competition_index,  # 卖家占比（越高竞争越分散）
        "avg_items_per_seller": round(total_items / unique_sellers, 1) if unique_sellers > 0 else 0,
        "top_sellers": top_sellers,
        "seller_distribution": seller_distribution,
        "items": all_items,
        "search_type": search_type,
    }


def print_analysis(analysis: dict, keyword: str = ""):
    """打印分析结果"""
    total = analysis["total_items"]
    unique = analysis["unique_sellers"]
    comp_idx = analysis["competition_index"]
    avg_per_seller = analysis["avg_items_per_seller"]

    print("\n" + "=" * 70)
    print(f"  eBay 卖家竞争分析  |  关键词: {keyword or 'N/A'}")
    print("=" * 70)

    # 概览
    print(f"\n📊 概览数据:")
    print(f"   总商品数:     {total}")
    print(f"   不同卖家数:   {unique}")
    print(f"   每卖家平均商品: {avg_per_seller}")
    print(f"   卖家集中度:   {comp_idx}% (越高竞争越分散)")

    # 竞争解读
    if avg_per_seller < 1.5:
        competition_level = "🔴 高度竞争（商品集中在少数卖家）"
    elif avg_per_seller < 3:
        competition_level = "🟡 中度竞争"
    else:
        competition_level = "🟢 竞争分散（机会较多）"

    print(f"   竞争程度:     {competition_level}")

    # Top 10 卖家
    top = analysis["seller_distribution"]
    if top:
        print(f"\n🏆 Top 10 卖家分布:")
        print("-" * 70)
        print(f"  {'排名':<4} {'卖家':<20} {'商品数':<8} {'占比':<8} {'好评率':<10} {'均价':<10}")
        print("-" * 70)
        for s in top[:10]:
            fb = s.get("feedback_score", 0)
            fb_str = f"{fb:,}" if fb else "N/A"
            avg_price = s.get("avg_price", 0)
            price_str = f"${avg_price:.2f}" if avg_price else "N/A"
            print(f"  {s['rank']:<4} {s['username']:<20} {s['count']:<8} {s['pct']}%-"
                  f"{'':2} {fb_str:<10} {price_str}")

    # 市场集中度分析
    if len(top) >= 3:
        top3_share = sum(s["pct"] for s in top[:3])
        top5_share = sum(s["pct"] for s in top[:5])
        print(f"\n📈 市场集中度:")
        print(f"   Top 3 卖家占据: {top3_share}% 的商品")
        print(f"   Top 5 卖家占据: {top5_share}% 的商品")

        if top3_share > 60:
            print("   ⚠️  市场被头部卖家垄断，进入门槛较高")
        elif top3_share > 40:
            print("   ℹ️  市场有一定集中度，但仍有空间")
        else:
            print("   ✅  市场分散，新进入者有机会")

    print("\n" + "=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="eBay 类目卖家竞争分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 关键词卖家分析
  python seller_analysis.py -q "iphone 15 case" --max-items 500

  # 分类 + 价格区间分析
  python seller_analysis.py --category 58539 --price-min 10 --price-max 50

  # 指定站点分析
  python seller_analysis.py -q "wireless earbuds" --marketplace EBAY_GB

  # 全新商品卖家分析
  python seller_analysis.py -q "laptop stand" --condition 1000
""",
    )

    # 搜索条件
    parser.add_argument("-q", "--query", help="搜索关键词")
    parser.add_argument("--gtin", help="GTIN（UPC/EAN/ISBN）")
    parser.add_argument("--epid", help="eBay Product ID")
    parser.add_argument("--category", dest="category_ids", help="分类ID")

    # 筛选参数
    parser.add_argument("--price-min", type=float, help="最低价格")
    parser.add_argument("--price-max", type=float, help="最高价格")
    parser.add_argument("--price-currency", default="USD", help="货币代码")
    parser.add_argument("--condition", action="append", type=int, dest="condition_ids",
                        help="商品状况ID (1000=全新)")
    parser.add_argument("--buying-options", action="append",
                        help="销售方式: FIXED_PRICE, AUCTION")
    parser.add_argument("--location-country", help="卖家所在国家")

    # 分析参数
    parser.add_argument("--max-items", type=int, default=500,
                        help="最大分析商品数（默认500）")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="翻页间隔（秒，默认0.3）")

    # 环境参数
    parser.add_argument("--sandbox", action="store_true", help="使用沙箱环境")
    parser.add_argument("--marketplace", default="EBAY_US",
                        choices=["EBAY_US", "EBAY_GB", "EBAY_DE", "EBAY_AU", "EBAY_CA"],
                        help="目标市场")

    args = parser.parse_args()

    if not any([args.query, args.gtin, args.epid, args.category_ids]):
        parser.print_help()
        print("\n❌ 错误：至少需要提供 -q / --gtin / --epid / --category 之一")
        exit(1)

    # 初始化搜索器
    try:
        searcher = eBaySearcher(
            marketplace_id=args.marketplace,
            sandbox=args.sandbox,
        )
    except ValueError as e:
        print(f"\n❌ {e}")
        print("\n请确保 .env 文件中配置了 EBAY_CLIENT_ID 和 EBAY_CLIENT_SECRET")
        exit(1)

    keyword = args.query or args.category_ids or args.gtin or args.epid
    env_label = "[Sandbox]" if args.sandbox else "[Production]"
    print(f"\n{env_label} 正在分析卖家数据: {keyword}")
    print(f"  最大商品数: {args.max_items}")

    try:
        analysis = analyze_sellers(
            searcher=searcher,
            q=args.query,
            category_ids=args.category_ids,
            gtin=args.gtin,
            epid=args.epid,
            price_min=args.price_min,
            price_max=args.price_max,
            price_currency=args.price_currency,
            condition_ids=args.condition_ids,
            buying_options=args.buying_options,
            location_country=args.location_country,
            max_items=args.max_items,
            delay=args.delay,
        )

        print_analysis(analysis, keyword=args.query)

    except Exception as e:
        print(f"\n❌ 分析失败: {type(e).__name__}: {e}")
        exit(1)


if __name__ == "__main__":
    main()
