"""
竞品详情解析器
根据 Amazon ASIN / eBay Item ID / 链接 解析产品真实信息（图片/标题/价格等）

Amazon: 通过 Playwright 访问详情页解析（复用已有浏览器实例）
eBay: 通过 eBay Browse API (getItem) 解析
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup


# ─── Amazon 详情解析（Playwright） ──────────────────────────────

def parse_amazon_asin(page, asin: str) -> dict:
    """
    用 Playwright page 对象访问 Amazon 详情页，解析产品信息。
    
    Args:
        page: Playwright Page 对象（已有浏览器实例）
        asin: Amazon ASIN (如 B0F6NKY4KZ)
    
    Returns:
        dict: {success, asin, title, price, image, rating, reviews, brand, url, error}
    """
    url = f"https://www.amazon.com/dp/{asin}"
    result = {
        "success": False,
        "asin": asin,
        "platform": "Amazon",
        "title": "",
        "price": 0,
        "currency": "USD",
        "image": "",
        "rating": 0,
        "reviews": 0,
        "brand": "",
        "url": url,
        "error": ""
    }

    for attempt in range(1, 4):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
        except Exception as e:
            if attempt < 3:
                time.sleep(3)
                continue
            result["error"] = f"页面加载失败: {e}"
            return result

        html = page.content()

        # 检测被拦截
        if _is_amazon_blocked(html):
            if attempt < 3:
                time.sleep(3)
                continue
            result["error"] = "被 Amazon 拦截（CAPTCHA）"
            return result
        break

    if not html:
        result["error"] = "页面内容为空"
        return result

    soup = BeautifulSoup(html, "html.parser")

    # 标题
    title_el = soup.select_one("#productTitle") or soup.select_one("#title span")
    if title_el:
        result["title"] = _clean(title_el.get_text())

    # 价格
    price_el = (
        soup.select_one("span.a-price > span.a-offscreen")
        or soup.select_one("#priceblock_ourprice")
        or soup.select_one("#priceblock_dealprice")
        or soup.select_one(".apexPriceToPay .a-offscreen")
    )
    if price_el:
        price_text = _clean(price_el.get_text())
        result["price"] = _parse_price(price_text) or 0
        if "$" in price_text:
            result["currency"] = "USD"

    # 评分
    rating_el = soup.select_one("#acrPopover span.a-icon-alt")
    if rating_el:
        result["rating"] = _parse_float(rating_el.get_text()) or 0

    # 评论数
    review_el = soup.select_one("#acrCustomerReviewText")
    if review_el:
        result["reviews"] = _parse_int(review_el.get_text()) or 0

    # 品牌
    brand_el = (
        soup.select_one("#bylineInfo a")
        or soup.select_one("a#bylineInfo")
    )
    if brand_el:
        brand = _clean(brand_el.get_text())
        brand = re.sub(r"^(Visit the |Brand:\s*|by\s+)", "", brand, flags=re.IGNORECASE)
        brand = re.sub(r"\s+Store$", "", brand, flags=re.IGNORECASE)
        result["brand"] = brand

    # 主图
    image_urls = []
    for img in soup.select("#imageBlock img, #landingImage, #imgBlkFront"):
        src = img.get("data-old-hires") or img.get("src") or ""
        if src and "grey-pixel" not in src and "data:" not in src:
            image_urls.append(src)
    # 从 JS 变量中提取 hiRes
    color_images = re.findall(r'"hiRes"\s*:\s*"([^"]+)"', html)
    for ci in color_images:
        if ci not in image_urls:
            image_urls.append(ci)
    if image_urls:
        result["image"] = image_urls[0]

    result["success"] = bool(result["title"])
    if not result["success"]:
        result["error"] = "未能提取到产品标题"
    return result


# ─── eBay 详情解析（Browse API） ─────────────────────────────────

class _EBayTokenCache:
    """eBay OAuth Token 缓存"""
    _token = None
    _expires_at = 0


def _get_ebay_token() -> str:
    """获取 eBay OAuth token（带缓存）"""
    import requests

    if _EBayTokenCache._token and time.time() < _EBayTokenCache._expires_at:
        return _EBayTokenCache._token

    client_id = os.environ.get("EBAY_CLIENT_ID", "") or os.environ.get("EBAY_APP_ID", "")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "") or os.environ.get("EBAY_CERT_ID", "")

    if not client_id or not client_secret:
        raise ValueError("未配置 eBay API 凭据 (EBAY_CLIENT_ID / EBAY_CLIENT_SECRET)")

    resp = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        auth=(client_id, client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _EBayTokenCache._token = data["access_token"]
    _EBayTokenCache._expires_at = time.time() + data.get("expires_in", 7200) - 60
    return _EBayTokenCache._token


def parse_ebay_item(item_id_or_url: str) -> dict:
    """
    通过 eBay Browse API 获取商品详情。
    
    Args:
        item_id_or_url: eBay 商品 ID（纯数字）或商品 URL
    
    Returns:
        dict: {success, item_id, title, price, image, rating, reviews, brand, url, error}
    """
    import requests

    result = {
        "success": False,
        "item_id": "",
        "platform": "eBay",
        "title": "",
        "price": 0,
        "currency": "USD",
        "image": "",
        "rating": 0,
        "reviews": 0,
        "brand": "",
        "url": "",
        "error": ""
    }

    # 从 URL 提取 ID
    item_id = item_id_or_url.strip()
    if "ebay." in item_id:
        m = re.search(r"/itm/(\d+)", item_id)
        if not m:
            m = re.search(r"item[=/](\d+)", item_id)
        if m:
            item_id = m.group(1)
        else:
            result["error"] = "无法从链接中提取 eBay 商品 ID"
            return result

    if not item_id.isdigit():
        result["error"] = f"无效的 eBay 商品 ID: {item_id}"
        return result

    result["item_id"] = item_id
    result["url"] = f"https://www.ebay.com/itm/{item_id}"

    try:
        token = _get_ebay_token()
        restful_id = f"v1|{item_id}|0"
        
        resp = requests.get(
            f"https://api.ebay.com/buy/browse/v1/item/{restful_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                "Content-Type": "application/json",
            },
            params={"fieldgroups": "PRODUCT"},
            timeout=15,
        )

        if resp.status_code == 404:
            result["error"] = f"商品未找到: {item_id}"
            return result
        if resp.status_code == 400:
            err = resp.json().get("errors", [{}])
            result["error"] = f"请求错误: {err}"
            return result
        resp.raise_for_status()

        data = resp.json()

        result["title"] = data.get("title", "")
        result["url"] = data.get("itemWebUrl", result["url"])

        # 价格
        price_obj = data.get("price", {})
        if price_obj:
            result["price"] = float(price_obj.get("value", 0))
            result["currency"] = price_obj.get("currency", "USD")

        # 图片
        img_obj = data.get("image", {})
        if img_obj:
            result["image"] = img_obj.get("imageUrl", "")

        # 品牌
        result["brand"] = data.get("brand", "")

        # 评分
        review = data.get("primaryProductReviewRating", {})
        if review:
            result["rating"] = float(review.get("averageRating", 0) or 0)
            result["reviews"] = int(review.get("reviewCount", 0) or 0)

        # 商品状况
        result["condition"] = data.get("condition", "")

        result["success"] = bool(result["title"])
        if not result["success"]:
            result["error"] = "未能提取到产品标题"

    except ValueError as e:
        result["error"] = str(e)
    except Exception as e:
        result["error"] = f"API 请求失败: {e}"

    return result


# ─── 工具函数 ──────────────────────────────────────────────────

def _clean(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _parse_float(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"(\d+\.?\d*)", text)
    return float(m.group(1)) if m else None


def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"(\d[\d,]*\.?\d*)", text)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def _parse_int(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"([\d.]+)\s*([KM])?", text, re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").upper()
    if suffix == "K":
        num *= 1_000
    elif suffix == "M":
        num *= 1_000_000
    return int(num)


def _is_amazon_blocked(html: str) -> bool:
    lower = html.lower()
    tokens = [
        "enter the characters you see below",
        "sorry, we just need to make sure you're not a robot",
        "to discuss automated access to amazon data",
    ]
    return any(t in lower for t in tokens)
