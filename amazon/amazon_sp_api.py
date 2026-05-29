"""
Amazon SP-API Catalog Items API v0 客户端
文档: https://spapi.cyou/zh/references/catalog-items-api-v0-reference.html
接口: GET /catalog/v0/items  (listCatalogItems)
"""

import os
import time
import json
import base64
import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone
from functools import lru_cache

import requests
from dotenv import load_dotenv

load_dotenv()

# ── 凭证 ──────────────────────────────────────────────────────────────────────
LWA_CLIENT_ID     = os.getenv("Amazon_APP_ID", "").strip()
LWA_CLIENT_SECRET = os.getenv("Amazon_APP_SECRET", "").strip()
REFRESH_TOKEN     = os.getenv("Amazon_APP_TOKEN", "").strip()

# SP-API 固定配置
SP_API_BASE       = "https://sellingpartnerapi-na.amazon.com"
REGION            = "us-east-1"          # 北美

# 主要 Marketplace ID
MARKETPLACE_IDS = {
    "US": "ATVPDKIKX0DER",
    "CA": "A2EUQ1WTGCTBG2",
    "MX": "A1AM78C64UM0Y8",
    "BR": "A2Q3Y263D00KWC",
    "UK": "A1F83G8C2ARO7P",
    "DE": "A1PA6795UKMFR9",
    "FR": "A13V1IB3VIYBER",
    "IT": "APJ6JRA9NG5V4",
    "ES": "A1RKKUPIHCS9HS",
    "NL": "A180ACIZD8BO8A",
    "SE": "A2NODRKZP88ZB9",
    "PL": "A1C3SOZRARQ6R3",
    "JP": "A1VC38T7YXB528",
    "AU": "A39IBJ37TRP1C6",
    "SG": "A19VVU5S5SG8QW",
    "AE": "A2VIGQ35RCS4UG",
    "IN": "A21TJRUUN4KGV",
}

DEFAULT_MARKETPLACE = "US"

# ── LWA 授权 ──────────────────────────────────────────────────────────────────

_ACCESS_TOKEN_CACHE = {}


def get_access_token(force_refresh=False):
    """使用 Refresh Token 获取 LWA Access Token，带内存缓存（有效期约1小时）"""
    global _ACCESS_TOKEN_CACHE
    now = time.time()

    if not force_refresh and _ACCESS_TOKEN_CACHE:
        exp, token = _ACCESS_TOKEN_CACHE.get("exp"), _ACCESS_TOKEN_CACHE.get("token")
        if exp and token and now < exp - 60:
            return token

    if not LWA_CLIENT_ID or not LWA_CLIENT_SECRET or not REFRESH_TOKEN:
        raise RuntimeError(
            "缺少 SP-API 凭证，请确保 .env 包含: "
            "Amazon_APP_ID, Amazon_APP_SECRET, Amazon_APP_TOKEN"
        )

    resp = requests.post(
        "https://api.amazon.com/auth/o2/token",
        data={
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id":     LWA_CLIENT_ID,
            "client_secret": LWA_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    _ACCESS_TOKEN_CACHE = {
        "token": data["access_token"],
        "exp":   now + data.get("expires_in", 3600),
    }
    return _ACCESS_TOKEN_CACHE["token"]


# ── STS 签名 (sts:AssumeRole) ──────────────────────────────────────────────────
# 当 LWA 凭证不足以调用某些 API 时使用；catalog v0 通常只需 LWA Token
# 如后续需调用需要 RDS 代理的 API，在此补充 sts_sign()


def _sts_sign(method, url, region, secret_key, token=None):
    """生成 AWS SigV4 签名 (用于需要 STS 的 API)"""
    t = datetime.now(timezone.utc)
    amz_date  = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")

    parsed = urllib.parse.urlparse(url)
    canonical_uri  = urllib.parse.quote(parsed.path, safe="/")
    canonical_qs   = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted(urllib.parse.parse_qsl(parsed.query))
    )
    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical_headers = (
        f"host:{parsed.netloc}\n"
        f"x-amz-date:{amz_date}\n"
        + (f"x-amz-security-token:{token}\n" if token else "")
    )
    signed_headers = "host;x-amz-date" + (";x-amz-security-token" if token else "")

    canonical_req = (
        f"{method}\n{canonical_uri}\n{canonical_qs}\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    canonical_hash = hashlib.sha256(canonical_req.encode()).hexdigest()

    algo    = "AWS4-HMAC-SHA256"
    scope   = f"{date_stamp}/{region}/sts/aws4_request"
    str_req = f"{algo}\n{amz_date}\n{scope}\n{canonical_hash}"

    def _hmac_sha256(key, data):
        return hmac.new(key, data.encode(), hashlib.sha256).digest()

    k_date  = _hmac_sha256(b"AWS4" + secret_key.encode(), date_stamp)
    k_reg   = _hmac_sha256(k_date, region)
    k_svc   = _hmac_sha256(k_reg, "sts")
    k_sign  = _hmac_sha256(k_svc, "aws4_request")
    signature = hmac.new(k_sign, str_req.encode(), hashlib.sha256).hexdigest()

    auth = (
        f"{algo} "
        f"Credential={LWA_CLIENT_ID}/{scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return {"x-amz-date": amz_date, "Authorization": auth}


# ── 核心请求 ──────────────────────────────────────────────────────────────────

def _call_spapi(method, path, params=None, body=None, marketplace=DEFAULT_MARKETPLACE):
    """
    通用 SP-API 请求，自动处理:
    - LWA Access Token
    - X-Amz-Target (Seller Partner 特定操作头)
    - Rate-Limit 重试 (429)
    """
    base_url = f"https://sellingpartnerapi-na.amazon.com"
    url = base_url + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    token = get_access_token()

    headers = {
        "Content-Type":                 "application/json",
        "Accept":                       "application/json",
        "x-amz-access-token":          token,
        # catalog v0 不需要 x-amz-target；某些 Seller Central API 需要
        # "x-amz-target": "com.amazon.sellingpartnerapi.a2021datawrapper.productMasterForSP.API.Create",
    }

    max_retries = 3
    for attempt in range(max_retries):
        resp = requests.request(
            method,
            url,
            headers=headers,
            json=body,
            timeout=30,
        )

        if resp.status_code == 429:
            # 读取 Retry-After 或等待
            retry_after = int(resp.headers.get("Retry-After", 5))
            print(f"[SP-API] Rate limit hit, retry after {retry_after}s (attempt {attempt + 1})")
            time.sleep(retry_after)
            # 强制刷新 token 避免 401 触发重试循环
            get_access_token(force_refresh=True)
            continue

        if resp.status_code == 401:
            if attempt < max_retries - 1:
                get_access_token(force_refresh=True)
                continue
            raise RuntimeError(f"SP-API 401 Unauthorized: {resp.text}")

        resp.raise_for_status()
        return resp

    raise RuntimeError(f"SP-API request failed after {max_retries} retries")


# ── Catalog Items API v0 ──────────────────────────────────────────────────────

def list_catalog_items(
    keywords: str,
    marketplace: str = DEFAULT_MARKETPLACE,
    page_size: int = 10,
    included_type: str = "APPLICABLE",
) -> dict:
    """
    关键词搜索亚马逊目录商品 (listCatalogItems)

    文档: GET /catalog/v0/items
    参数:
        keywords       - 搜索关键词
        marketplace    - 市场代码，如 "US", "UK", "DE"
        page_size      - 每页数量 (最大10)
        included_type  - "APPLICABLE" / "FULL" (返回完整属性)

    返回:
        {
            "success": True,
            "items": [...],
            "total": N,
            "marketplace": "ATVPDKIKX0DER",
            "headers": {...},
            "raw": {...}
        }
    """
    marketplace_id = MARKETPLACE_IDS.get(marketplace.upper(), MARKETPLACE_IDS["US"])

    params = {
        "MarketplaceId": marketplace_id,
        "Query":         keywords,
        "IncludedType":  included_type,
    }

    resp = _call_spapi("GET", "/catalog/v0/items", params=params)

    result = resp.json()
    headers_out = {
        "x-amzn-RateLimit-Limit":  resp.headers.get("x-amzn-RateLimit-Limit"),
        "x-amzn-RequestId":        resp.headers.get("x-amzn-RequestId"),
    }

    payload = result.get("payload", {})
    items_raw = payload.get("Items", []) if payload else []

    # 规范化字段
    items = [_normalize_item(it) for it in items_raw]

    return {
        "success":     True,
        "items":       items,
        "total":       len(items),
        "marketplace": marketplace.upper(),
        "marketplace_id": marketplace_id,
        "rate_limit":  headers_out["x-amzn-RateLimit-Limit"],
        "request_id":  headers_out["x-amzn-RequestId"],
        "raw":         payload,
    }


def get_catalog_item(asin: str, marketplace: str = DEFAULT_MARKETPLACE) -> dict:
    """
    根据 ASIN 获取单个商品详情 (getCatalogItem)

    文档: GET /catalog/v0/items/{asin}
    """
    marketplace_id = MARKETPLACE_IDS.get(marketplace.upper(), MARKETPLACE_IDS["US"])

    params = {"MarketplaceId": marketplace_id}
    resp = _call_spapi("GET", f"/catalog/v0/items/{asin}", params=params)

    result = resp.json()
    payload = result.get("payload", {})

    return {
        "success":      True,
        "item":         _normalize_item(payload) if payload else None,
        "marketplace":  marketplace.upper(),
        "marketplace_id": marketplace_id,
        "raw":          payload,
    }


def _normalize_item(raw: dict) -> dict:
    """
    将 catalog API 返回的 Item 对象规范化为易用结构
    """
    # ── Identifiers ──
    identifiers = {}
    ident_type  = raw.get("Identifiers", {})
    mkt_asin    = ident_type.get("MarketplaceASIN", {})
    if mkt_asin:
        identifiers["asin"]         = mkt_asin.get("ASIN", "")
        identifiers["marketplace"]  = mkt_asin.get("MarketplaceId", "")

    sku_ident   = ident_type.get("SKUIdentifier", {})
    if sku_ident:
        identifiers["sku"]          = sku_ident.get("SellerSKU", "")
        identifiers["seller_id"]    = sku_ident.get("SellerId", "")

    # ── AttributeSets ──
    attr_sets  = raw.get("AttributeSets", []) or []
    attrs      = {}
    if attr_sets:
        a = attr_sets[0]
        attrs = {
            "title":                 a.get("Title", ""),
            "brand":                 a.get("Brand", ""),
            "manufacturer":         a.get("Manufacturer", ""),
            "product_group":         a.get("ProductGroup", ""),
            "product_type":          a.get("ProductTypeName", ""),
            "part_number":           a.get("ItemPartNumber", ""),
            "model":                 a.get("Model", ""),
            "color":                 a.get("Color", ""),
            "size":                  a.get("Size", ""),
            "binding":               a.get("Binding", ""),
            "features":              a.get("Features") or a.get("Feature", []),
            "package_quantity":      a.get("PackageQuantity", 1),
            "list_price_amount":     _get_price_amount(a.get("ListPrice")),
            "list_price_currency":   _get_price_currency(a.get("ListPrice")),
            "small_image_url":       _get_image_url(a.get("SmallImage")),
            "item_dimensions":       _get_dimensions(a.get("ItemDimensions")),
            "package_dimensions":    _get_dimensions(a.get("PackageDimensions")),
            "weight":                _get_weight(a.get("ItemDimensions")),
        }

    # ── SalesRankings ──
    rankings = []
    for rank_entry in (raw.get("SalesRankings") or []):
        rankings.append({
            "category_id":   rank_entry.get("ProductCategoryId", ""),
            "rank":          rank_entry.get("Rank", 0),
        })

    # ── Relationships (变体) ──
    variants = []
    for rel in (raw.get("Relationships") or []):
        var_ident = rel.get("Identifiers", {})
        var_asin  = (var_ident.get("MarketplaceASIN") or {}).get("ASIN", "")
        variants.append({
            "asin":  var_asin,
            "color": rel.get("Color", ""),
            "size":  rel.get("Size", ""),
        })

    return {
        "identifiers":    identifiers,
        "attributes":     attrs,
        "sales_rankings": rankings,
        "relationships":  variants,
    }


def _get_price_amount(price_obj) -> float:
    if not price_obj:
        return None
    val = price_obj.get("Amount") or price_obj.get("value") or price_obj.get("amount")
    return float(val) if val else None


def _get_price_currency(price_obj) -> str:
    if not price_obj:
        return None
    return price_obj.get("CurrencyCode", "") or ""


def _get_image_url(img_obj) -> str:
    if not img_obj:
        return ""
    return img_obj.get("URL", "") or ""


def _get_dimensions(dim_obj) -> dict:
    if not dim_obj:
        return {}
    def _du(d):
        return {"value": d.get("value"), "units": d.get("Units", "")} if d else None
    return {
        "height": _du(dim_obj.get("Height")),
        "length": _du(dim_obj.get("Length")),
        "width":  _du(dim_obj.get("Width")),
    }


def _get_weight(dim_obj) -> dict:
    if not dim_obj:
        return {}
    w = dim_obj.get("Weight")
    if not w:
        return {}
    return {"value": w.get("value"), "units": w.get("Units", "")}


# ── 入口：CLI 测试 ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    keyword = sys.argv[1] if len(sys.argv) > 1 else "wireless earbuds"
    market  = sys.argv[2].upper() if len(sys.argv) > 2 else "US"

    print(f"\n[SP-API] listCatalogItems | keyword='{keyword}' | marketplace={market}\n")

    try:
        result = list_catalog_items(keyword, marketplace=market)
        items  = result.get("items", [])

        print(f"  Total: {result['total']}  |  RateLimit: {result.get('rate_limit', 'N/A')}\n")
        print("-" * 90)

        for i, item in enumerate(items, 1):
            attrs = item.get("attributes", {})
            price = attrs.get("list_price_amount")
            rank  = item.get("sales_rankings", [{}])
            rank_str = f"BSSR#{rank[0]['rank']}" if rank and rank[0].get("rank") else ""

            print(
                f"  [{i:2d}] ASIN: {item['identifiers'].get('asin', 'N/A'):10s}  "
                f"{'(' + rank_str + ')':15s}  "
                f"{'$' + str(price) if price else 'N/A price':>12s}  "
                f"{attrs.get('brand', '')}  "
                f"{attrs.get('title', '')[:50]}"
            )

        print("-" * 90)

        # 同时尝试 getCatalogItem (用第一个 ASIN)
        if items:
            asin = items[0]["identifiers"].get("asin")
            print(f"\n[SP-API] getCatalogItem | ASIN={asin}\n")
            detail = get_catalog_item(asin, marketplace=market)
            it = detail.get("item") or {}
            print("  Title:", it.get("attributes", {}).get("title", "N/A"))
            print("  Brand:", it.get("attributes", {}).get("brand", "N/A"))
            print("  Rank :", it.get("sales_rankings", [{}])[0].get("rank", "N/A"))

    except Exception as e:
        import traceback
        print(f"\nError: {e}")
        traceback.print_exc()
