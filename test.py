"""
eBay 搜索测试脚本 — 单个关键词搜索（美国地址）
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
用法: python test.py
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ebay.ebay_scraper import scrape

# ── 搜索关键词 ──
KEYWORD = "Freshcut Paper Pop Up Cards, Hummingbird Oasis, 12 Inch Life Sized Forever Flower Bouquet 3D Popup Greeting Card, Birthday Cards, Thank You Card, Blank Notecard & Envelope"

# ── 参数 ──
MAX_PAGES = 3
SORT = "best_match"


def main():
    print(f"{'=' * 70}")
    print(f"  eBay 搜索测试 (Ship to: 90001 Los Angeles, CA, US)")
    print(f"  关键词: {KEYWORD}")
    print(f"  排序: {SORT} | 最大页数: {'全部' if MAX_PAGES == 0 else MAX_PAGES}")
    print(f"{'=' * 70}")

    result = scrape(
        keyword=KEYWORD,
        sort=SORT,
        last_n=999,
        max_pages=MAX_PAGES,
        output_file=None,
    )

    if not result.get("success"):
        print(f"\n  !!! 失败: {result.get('error', '未知错误')}")
        return

    items = result.get("items", [])

    print(f"\n{'=' * 70}")
    print(f"  >>> 成功: {result['total_items']} 个商品 (共爬 {result.get('pages_scraped', '?')} 页)")
    print(f"  >>> eBay 总结果数: {result.get('ebay_total_results', '未知')}")
    print(f"  >>> 均价(最低N个): ${result['avg_price']:.2f}")
    print(f"{'=' * 70}")

    # 打印每个商品详情
    for i, item in enumerate(items, 1):
        ship = "FREE" if item.get("free_shipping") else f"${item.get('shipping', 0):.2f}"
        total = item.get("total", 0)
        cond = item.get("condition", "")[:15]
        iid = item.get("item_id", "")
        print(f"  [{i:2d}] ${total:<8.2f} (${item.get('price', 0):.2f}+{ship}) [{cond}] ID:{iid}  {item['title'][:65]}")

    # 保存结果
    output_file = os.path.join(os.path.dirname(__file__), "ebay", "_tmp_test_result.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n  结果已保存到: {output_file}")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
