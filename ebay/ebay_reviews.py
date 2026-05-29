"""
eBay 商品评论爬虫 - 传统HTTP请求版
使用 requests + BeautifulSoup 抓取指定 Item ID 的评论/评价
无需 Playwright，无需 CDP 连接

eBay 评论有两种来源：
  1. 商品 Product Reviews（部分商品有）— /reviews 子页
  2. 卖家 Feedback（买家对该商品交易的评价）— /fdbk/feedback_profile
"""
import re
import json
import time
import random
from typing import Optional

import requests
from bs4 import BeautifulSoup


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]


def get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }


def make_session() -> requests.Session:
    s = requests.Session()
    s.cookies.set("nonsession", "BAQAAAXXfake" + str(random.randint(10000, 99999)), domain=".ebay.com")
    return s


def fetch_page(session: requests.Session, url: str, retries: int = 3) -> Optional[BeautifulSoup]:
    """发起GET请求，返回BeautifulSoup，失败重试"""
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=get_headers(), timeout=20, allow_redirects=True)
            if resp.status_code == 200:
                if "signin" in resp.url or "SignIn" in resp.url:
                    print(f"[eBay评论] 被重定向到登录页，跳过")
                    return None
                return BeautifulSoup(resp.content, "html.parser")
            elif resp.status_code in (429, 503):
                wait = 10 * (attempt + 1)
                print(f"[eBay评论] HTTP {resp.status_code}，等待 {wait}s 后重试")
                time.sleep(wait)
            elif resp.status_code == 404:
                print(f"[eBay评论] 404 页面不存在: {url}")
                return None
            else:
                print(f"[eBay评论] HTTP {resp.status_code}，重试")
                time.sleep(5)
        except requests.exceptions.RequestException as e:
            print(f"[eBay评论] 请求异常 (尝试 {attempt+1}/{retries}): {e}")
            time.sleep(random.uniform(3, 7))
    return None


# ─────────────────────────────────────────────────────────────
# 方法一：Product Reviews 页面（部分商品有 /p/ 类商品聚合评论）
# URL: https://www.ebay.com/urw/{item_id}/product-reviews
# ─────────────────────────────────────────────────────────────

def scrape_product_reviews(session: requests.Session, item_id: str, max_pages: int = 5) -> tuple:
    """
    尝试抓取 eBay 商品聚合评论 (Product Reviews)
    返回 (summary_dict, reviews_list)
    """
    summary = {"total": 0, "avg_rating": 0.0, "star_pcts": {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}}
    reviews = []

    # eBay product reviews URL
    base_url = f"https://www.ebay.com/urw/{item_id}/product-reviews"
    print(f"[eBay评论] 尝试Product Reviews: {base_url}")

    soup = fetch_page(session, base_url)
    if soup is None:
        return summary, reviews

    # 检查是否有评论内容
    review_items = _parse_product_review_soup(soup)
    if not review_items:
        print("[eBay评论] Product Reviews 页无内容")
        return summary, reviews

    # 解析汇总
    try:
        avg_el = soup.find("span", class_=re.compile(r"reviews-star-rating|average-rating|averageRating", re.I))
        if not avg_el:
            avg_el = soup.find(attrs={"data-testid": re.compile(r"avg|average|rating", re.I)})
        if avg_el:
            m = re.search(r"([\d.]+)", avg_el.get_text())
            if m:
                summary["avg_rating"] = float(m.group(1))

        total_el = soup.find(string=re.compile(r"\d+\s+(product\s+)?ratings?", re.I))
        if total_el:
            m = re.search(r"([\d,]+)", total_el)
            if m:
                summary["total"] = int(m.group(1).replace(",", ""))
    except Exception:
        pass

    reviews.extend(review_items)
    print(f"[eBay评论] Product Reviews 第1页: {len(review_items)}条")

    # 翻页
    for pg in range(2, max_pages + 1):
        page_url = f"{base_url}?pgn={pg}"
        soup = fetch_page(session, page_url)
        if soup is None:
            break
        page_items = _parse_product_review_soup(soup)
        if not page_items:
            print(f"[eBay评论] 第{pg}页无评论，停止")
            break
        reviews.extend(page_items)
        print(f"[eBay评论] Product Reviews 第{pg}页: {len(page_items)}条")
        time.sleep(random.uniform(1.5, 3.0))

    if not summary["total"]:
        summary["total"] = len(reviews)

    return summary, reviews


def _parse_product_review_soup(soup: BeautifulSoup) -> list:
    """解析 Product Reviews 页面"""
    reviews = []

    # eBay product review 容器（多版本选择器）
    containers = (
        soup.find_all("div", class_=re.compile(r"review-item|review-entry|ebay-review", re.I))
        or soup.find_all(attrs={"data-testid": re.compile(r"review", re.I)})
        or soup.find_all("div", class_=re.compile(r"\breview\b", re.I))
    )

    for item in containers:
        r = {}

        # 评分
        star_el = item.find(attrs={"aria-label": re.compile(r"\d[\s.]+out of \d|stars?", re.I)})
        if not star_el:
            star_el = item.find(class_=re.compile(r"stars?|rating", re.I))
        if star_el:
            label = star_el.get("aria-label", "") or star_el.get_text()
            m = re.search(r"([\d.]+)", label)
            r["rating"] = float(m.group(1)) if m else 0
        else:
            r["rating"] = 0

        # 标题
        title_el = item.find(["h3", "h4"], class_=re.compile(r"title|heading", re.I)) or item.find(class_=re.compile(r"review.?title", re.I))
        r["title"] = title_el.get_text(strip=True) if title_el else ""

        # 日期
        date_el = item.find("time") or item.find(class_=re.compile(r"date|time", re.I))
        r["date"] = (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else ""

        # 正文
        body_el = item.find(class_=re.compile(r"review.?body|review.?text|review.?content|description", re.I)) or item.find("p")
        r["body"] = body_el.get_text(separator=" ", strip=True) if body_el else ""

        r["verified"] = False
        r["helpful"] = ""

        if r.get("body") or r.get("title"):
            reviews.append(r)

    return reviews


# ─────────────────────────────────────────────────────────────
# 方法二：Seller Feedback（买家对该订单交易的评价）
# URL: https://www.ebay.com/fdbk/feedback_profile?item_id={id}
# ─────────────────────────────────────────────────────────────

def scrape_seller_feedback(session: requests.Session, item_id: str, max_pages: int = 5) -> tuple:
    """
    抓取 eBay 卖家 Feedback（与该商品相关的买家反馈）
    返回 (summary_dict, reviews_list)
    """
    summary = {"total": 0, "avg_rating": 0.0, "star_pcts": {}}
    reviews = []

    for pg in range(1, max_pages + 1):
        url = (
            f"https://www.ebay.com/fdbk/feedback_profile"
            f"?item_id={item_id}&_pgn={pg}&_rdc=1&filter=positive"
        )
        # 也抓负面反馈
        neg_url = (
            f"https://www.ebay.com/fdbk/feedback_profile"
            f"?item_id={item_id}&_pgn={pg}&_rdc=1&filter=negative"
        )

        for furl, label in [(neg_url, "负面"), (url, "正面")]:
            print(f"[eBay评论] Feedback {label} 第{pg}页: {furl}")
            soup = fetch_page(session, furl)
            if soup is None:
                continue

            page_items = _parse_feedback_soup(soup)
            reviews.extend(page_items)
            print(f"[eBay评论] Feedback {label} 第{pg}页: {len(page_items)}条")

            if pg == 1 and not summary["total"]:
                # 尝试解析总数
                try:
                    count_el = soup.find(string=re.compile(r"\d+\s+feedback", re.I))
                    if count_el:
                        m = re.search(r"([\d,]+)", count_el)
                        if m:
                            summary["total"] = int(m.group(1).replace(",", ""))
                except Exception:
                    pass

        if len(reviews) >= max_pages * 20:
            break
        time.sleep(random.uniform(1.5, 2.5))

    if not summary["total"]:
        summary["total"] = len(reviews)

    return summary, reviews


def _parse_feedback_soup(soup: BeautifulSoup) -> list:
    """解析 Feedback 页面评价列表"""
    reviews = []

    # eBay feedback 行容器
    rows = (
        soup.find_all("tr", class_=re.compile(r"feedback", re.I))
        or soup.find_all("div", class_=re.compile(r"feedback.?row|feedback.?item|fb-row", re.I))
        or soup.find_all("li", class_=re.compile(r"feedback", re.I))
    )

    if not rows:
        # 通用：抓 .feedback-table tbody 里的 tr
        table = soup.find("table", class_=re.compile(r"feedback", re.I))
        if table:
            rows = table.find_all("tr")[1:]  # 跳过表头

    for row in rows:
        text = row.get_text(separator=" ", strip=True)
        if not text or len(text) < 5:
            continue

        # 判断正负面
        rating = 0
        cls_str = " ".join(row.get("class", []))
        if re.search(r"positive", cls_str, re.I):
            rating = 5
        elif re.search(r"negative", cls_str, re.I):
            rating = 1
        elif re.search(r"neutral", cls_str, re.I):
            rating = 3
        else:
            # 尝试从图标 alt/title 推断
            img = row.find("img", alt=re.compile(r"positive|negative|neutral", re.I))
            if img:
                alt = img.get("alt", "").lower()
                if "positive" in alt:
                    rating = 5
                elif "negative" in alt:
                    rating = 1
                elif "neutral" in alt:
                    rating = 3

        # 日期
        date = ""
        date_el = row.find("td", class_=re.compile(r"date|time", re.I)) or row.find("span", class_=re.compile(r"date|time", re.I))
        if date_el:
            date = date_el.get_text(strip=True)

        reviews.append({
            "rating": rating,
            "title": "",
            "body": text[:400],
            "date": date,
            "verified": True,
            "helpful": "",
        })

    return reviews


# ─────────────────────────────────────────────────────────────
# 方法三：从商品详情页抓取评分汇总 + 标题
# ─────────────────────────────────────────────────────────────

def fetch_item_meta(session: requests.Session, item_id: str) -> dict:
    """从商品详情页抓取标题、评分汇总"""
    meta = {"title": "", "avg_rating": 0.0, "total_reviews": 0}
    url = f"https://www.ebay.com/itm/{item_id}"
    print(f"[eBay评论] 抓取商品Meta: {url}")
    soup = fetch_page(session, url)
    if soup is None:
        return meta

    # 标题
    title_el = (
        soup.find("h1", class_=re.compile(r"title", re.I))
        or soup.find("h1", {"id": "itemTitle"})
        or soup.find("h1")
    )
    if title_el:
        meta["title"] = title_el.get_text(strip=True)[:200]

    # 评分
    rating_el = soup.find(attrs={"itemprop": "ratingValue"}) or soup.find(class_=re.compile(r"star-rating|average-rating|reviewCount", re.I))
    if rating_el:
        m = re.search(r"([\d.]+)", rating_el.get("content", "") or rating_el.get_text())
        if m:
            meta["avg_rating"] = float(m.group(1))

    # 评论总数
    count_el = soup.find(attrs={"itemprop": "reviewCount"}) or soup.find(class_=re.compile(r"review.?count|rating.?count", re.I))
    if count_el:
        m = re.search(r"([\d,]+)", count_el.get("content", "") or count_el.get_text())
        if m:
            meta["total_reviews"] = int(m.group(1).replace(",", ""))

    return meta


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────

def scrape_ebay_reviews(
    item_id: str,
    max_pages: int = 5,
    cdp_url: Optional[str] = None,   # 保留参数兼容旧接口，不使用
) -> dict:
    """
    用传统HTTP请求抓取eBay商品评论/评价
    :param item_id: eBay商品ID（纯数字）
    :param max_pages: 最大抓取页数
    :param cdp_url: 已废弃，保留兼容
    :return: {'success': bool, 'item_id': str, 'item_title': str, 'summary': dict, 'reviews': list, 'error': str}
    """
    session = make_session()
    all_reviews = []
    summary = {}
    item_title = ""

    try:
        # Step 1: 获取商品 Meta（标题 + 评分汇总）
        meta = fetch_item_meta(session, item_id)
        item_title = meta.get("title", "")
        time.sleep(random.uniform(1.5, 2.5))

        # Step 2: 尝试抓取 Product Reviews
        pr_summary, pr_reviews = scrape_product_reviews(session, item_id, max_pages=max_pages)
        if pr_reviews:
            print(f"[eBay评论] ✅ Product Reviews 找到 {len(pr_reviews)} 条")
            summary = pr_summary
            all_reviews = pr_reviews
        else:
            # Step 3: Fallback 到 Seller Feedback
            print(f"[eBay评论] Product Reviews 无内容，切换到 Seller Feedback...")
            time.sleep(random.uniform(1.0, 2.0))
            fb_summary, fb_reviews = scrape_seller_feedback(session, item_id, max_pages=max_pages)
            summary = fb_summary
            all_reviews = fb_reviews
            if all_reviews:
                print(f"[eBay评论] ✅ Seller Feedback 找到 {len(all_reviews)} 条")
            else:
                print(f"[eBay评论] ⚠️ 未找到任何评论")

        # 补充 Meta 中的评分
        if not summary.get("avg_rating") and meta.get("avg_rating"):
            summary["avg_rating"] = meta["avg_rating"]
        if not summary.get("total") and meta.get("total_reviews"):
            summary["total"] = meta["total_reviews"]

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "item_id": item_id,
            "error": str(e)[:300],
            "reviews": [],
            "summary": {},
        }

    if not all_reviews:
        return {
            "success": False,
            "item_id": item_id,
            "item_title": item_title,
            "error": "未能抓取到评论数据。eBay部分商品评论需要登录或使用浏览器访问，可尝试稍后重试",
            "reviews": [],
            "summary": summary,
        }

    return {
        "success": True,
        "item_id": item_id,
        "item_title": item_title,
        "summary": summary,
        "reviews": all_reviews,
        "total_fetched": len(all_reviews),
        "error": "",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="eBay商品评论抓取")
    parser.add_argument("item_id", help="eBay Item ID，如 123456789012")
    parser.add_argument("--pages", type=int, default=5, help="最多抓取页数（默认5）")
    args = parser.parse_args()

    result = scrape_ebay_reviews(args.item_id, max_pages=args.pages)
    print(json.dumps(result, ensure_ascii=False, indent=2))
