r"""
eBay Browse API - 产品搜索模块
支持关键词 / GTIN / ePID / 分类ID 搜索，以及丰富的筛选参数

文档:     https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search
过滤器:   https://developer.ebay.com/api-docs/buy/static/ref-buy-browse-filters.html
字段索引: https://developer.ebay.com/api-docs/buy/browse/fields

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
支持的搜索条件（至少提供一个）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  -q / --query          关键词搜索（最多 100 字符）
  --gtin                GTIN（UPC/EAN/ISBN）
  --epid                eBay Product ID
  --category            分类 ID（叶子节点分类效果最佳）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
支持的筛选参数（可组合使用）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  --price-min           最低价格（USD）
  --price-max           最高价格（USD）
  --price-currency      货币代码，默认 USD（USD/GBP/EUR/AUD/CAD/CHF 等）

  --condition           商品状况 ID，支持多次指定
                        1000 = NEW（全新）
                        1500 = OPEN_BOX（开封）/ LIKE_NEW
                        2000 = VERY_GOOD / SELLER_REFURBISHED
                        2500 = GOOD
                        3000 = ACCEPTABLE

  --buying-options      销售方式，支持多次指定
                        FIXED_PRICE   = 一口价（固定价格）
                        AUCTION       = 拍卖
                        BEST_OFFER    = 可议价
                        CLASSIFIED_AD = 分类广告

  --bid-count-min       拍卖最少竞拍数
  --bid-count-max       拍卖最多竞拍数

  --free-shipping       只显示包邮商品
  --credit-card         只显示支持信用卡支付的商品
  --returns-accepted    只显示接受退货的商品

  --location-country     商品所在国家（ISO 3166 两字母代码，如 US/CN/GB/DE）
  --location-region      商品所在区域（按站点不同，见下表）

  --seller-type          卖家类型：BUSINESS（企业）或 INDIVIDUAL（个人）

  --priority-listing    只显示推广商品（Priority Listing）
  --charity             只显示慈善商品

  --search-in-description 在商品描述中也搜索关键词（默认仅搜索标题）

  --qualified-program    合格项目，支持多次指定
                        EBAY_PLUS              eBay Plus（仅德国/奥地利/澳大利亚）
                        AUTHENTICITY_GUARANTEE 正品保障（仅美国）
                        AUTHENTICITY_VERIFICATION 正品验证

  --exclude-category    排除的分类 ID，支持多次指定

  --delivery-country     配送目的国家代码
  --delivery-postal-code 配送目的邮编

  --last-sold-start     最后售出时间下限（ISO 8601，如 2024-01-01T00:00:00Z）
  --last-sold-end       最后售出时间上限

  --listing-start       上架开始时间（ISO 8601）
  --listing-end         上架结束时间（ISO 8601）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
--location-region 有效值（按站点）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  EBAY_US  → NORTH_AMERICA, WORLDWIDE
  EBAY_GB  → EUROPEAN_UNION, CONTINENTAL_EUROPE, WORLDWIDE
  EBAY_DE  → EUROPEAN_UNION, CONTINENTAL_EUROPE, WORLDWIDE
  EBAY_CA  → NORTH_AMERICA, WORLDWIDE
  EBAY_AU  → WORLDWIDE
  EBAY_FR  → EUROPEAN_UNION, CONTINENTAL_EUROPE, BORDER_COUNTRIES, WORLDWIDE
  EBAY_IT  → EUROPEAN_UNION, CONTINENTAL_EUROPE, WORLDWIDE
  EBAY_ES  → EUROPEAN_UNION, CONTINENTAL_EUROPE, WORLDWIDE
  EBAY_BE  → EUROPEAN_UNION, CONTINENTAL_EUROPE, WORLDWIDE
  EBAY_AT  → EUROPEAN_UNION, CONTINENTAL_EUROPE, WORLDWIDE
  EBAY_CH  → EUROPEAN_UNION, CONTINENTAL_EUROPE, WORLDWIDE
  EBAY_NL  → EUROPEAN_UNION, CONTINENTAL_EUROPE, WORLDWIDE
  EBAY_PL  → EUROPEAN_UNION, CONTINENTAL_EUROPE, WORLDWIDE
  EBAY_IE  → EUROPEAN_UNION, CONTINENTAL_EUROPE, UK_AND_IRELAND, WORLDWIDE
  EBAY_SG  → ASIA, WORLDWIDE
  EBAY_HK  → ASIA, BORDER_COUNTRIES, WORLDWIDE

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
排序选项（--sort）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BestMatch              最佳匹配（默认）
  PricePlusShippingLowest 价格+运费：低到高
  PricePlusShippingHighest 价格+运费：高到低
  Price                  价格：低到高
  PriceHigh              价格：高到低
  StartTimeNewest        最新上架
  EndTimeSoonest         即将结束
  BestMatchCategoryDefault 分类最佳匹配

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
返回字段说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  itemId              eBay 商品 ID（RESTful 格式 v1|数字|0）
  legacyItemId         传统纯数字 ID
  title               商品标题
  price.value         价格数值
  price.currency      价格货币（USD/GBP 等）
  condition           商品状况文字描述
  conditionId         商品状况 ID（数值）
  image.imageUrl      主图 URL
  itemWebUrl          eBay 商品页面 URL
  seller.username      卖家用户名
  seller.feedbackScore 卖家反馈评分
  seller.feedbackPercentage 卖家好评率
  shippingOptions[].shippingCost.value 运费
  buyingOptions[]      销售方式列表
  categories[].categoryId      分类 ID
  categories[].categoryName     分类名称 
  topRatedBuyingExperience  是否为顶级卖家
  availableCoupons    是否可用优惠券
  itemCreationDate    上架时间（ISO 8601）
  itemEndDate         结束时间（ISO 8601）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
使用示例
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  # 1. 基础搜索 - 关键词 + 价格区间 + 全新 + 包邮


  # 2. 搜索 GTIN（UPC/EAN/ISBN）
  python ebay_search.py --gtin "012345678901"

  # 3. 按分类搜索 + 排序
  python ebay_search.py --category 9355 -q "headphones" --sort Price

  # 4. 搜索拍卖商品（可设置竞拍数下限）
  python ebay_search.py -q "laptop" --buying-options AUCTION \\
      --bid-count-min 3 --sort EndTimeSoonest

  # 5. 搜索企业卖家 + 高质量商品
  python ebay_search.py -q "iphone case" --condition 1000 --condition 1500 \\
      --seller-type BUSINESS --returns-accepted --sort Price

  # 6. 使用沙箱环境
  python ebay_search.py -q "test product" --sandbox --limit 10

  # 7. 切换站点 + 多条件筛选
  python ebay_search.py -q "sneakers" --marketplace EBAY_GB \\
      --price-min 20 --price-max 200 --condition 1000 \\
      --location-country GB --sort Price

  # 8. 自动翻页获取大量数据
  python ebay_search.py -q "smart watch" --limit 100 --max-items 5000 \\
      --price-min 50 --free-shipping --sort BestMatch

  # 9. 排除特定分类 + 搜索描述
  python ebay_search.py -q "watch" --exclude-category 10367 \\
      --search-in-description --condition 1000

  # 10. 搜索结果保存到文件
  python ebay_search.py -q "wireless earbuds" --limit 50 \\
      --price-min 10 --price-max 100 --output search_results.json

  # 11. 查看可用属性（细化选项）
  python ebay_search.py -q "headphones" --fieldgroups ASPECT_REFINEMENTS,CONDITION_REFINEMENTS

  # 12. 查询商品评论数据（需提供商品ID）
  python ebay_search.py --reviews --item-id "v1|314505929469|0"
  python ebay_search.py --reviews --item-id "314505929469" --sandbox

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
评论数据（--reviews --item-id）返回字段说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  average_rating        商品平均评分（1-5 分，例：4.76）
  review_count          评论总数（例：142）
  rating_histograms      评分分布（例：[{"rating":"5","count":120},...]）
  seller_username       卖家用户名
  seller_feedback_score 卖家反馈评分（例：137720）
  seller_positive_percent 好评率（例：99.7）
  top_rated             是否为顶级卖家（True/False）
  brand                 品牌
  gtin                  GTIN 码
  condition             商品状况文字
  price_value + price_currency 价格
  availability_status   可用性状态（IN_STOCK 等）
  available_quantity     可售数量
  sold_quantity         已售数量

  注意：只有关联了 eBay Product ID（ePID）的 listing 才有评论数据，
        普通 unique listings（如 custom/bundled items）无法获取评分。
        无数据时返回 None，请换一个有产品信息的商品 ID 重试。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
环境变量配置（自动从 .env 读取，优先父目录）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  生产环境:
    EBAY_APP_ID      → Client ID（也可写作 EBAY_CLIENT_ID）
    EBAY_CERT_ID     → Client Secret（也可写作 EBAY_CLIENT_SECRET）

  沙箱环境:
    EBAY_SBX_APP_ID  → Sandbox Client ID
    EBAY_SBX_CERT_ID → Sandbox Client Secret

  默认站点: EBAY_MARKETPLACE_ID（默认 EBAY_US）
"""

import os
import re
import json
import time
import argparse
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path


# ============================================================
# 加载 .env 文件
# ============================================================
def _load_env_file():
    """从 .env 文件加载环境变量，支持当前目录和父目录"""
    # 支持从当前脚本所在目录或父目录加载 .env
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
# PRD 凭证
CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "") or os.environ.get("EBAY_APP_ID", "")
CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "") or os.environ.get("EBAY_CERT_ID", "")

# Sandbox 凭证
SBX_CLIENT_ID = os.environ.get("EBAY_SBX_APP_ID", "")
SBX_CLIENT_SECRET = os.environ.get("EBAY_SBX_CERT_ID", "")

# 默认站点
DEFAULT_MARKETPLACE = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")

# API 端点
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SANDBOX_TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
SANDBOX_SEARCH_URL = "https://api.sandbox.ebay.com/buy/browse/v1/item_summary/search"

# Marketplace ID 映射
MARKETPLACE_IDS = {
    "EBAY_US": "EBAY_US",
    "EBAY_GB": "EBAY_GB",
    "EBAY_DE": "EBAY_DE",
    "EBAY_AU": "EBAY_AU",
    "EBAY_CA": "EBAY_CA",
    "EBAY_FR": "EBAY_FR",
    "EBAY_IT": "EBAY_IT",
    "EBAY_ES": "EBAY_ES",
    "EBAY_AT": "EBAY_AT",
    "EBAY_BE": "EBAY_BE",
    "EBAY_CH": "EBAY_CH",
    "EBAY_NL": "EBAY_NL",
    "EBAY_PL": "EBAY_PL",
    "EBAY_IE": "EBAY_IE",
    "EBAY_SG": "EBAY_SG",
    "EBAY_HK": "EBAY_HK",
}

# 商品状况 ID 映射
CONDITION_ID_MAP = {
    "NEW": 1000,
    "LIKE_NEW": 1500,
    "VERY_GOOD": 2000,
    "GOOD": 2500,
    "ACCEPTABLE": 3000,
    "SELLER_REFURBISHED": 2000,
    "OPEN_BOX": 1500,
}

# 销售方式
BUYING_OPTIONS = {
    "FIXED_PRICE": "一口价（固定价格）",
    "AUCTION": "拍卖",
    "BEST_OFFER": "议价",
    "CLASSIFIED_AD": "分类广告",
}

# 排序选项
SORT_OPTIONS = {
    "BestMatch": "最佳匹配（默认）",
    "PricePlusShippingLowest": "价格+运费：低到高",
    "PricePlusShippingHighest": "价格+运费：高到低",
    "Price": "价格：低到高",
    "PriceHigh": "价格：高到低",
    "StartTimeNewest": "最新上架",
    "EndTimeSoonest": "即将结束",
    "BestMatchCategoryDefault": "分类最佳匹配",
}

# 地区筛选（按站点）
LOCATION_REGION_MAP = {
    "EBAY_US": ["NORTH_AMERICA", "WORLDWIDE"],
    "EBAY_GB": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "WORLDWIDE"],
    "EBAY_DE": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "WORLDWIDE"],
    "EBAY_CA": ["NORTH_AMERICA", "WORLDWIDE"],
    "EBAY_AU": ["WORLDWIDE"],
    "EBAY_FR": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "BORDER_COUNTRIES", "WORLDWIDE"],
    "EBAY_IT": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "WORLDWIDE"],
    "EBAY_ES": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "WORLDWIDE"],
    "EBAY_BE": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "WORLDWIDE"],
    "EBAY_AT": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "WORLDWIDE"],
    "EBAY_CH": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "WORLDWIDE"],
    "EBAY_NL": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "WORLDWIDE"],
    "EBAY_PL": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "WORLDWIDE"],
    "EBAY_IE": ["EUROPEAN_UNION", "CONTINENTAL_EUROPE", "UK_AND_IRELAND", "WORLDWIDE"],
    "EBAY_SG": ["ASIA", "WORLDWIDE"],
    "EBAY_HK": ["ASIA", "BORDER_COUNTRIES", "WORLDWIDE"],
}

# 合格项目
QUALIFIED_PROGRAMS = {
    "EBAY_PLUS": "eBay Plus（仅德/奥/澳）",
    "AUTHENTICITY_GUARANTEE": "正品保障（仅美国）",
    "AUTHENTICITY_VERIFICATION": "正品验证",
}

# 卖家类型
SELLER_ACCOUNT_TYPES = {
    "BUSINESS": "企业卖家",
    "INDIVIDUAL": "个人卖家",
}

# 货币代码
CURRENCY_CODES = ["USD", "GBP", "EUR", "AUD", "CAD", "CHF", "CNY", "HKD", "JPY", "SGD", "PLN", "SEK", "NOK", "DKK"]


# ============================================================
# 搜索参数类
# ============================================================
class SearchFilters:
    """搜索筛选参数构建器"""

    def __init__(self):
        self._filters: Dict[str, Any] = {}

    # ---- 价格区间 ----
    def price_range(self, min_price: float = None, max_price: float = None,
                    currency: str = "USD") -> "SearchFilters":
        """
        价格区间筛选
        :param min_price: 最低价格
        :param max_price: 最高价格
        :param currency: 货币代码，默认 USD
        """
        if min_price is not None and max_price is not None:
            self._filters["price"] = f"[{min_price}..{max_price}]"
            self._filters["priceCurrency"] = currency
        elif min_price is not None:
            self._filters["price"] = f"[{min_price}]"
            self._filters["priceCurrency"] = currency
        elif max_price is not None:
            self._filters["price"] = f"[..{max_price}]"
            self._filters["priceCurrency"] = currency
        return self

    # ---- 商品状况 ----
    def conditions(self, condition_ids: List[int] = None,
                   condition_names: List[str] = None) -> "SearchFilters":
        """
        商品状况筛选
        :param condition_ids: 数值状况ID列表，如 [1000, 1500]
        :param condition_names: 文本状况名，如 ["NEW", "USED"]
        """
        if condition_ids:
            self._filters["conditionIds"] = "{" + "|".join(str(x) for x in condition_ids) + "}"
        if condition_names:
            self._filters["conditions"] = "{" + "|".join(condition_names) + "}"
        return self

    # ---- 销售方式 ----
    def buying_options(self, options: List[str]) -> "SearchFilters":
        """
        销售方式筛选
        :param options: 购买选项列表，如 ["FIXED_PRICE"] 或 ["FIXED_PRICE", "AUCTION"]
        """
        if options:
            self._filters["buyingOptions"] = "{" + "|".join(options) + "}"
        return self

    # ---- 拍卖竞拍数 ----
    def bid_count(self, min_bids: int = None, max_bids: int = None) -> "SearchFilters":
        """
        拍卖竞拍数筛选
        :param min_bids: 最少竞拍数
        :param max_bids: 最多竞拍数
        """
        if min_bids is not None and max_bids is not None:
            self._filters["bidCount"] = f"[{min_bids}..{max_bids}]"
        elif min_bids is not None:
            self._filters["bidCount"] = f"[{min_bids}]"
        elif max_bids is not None:
            self._filters["bidCount"] = f"[..{max_bids}]"
        return self

    # ---- 卖家 ----
    def sellers(self, seller_ids: List[str], exclude: bool = False) -> "SearchFilters":
        """
        卖家筛选
        :param seller_ids: 卖家ID列表
        :param exclude: True=排除这些卖家，False=只显示这些卖家
        """
        if not seller_ids:
            return self
        val = "|".join(seller_ids)
        if exclude:
            self._filters["excludeSellers"] = "{" + val + "}"
        else:
            self._filters["sellers"] = "{" + val + "}"
        return self

    # ---- 商品所在地 ----
    def location_country(self, country_code: str) -> "SearchFilters":
        """
        商品所在地国家筛选
        :param country_code: ISO 3166 两字母国家代码，如 "US", "CN", "GB"
        """
        if country_code:
            self._filters["itemLocationCountry"] = country_code
        return self

    def location_region(self, region: str) -> "SearchFilters":
        """
        商品所在地区域筛选
        :param region: 区域代码，如 "WORLDWIDE", "NORTH_AMERICA", "EUROPEAN_UNION"
        """
        if region:
            self._filters["itemLocationRegion"] = region
        return self

    # ---- 配送选项 ----
    def free_shipping(self) -> "SearchFilters":
        """只显示包邮商品"""
        self._filters["maxDeliveryCost"] = "0"
        return self

    def credit_card_payment(self) -> "SearchFilters":
        """只显示支持信用卡支付的商品"""
        self._filters["paymentMethods"] = "{CREDIT_CARD}"
        return self

    def returns_accepted(self) -> "SearchFilters":
        """只显示接受退货的商品"""
        self._filters["returnsAccepted"] = "true"
        return self

    # ---- 分类筛选 ----
    def exclude_categories(self, category_ids: List[str]) -> "SearchFilters":
        """
        排除指定分类
        :param category_ids: 分类ID列表
        """
        if category_ids:
            self._filters["excludeCategoryIds"] = "{" + "|".join(category_ids) + "}"
        return self

    # ---- 上架/结束时间 ----
    def listing_start_date(self, start_date: str = None, end_date: str = None) -> "SearchFilters":
        """
        上架时间筛选（ISO 8601 格式）
        :param start_date: 开始时间，如 "2024-01-01T00:00:00Z"
        :param end_date: 结束时间，如 "2024-12-31T23:59:59Z"
        """
        if start_date and end_date:
            self._filters["itemStartDate"] = f"[{start_date}..{end_date}]"
        elif start_date:
            self._filters["itemStartDate"] = f"[{start_date}]"
        elif end_date:
            self._filters["itemStartDate"] = f"[..{end_date}]"
        return self

    def listing_end_date(self, start_date: str = None, end_date: str = None) -> "SearchFilters":
        """
        结束时间筛选（ISO 8601 格式）
        :param start_date: 开始时间
        :param end_date: 结束时间
        """
        if start_date and end_date:
            self._filters["itemEndDate"] = f"[{start_date}..{end_date}]"
        elif start_date:
            self._filters["itemEndDate"] = f"[{start_date}]"
        elif end_date:
            self._filters["itemEndDate"] = f"[..{end_date}]"
        return self

    # ---- 本地取货 ----
    def local_pickup(self, country: str, postal_code: str,
                     radius: int, unit: str = "mi") -> "SearchFilters":
        """
        本地取货筛选
        :param country: 国家代码，如 "US"
        :param postal_code: 邮编
        :param radius: 半径距离
        :param unit: 距离单位，"mi" 或 "km"
        """
        self._filters["pickupCountry"] = country
        self._filters["pickupPostalCode"] = postal_code
        self._filters["pickupRadius"] = str(radius)
        self._filters["pickupRadiusUnit"] = unit
        self._filters["deliveryOptions"] = "{SELLER_ARRANGED_LOCAL_PICKUP}"
        return self

    # ---- 最后售出时间 ----
    def last_sold_date(self, start_date: str = None, end_date: str = None) -> "SearchFilters":
        """
        最后售出时间筛选
        :param start_date: 开始时间（ISO 8601）
        :param end_date: 结束时间（ISO 8601）
        """
        if start_date and end_date:
            self._filters["lastSoldDate"] = f"[{start_date}..{end_date}]"
        elif start_date:
            self._filters["lastSoldDate"] = f"[{start_date}]"
        elif end_date:
            self._filters["lastSoldDate"] = f"[..{end_date}]"
        return self

    # ---- 描述搜索 ----
    def search_in_description(self) -> "SearchFilters":
        """在商品描述中也搜索关键词（默认只搜标题）"""
        self._filters["searchInDescription"] = "true"
        return self

    # ---- 卖家账户类型 ----
    def seller_account_type(self, account_type: str) -> "SearchFilters":
        """
        卖家账户类型筛选
        :param account_type: "BUSINESS" 或 "INDIVIDUAL"
        """
        if account_type:
            self._filters["sellerAccountTypes"] = "{" + account_type + "}"
        return self

    # ---- 合格项目 ----
    def qualified_programs(self, programs: List[str]) -> "SearchFilters":
        """
        合格项目筛选
        :param programs: 如 ["AUTHENTICITY_GUARANTEE", "AUTHENTICITY_VERIFICATION"]
        """
        if programs:
            self._filters["qualifiedPrograms"] = "{" + "|".join(programs) + "}"
        return self

    # ---- 推广listing ----
    def priority_listing(self) -> "SearchFilters":
        """只显示推广商品"""
        self._filters["priorityListing"] = "true"
        return self

    # ---- 慈善商品 ----
    def charity_only(self) -> "SearchFilters":
        """只显示慈善商品"""
        self._filters["charityOnly"] = "true"
        return self

    # ---- 配送目的地 ----
    def delivery_destination(self, country: str, postal_code: str = None) -> "SearchFilters":
        """
        配送目的地筛选
        :param country: 配送国家代码
        :param postal_code: 配送邮编（可选）
        """
        self._filters["deliveryCountry"] = country
        if postal_code:
            self._filters["deliveryPostalCode"] = postal_code
        return self

    # ---- 构建 filter 参数字符串 ----
    def build(self) -> str:
        """
        返回构建好的 filter 参数字符串
        格式: filter=price:[10..50],conditionIds:{1000},buyingOptions:{FIXED_PRICE}
        """
        parts = []
        for key, value in self._filters.items():
            parts.append(f"{key}:{value}")
        return ",".join(parts)

    def __repr__(self):
        return f"SearchFilters({self._filters})"


# ============================================================
# eBay Searcher 类
# ============================================================
class eBaySearcher:
    """
    eBay Browse API 搜索器
    使用 curl subprocess 绕过本地代理，确保稳定连接
    支持关键词、GTIN、ePID、分类ID等多种搜索方式
    支持丰富的筛选参数
    """

    def __init__(
        self,
        client_id: str = None,
        client_secret: str = None,
        marketplace_id: str = None,
        sandbox: bool = False,
    ):
        if sandbox:
            self.client_id = client_id or SBX_CLIENT_ID
            self.client_secret = client_secret or SBX_CLIENT_SECRET
        else:
            self.client_id = client_id or CLIENT_ID
            self.client_secret = client_secret or CLIENT_SECRET

        self.marketplace_id = marketplace_id or DEFAULT_MARKETPLACE
        self.sandbox = sandbox

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "缺少 eBay API 凭据！\n"
                "请设置环境变量:\n"
                "  生产环境: EBAY_APP_ID + EBAY_CERT_ID\n"
                "  沙箱环境: EBAY_SBX_APP_ID + EBAY_SBX_CERT_ID\n"
                "或初始化时传入 client_id 和 client_secret"
            )

        self._access_token = None
        self._token_expires_at = 0

    @property
    def token_url(self) -> str:
        return SANDBOX_TOKEN_URL if self.sandbox else TOKEN_URL

    @property
    def search_url(self) -> str:
        return SANDBOX_SEARCH_URL if self.sandbox else SEARCH_URL

    def _curl_post(self, url: str, headers: dict, data: dict = None,
                   timeout: int = 20) -> tuple:
        """
        使用 curl 发送 POST 请求，返回 (status_code, body_text)
        """
        import subprocess, shlex
        cmd = ["curl", "-s", "-X", "POST", url]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if data:
            for k, v in data.items():
                cmd += ["-d", f"{k}={v}"]
        cmd += ["--connect-timeout", str(timeout), "-m", str(timeout)]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 5,
                encoding="utf-8", errors="replace",
            )
            return 200, result.stdout
        except subprocess.TimeoutExpired:
            return 0, ""
        except Exception:
            return 0, ""

    def _build_url(self, base_url, params):
        """Build URL with proper URL encoding for all parameters"""
        if not params:
            return base_url
        import urllib.parse
        parts = []
        for k, v in params.items():
            if v is None:
                continue
            parts.append(urllib.parse.quote(str(k), safe='') + '=' + urllib.parse.quote(str(v), safe=''))
        qs = '&'.join(parts)
        return base_url + '?' + qs if qs else base_url

    def _curl_get(self, url: str, headers: dict = None,
                  params: dict = None, timeout: int = 30) -> tuple:
        """
        使用 curl 发送 GET 请求，返回 (status_code, body_text)
        """
        import subprocess
        full_url = self._build_url(url, params)
        cmd = ["curl", "-s", "-X", "GET", full_url, "--connect-timeout", "15", "-m", str(timeout)]
        if headers:
            for k, v in headers.items():
                cmd += ["-H", f"{k}: {v}"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 10,
                encoding="utf-8", errors="replace",
            )
            return 200, result.stdout
        except subprocess.TimeoutExpired:
            return 0, ""
        except Exception:
            return 0, ""

    def _get_access_token(self) -> str:
        """使用 Client Credentials Grant 获取访问令牌（带缓存）"""
        import base64
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        auth = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        status, body = self._curl_post(
            self.token_url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {auth}",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=20,
        )

        if status != 200 or not body:
            raise PermissionError(
                f"eBay API 认证失败 (HTTP {status})\n"
                f"请检查 Client ID 和 Client Secret 是否正确\n"
                f"状态: {'Sandbox' if self.sandbox else 'Production'}"
            )

        try:
            token_data = json.loads(body)
        except json.JSONDecodeError:
            raise PermissionError(f"Token 响应格式错误: {body[:200]}")

        if "access_token" not in token_data:
            err_msg = token_data.get("error_description", token_data.get("error", "未知错误"))
            raise PermissionError(f"Token 获取失败: {err_msg}")

        self._access_token = token_data["access_token"]
        self._token_expires_at = time.time() + token_data.get("expires_in", 7200) - 60
        return self._access_token

    # ---- 基础搜索方法 ----

    def search(
        self,
        q: str = None,
        epid: str = None,
        gtin: str = None,
        category_ids: str = None,
        charity_ids: str = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "BestMatch",
        fieldgroups: str = None,
        filters: SearchFilters = None,
        aspect_filter: Dict[str, List[str]] = None,
        compatibility_filter: Dict[str, str] = None,
    ) -> dict:
        """
        搜索商品
        """
        if not any([q, epid, gtin, category_ids, charity_ids]):
            raise ValueError("必须提供至少一个搜索条件: q / epid / gtin / category_ids")

        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
        }

        params = {
            "limit": str(min(limit, 200)),
            "offset": str(offset),
        }
        if q:
            params["q"] = q
        if epid:
            params["epid"] = epid
        if gtin:
            params["gtin"] = gtin
        if category_ids:
            params["category_ids"] = category_ids
        if charity_ids:
            params["charity_ids"] = charity_ids
        if sort:
            params["sort"] = sort
        if fieldgroups:
            params["fieldgroups"] = fieldgroups

        # 构建 filter 字符串
        filter_parts = []
        if filters:
            fs = filters.build()
            if fs:
                filter_parts.append(fs)

        # aspect_filter: 格式 aspect={Brand:Nike|Adidas}
        if aspect_filter:
            for name, values in aspect_filter.items():
                if isinstance(values, list) and values:
                    val_str = "|".join(values)
                    filter_parts.append(f"aspect={name}:{{{val_str}}}")

        # compatibility_filter
        if compatibility_filter:
            for key, val in compatibility_filter.items():
                filter_parts.append(f"compatibility={key}:{{{val}}}")

        if filter_parts:
            params["filter"] = ",".join(filter_parts)

        status, body = self._curl_get(self.search_url, headers=headers, params=params, timeout=30)
        return self._parse_response(status, body)

    def search_all_pages(
        self,
        q: str = None,
        epid: str = None,
        gtin: str = None,
        category_ids: str = None,
        limit: int = 100,
        max_items: int = 500,
        sort: str = "BestMatch",
        filters: SearchFilters = None,
        aspect_filter: Dict[str, List[str]] = None,
        delay: float = 0.3,
    ) -> List[dict]:
        """自动翻页搜索，获取所有匹配的商品"""
        all_items = []
        offset = 0

        while len(all_items) < max_items:
            batch_limit = min(limit, max_items - len(all_items))
            result = self.search(
                q=q, epid=epid, gtin=gtin, category_ids=category_ids,
                limit=batch_limit, offset=offset,
                sort=sort, filters=filters, aspect_filter=aspect_filter,
            )

            items = result.get("itemSummaries", [])
            if not items:
                break

            all_items.extend(items)
            total = result.get("total", 0)
            if total and len(all_items) >= total:
                break
            if len(items) < batch_limit:
                break

            offset += len(items)
            if delay > 0:
                time.sleep(delay)

        return all_items

    def get_refinements(self, q: str = None, category_ids: str = None) -> dict:
        """获取搜索细化选项"""
        result = self.search(
            q=q, category_ids=category_ids,
            limit=1, fieldgroups="ASPECT_REFINEMENTS,CONDITION_REFINEMENTS",
        )
        return {
            "aspects": result.get("refinement", {}).get("aspectDistributions", []),
            "conditions": result.get("refinement", {}).get("conditionDistributions", []),
            "categories": result.get("refinement", {}).get("categoryDistributions", []),
        }

    def get_item_reviews(
        self,
        item_id: str,
        fieldgroups: str = "PRODUCT",
    ) -> dict:
        """
        获取商品评论数据（聚合评分 + 评分分布）

        注意：只有关联了 eBay 产品的 listing 才能返回评论数据。
        未关联产品的商品（如 unique/custom listings）返回 None。

        返回字段:
            averageRating      平均评分（1-5 分）
            reviewCount        总评论数
            ratingHistograms   评分分布 [{"rating": "5", "count": 120}, ...]
            totalFeedbackScore 卖家反馈评分
            positiveFeedbackPercent 好评率（%）
            sellerUsername     卖家用户名
            topRated          是否为顶级卖家

        :param item_id: 商品 ID（支持 RESTful 格式 v1|数字|变体ID，
                        也支持传统数字 ID，自动转为 RESTful 格式）
        :param fieldgroups: 字段组，默认 PRODUCT
        :return: 包含评论数据的字典，无数据时返回 None
        """
        import urllib.parse
        restful_id = self._normalize_item_id(item_id)
        base = self.search_url.rsplit("/item_summary", 1)[0]
        if self.sandbox:
            base = base.replace("://api.ebay.com/", "://api.sandbox.ebay.com/")
        else:
            base = base.replace("://api.sandbox.ebay.com/", "://api.ebay.com/")
        url = base + "/item/" + urllib.parse.quote(restful_id, safe="")
        full_url = url + "?" + urllib.parse.urlencode({"fieldgroups": fieldgroups})

        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
        }

        status, body = self._curl_get(full_url, headers=headers, timeout=30)
        if status != 200 or not body:
            return None

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None

        if data.get("errors"):
            return None

        rev = data.get("primaryProductReviewRating") or {}
        seller = data.get("seller") or {}
        avail_list = data.get("estimatedAvailabilities") or [{}]
        avail0 = avail_list[0] if avail_list else {}
        price_info = data.get("price") or {}

        return {
            "item_id": data.get("itemId", ""),
            "title": data.get("title", ""),
            "average_rating": rev.get("averageRating"),
            "review_count": rev.get("reviewCount"),
            "rating_histograms": rev.get("ratingHistograms", []),
            "seller_username": seller.get("username", ""),
            "seller_feedback_score": seller.get("feedbackScore", ""),
            "seller_positive_percent": seller.get("feedbackPercentage", ""),
            "top_rated": data.get("topRatedBuyingExperience", False),
            "condition": data.get("condition", ""),
            "condition_id": data.get("conditionId", ""),
            "price_value": price_info.get("value", ""),
            "price_currency": price_info.get("currency", ""),
            "brand": data.get("brand", ""),
            "gtin": data.get("gtin", ""),
            "epid": data.get("epid", ""),
            "availability_status": avail0.get("estimatedAvailabilityStatus", ""),
            "available_quantity": avail0.get("estimatedAvailableQuantity", ""),
            "sold_quantity": avail0.get("estimatedSoldQuantity", ""),
        }

    @staticmethod
    def _normalize_item_id(item_id: str) -> str:
        """
        将各种格式的商品ID标准化为 eBay RESTful ID 格式
        """
        item_id = item_id.strip()
        if item_id.startswith("v1|"):
            return item_id
        if item_id.isdigit():
            return f"v1|{item_id}|0"
        return item_id

    # ---- 便捷搜索方法 ----

    def search_by_keyword(
        self,
        keyword: str,
        price_min: float = None,
        price_max: float = None,
        price_currency: str = "USD",
        condition_ids: List[int] = None,
        buying_options: List[str] = None,
        free_shipping: bool = False,
        returns_accepted: bool = False,
        location_country: str = None,
        sort: str = "BestMatch",
        limit: int = 50,
        **kwargs,
    ) -> dict:
        """便捷的关键词搜索方法"""
        f = SearchFilters()
        if price_min is not None or price_max is not None:
            f.price_range(price_min, price_max, price_currency)
        if condition_ids:
            f.conditions(condition_ids=condition_ids)
        if buying_options:
            f.buying_options(buying_options)
        if free_shipping:
            f.free_shipping()
        if returns_accepted:
            f.returns_accepted()
        if location_country:
            f.location_country(location_country)
        return self.search(q=keyword, sort=sort, limit=limit, filters=f, **kwargs)

    def search_by_gtin(
        self,
        gtin: str,
        price_min: float = None,
        price_max: float = None,
        price_currency: str = "USD",
        limit: int = 50,
    ) -> dict:
        """通过 GTIN 搜索"""
        f = SearchFilters()
        if price_min is not None or price_max is not None:
            f.price_range(price_min, price_max, price_currency)
        return self.search(gtin=gtin, limit=limit, filters=f)

    def search_by_epid(
        self,
        epid: str,
        price_min: float = None,
        price_max: float = None,
        price_currency: str = "USD",
        limit: int = 50,
    ) -> dict:
        """通过 eBay Product ID 搜索"""
        f = SearchFilters()
        if price_min is not None or price_max is not None:
            f.price_range(price_min, price_max, price_currency)
        return self.search(epid=epid, limit=limit, filters=f)

    def search_by_category(
        self,
        category_id: str,
        price_min: float = None,
        price_max: float = None,
        price_currency: str = "USD",
        condition_ids: List[int] = None,
        buying_options: List[str] = None,
        limit: int = 50,
    ) -> dict:
        """通过分类ID搜索"""
        f = SearchFilters()
        if price_min is not None or price_max is not None:
            f.price_range(price_min, price_max, price_currency)
        if condition_ids:
            f.conditions(condition_ids=condition_ids)
        if buying_options:
            f.buying_options(buying_options)
        return self.search(category_ids=category_id, limit=limit, filters=f)

    # ---- 响应处理 ----

    def _parse_response(self, status: int, body: str) -> dict:
        """解析 API 响应"""
        if not body:
            if status == 0:
                raise TimeoutError("请求超时，请检查网络连接")
            raise ConnectionError(f"API 返回空响应 (HTTP {status})")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise ConnectionError(f"API 响应格式错误 (HTTP {status}): {body[:200]}")

        if status == 200:
            return data

        errors = data.get("errors", [])
        if errors:
            err = errors[0]
            error_id = err.get("errorId", "")
            message = err.get("message", "")
            domain = err.get("domain", "")
            raise ValueError(f"API 错误 [{error_id}] ({domain}): {message}")

        if status == 401:
            raise PermissionError("eBay API 认证失败，请检查 Client ID 和 Client Secret")
        if status == 429:
            raise RuntimeError("API 请求过于频繁（限流），请稍后再试")
        if status == 0:
            raise TimeoutError("请求超时")
        raise ConnectionError(f"API 请求失败 (HTTP {status}): {body[:200]}")

    # ---- 结果解析 ----

    @staticmethod
    def parse_items(raw_result: dict) -> List[dict]:
        return raw_result.get("itemSummaries", [])

    @staticmethod
    def extract_summary(item: dict) -> dict:
        price_info = item.get("price", {})
        shipping_opts = item.get("shippingOptions", []) or []
        first_shipping = shipping_opts[0] if shipping_opts else {}
        shipping_cost = first_shipping.get("shippingCost", {}) or {}

        return {
            "item_id": item.get("itemId", ""),
            "legacy_item_id": item.get("legacyItemId", ""),
            "title": item.get("title", ""),
            "price": price_info.get("value", ""),
            "currency": price_info.get("currency", ""),
            "condition": item.get("condition", ""),
            "condition_id": item.get("conditionId", ""),
            "image_url": (item.get("image") or {}).get("imageUrl", "") if item.get("image") else "",
            "item_web_url": item.get("itemWebUrl", ""),
            "seller_username": (item.get("seller") or {}).get("username", ""),
            "seller_feedback_score": (item.get("seller") or {}).get("feedbackScore", ""),
            "location": item.get("itemLocation", {}).get("postalCode", ""),
            "country": item.get("itemLocation", {}).get("country", ""),
            "shipping_cost": shipping_cost.get("value", ""),
            "shipping_currency": shipping_cost.get("currency", ""),
            "buying_options": item.get("buyingOptions", []),
            "category_id": (item.get("categories") or [{}])[0].get("categoryId", ""),
            "category_name": (item.get("categories") or [{}])[0].get("categoryName", ""),
            "top_rated": item.get("topRatedBuyingExperience", False),
            "available_coupons": item.get("availableCoupons", False),
            "adult_only": item.get("adultOnly", False),
            "item_creation_date": item.get("itemCreationDate", ""),
            "item_end_date": item.get("itemEndDate", ""),
        }

    @staticmethod
    def print_results(result: dict, limit: int = 20):
        items = result.get("itemSummaries", [])
        total = result.get("total", len(items))
        href = result.get("href", "")

        print(f"\n{'=' * 80}")
        print(f"  eBay 搜索结果  |  总计: {total}  |  当前页: {len(items)}  |  站点: {result.get('listingMarketplaceId', 'N/A')}")
        print(f"  URL: {href}")
        print(f"{'=' * 80}")

        if not items:
            print("  （无结果）")
            return

        for i, item in enumerate(items[:limit], 1):
            price = item.get("price", {})
            title = item.get("title", "")
            condition = item.get("condition", "")
            condition_id = item.get("conditionId", "")

            print(f"\n  [{i}] {title[:70]}{'...' if len(title) > 70 else ''}")
            print(f"      price: {price.get('value', 'N/A')} {price.get('currency', '')}"
                  f"  |  condition: {condition} (ID:{condition_id})"
                  f"  |  ID: {item.get('itemId', 'N/A')}")

            seller = item.get("seller", {})
            if seller:
                print(f"      seller: {seller.get('username', 'N/A')} "
                      f"(feedback: {seller.get('feedbackScore', 'N/A')})")

            shipping = (item.get("shippingOptions") or [{}])[0]
            ship_cost = (shipping.get("shippingCost") or {})
            print(f"      shipping: {ship_cost.get('value', 'N/A')} {ship_cost.get('currency', '')}")

        if len(items) > limit:
            print(f"\n  ... 还有 {len(items) - limit} 条结果未展示")

        print(f"\n{'=' * 80}\n")

    @staticmethod
    def print_reviews(review_data: dict):
        """打印商品评论数据"""
        if not review_data:
            print("  （无评论数据：该商品未关联 eBay 产品，无法获取评分）")
            return

        avg = review_data.get("average_rating")
        cnt = review_data.get("review_count")
        seller = review_data.get("seller_username", "")
        fb_score = review_data.get("seller_feedback_score", "")
        fb_pct = review_data.get("seller_positive_percent", "")
        price = review_data.get("price_value", "")
        curr = review_data.get("price_currency", "")
        cond = review_data.get("condition", "")
        brand = review_data.get("brand", "")
        top_rated = review_data.get("top_rated", False)
        avail = review_data.get("availability_status", "")
        sold = review_data.get("sold_quantity", "")

        print(f"\n{'=' * 80}")
        print(f"  评论数据  |  {review_data.get('title', '')[:60]}")
        print(f"{'=' * 80}")

        # 商品基本信息
        print(f"\n  [商品信息]")
        print(f"  ID:     {review_data.get('item_id', '')}")
        print(f"  品牌:    {brand}")
        print(f"  GTIN:    {review_data.get('gtin', 'N/A')}")
        print(f"  状况:    {cond} (ID: {review_data.get('condition_id', '')})")
        print(f"  价格:    {price} {curr}")
        print(f"  状态:    {avail}  |  在售: {review_data.get('available_quantity', 'N/A')}  |  已售: {sold}")

        # 评分
        print(f"\n  [商品评分]")
        if avg is not None and cnt is not None:
            stars = int(round(float(avg)))
            star_str = "\u2605" * stars + "\u2606" * (5 - stars)
            print(f"  平均评分: {avg} / 5.0  {star_str}")
            print(f"  评论总数: {cnt}")
        else:
            print(f"  平均评分: 无数据")
            print(f"  评论总数: 无数据")

        # 评分分布
        hist = review_data.get("rating_histograms", [])
        if hist:
            print(f"\n  [评分分布]")
            total_reviews = sum(int(h.get("count", 0)) for h in hist)
            bar_width = 30
            for h in sorted(hist, key=lambda x: x.get("rating", "0"), reverse=True):
                rating = h.get("rating", "?")
                count = int(h.get("count", 0))
                pct = (count / total_reviews * 100) if total_reviews > 0 else 0
                bar = "\u2588" * int(pct / 100 * bar_width) + "\u2591" * (bar_width - int(pct / 100 * bar_width))
                print(f"  {rating}\u2605: {bar}  {count:>5} ({pct:5.1f}%)")

        # 卖家
        print(f"\n  [卖家信息]")
        print(f"  用户名:  {seller}")
        if fb_score:
            print(f"  反馈评分: {fb_score}  |  好评率: {fb_pct}%")
        print(f"  顶级卖家: {'是' if top_rated else '否'}")

        print(f"\n{'=' * 80}\n")


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import argparse

    parser = argparse.ArgumentParser(
        description="eBay Browse API 产品搜索工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 关键词搜索，包邮 + 全新 + 10-100美元
  python ebay_search.py -q "front lip spoiler" --price-min 10 --price-max 100 --free-shipping --condition 1000

  # 通过GTIN搜索
  python ebay_search.py --gtin "012345678901"

  # 搜索并按价格排序
  python ebay_search.py -q "iphone" --sort Price --sandbox

  # 搜索拍卖商品
  python ebay_search.py -q "nike shoes" --buying-options AUCTION --condition 1000

  # 获取细化选项（查看可用的品牌/颜色等属性）
  python ebay_search.py -q "sneakers" --fieldgroups refinements
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
    parser.add_argument("--price-currency", default="USD",
                        help="货币代码（默认USD）")
    parser.add_argument("--condition", action="append", type=int,
                        dest="condition_ids",
                        help="商品状况ID，可多次指定（如 --condition 1000 --condition 1500）")
    parser.add_argument("--buying-options", action="append",
                        help="销售方式: FIXED_PRICE, AUCTION, BEST_OFFER")
    parser.add_argument("--sort", default="BestMatch",
                        choices=list(SORT_OPTIONS.keys()),
                        help="排序方式")
    parser.add_argument("--free-shipping", action="store_true",
                        help="只显示包邮商品")
    parser.add_argument("--returns-accepted", action="store_true",
                        help="只显示接受退货的商品")
    parser.add_argument("--location-country",
                        help="商品所在国家代码，如 US, CN, GB")
    parser.add_argument("--location-region",
                        help="商品所在区域，如 NORTH_AMERICA, WORLDWIDE")
    parser.add_argument("--credit-card", action="store_true",
                        help="只显示支持信用卡支付的商品")
    parser.add_argument("--bid-count-min", type=int,
                        help="最少竞拍数（拍卖商品）")
    parser.add_argument("--bid-count-max", type=int,
                        help="最多竞拍数（拍卖商品）")
    parser.add_argument("--charity", action="store_true",
                        help="只显示慈善商品")
    parser.add_argument("--priority-listing", action="store_true",
                        help="只显示推广商品")
    parser.add_argument("--search-in-description", action="store_true",
                        help="在商品描述中搜索关键词")
    parser.add_argument("--seller-type",
                        choices=["BUSINESS", "INDIVIDUAL"],
                        help="卖家账户类型")
    parser.add_argument("--qualified-program", action="append",
                        dest="qualified_programs",
                        help="合格项目: EBAY_PLUS, AUTHENTICITY_GUARANTEE, AUTHENTICITY_VERIFICATION")
    parser.add_argument("--exclude-category", action="append",
                        dest="exclude_categories",
                        help="排除的分类ID")
    parser.add_argument("--delivery-country",
                        help="配送目的国家代码")
    parser.add_argument("--delivery-postal-code",
                        help="配送目的邮编")
    parser.add_argument("--last-sold-start",
                        help="最后售出开始时间（ISO 8601，如 2024-01-01T00:00:00Z）")
    parser.add_argument("--last-sold-end",
                        help="最后售出结束时间（ISO 8601）")
    parser.add_argument("--listing-start",
                        help="上架开始时间（ISO 8601）")
    parser.add_argument("--listing-end",
                        help="上架结束时间（ISO 8601）")

    # 分页和输出
    parser.add_argument("--limit", type=int, default=50,
                        help="每页数量（默认50，最大200）")
    parser.add_argument("--offset", type=int, default=0,
                        help="分页偏移")
    parser.add_argument("--max-items", type=int, default=0,
                        help="最大获取总数（0=不限，限制翻页次数）")
    parser.add_argument("--fieldgroups",
                        help="字段组，如 ASPECT_REFINEMENTS,CONDITION_REFINEMENTS")
    parser.add_argument("--sandbox", action="store_true",
                        help="使用沙箱环境")
    parser.add_argument("--marketplace", default="EBAY_US",
                        choices=list(MARKETPLACE_IDS.keys()),
                        help="目标市场")
    parser.add_argument("--output", "-o",
                        help="保存结果到JSON文件")
    parser.add_argument("--client-id",
                        help="eBay Client ID（覆盖环境变量）")
    parser.add_argument("--client-secret",
                        help="eBay Client Secret（覆盖环境变量）")
    parser.add_argument("--no-print", action="store_true",
                        help="不打印结果到终端")
    # ---- 评论查询 ----
    parser.add_argument("--reviews", action="store_true",
                        help="查询商品评论数据（需配合 --item-id 使用）")
    parser.add_argument("--item-id",
                        help="商品 ID（支持 RESTful 格式 v1|数字|变体ID，"
                              "也支持传统数字 ID，自动转为 RESTful 格式）")

    args = parser.parse_args()

    # ---- 评论查询模式（优先处理）----
    if args.reviews or args.item_id:
        try:
            searcher = eBaySearcher(
                client_id=args.client_id,
                client_secret=args.client_secret,
                marketplace_id=args.marketplace,
                sandbox=args.sandbox,
            )
        except ValueError as e:
            print(f"\n{e}")
            exit(1)

        if not args.item_id:
            print("\n错误：--reviews 需要配合 --item-id 使用")
            exit(1)

        item_id = args.item_id.strip()
        env_label = "[Sandbox]" if args.sandbox else "[Production]"
        print(f"\n{env_label} 正在获取商品评论数据: {item_id}")

        review_data = searcher.get_item_reviews(item_id)
        if review_data is None:
            print("  （无法获取评论数据，请确认商品 ID 正确且该商品关联了 eBay 产品）")
            exit(1)

        searcher.print_reviews(review_data)
        if args.output:
            import json as _json
            fp = Path(__file__).parent / args.output
            with open(fp, "w", encoding="utf-8") as fout:
                _json.dump(review_data, fout, ensure_ascii=False, indent=2)
            print(f"  数据已保存到: {fp}")
        exit(0)

    # ---- 搜索模式 ----
    # 至少需要一个搜索条件
    if not any([args.query, args.gtin, args.epid, args.category_ids]):
        parser.print_help()
        print("\n❌ 错误：必须提供搜索条件（-q / --gtin / --epid / --category）")
        exit(1)

    # 初始化搜索器
    try:
        searcher = eBaySearcher(
            client_id=args.client_id,
            client_secret=args.client_secret,
            marketplace_id=args.marketplace,
            sandbox=args.sandbox,
        )
    except ValueError as e:
        print(f"\n❌ {e}")
        exit(1)

    # 构建筛选器
    f = SearchFilters()

    if args.price_min is not None or args.price_max is not None:
        f.price_range(args.price_min, args.price_max, args.price_currency)

    if args.condition_ids:
        f.conditions(condition_ids=args.condition_ids)

    if args.buying_options:
        f.buying_options(args.buying_options)

    if args.bid_count_min is not None or args.bid_count_max is not None:
        f.bid_count(args.bid_count_min, args.bid_count_max)

    if args.location_country:
        f.location_country(args.location_country)

    if args.location_region:
        f.location_region(args.location_region)

    if args.free_shipping:
        f.free_shipping()

    if args.returns_accepted:
        f.returns_accepted()

    if args.credit_card:
        f.credit_card_payment()

    if args.charity:
        f.charity_only()

    if args.priority_listing:
        f.priority_listing()

    if args.search_in_description:
        f.search_in_description()

    if args.seller_type:
        f.seller_account_type(args.seller_type)

    if args.qualified_programs:
        f.qualified_programs(args.qualified_programs)

    if args.exclude_categories:
        f.exclude_categories(args.exclude_categories)

    if args.last_sold_start or args.last_sold_end:
        f.last_sold_date(args.last_sold_start, args.last_sold_end)

    if args.listing_start or args.listing_end:
        f.listing_start_date(args.listing_start, args.listing_end)

    if args.delivery_country:
        f.delivery_destination(args.delivery_country, args.delivery_postal_code)

    # 搜索
    try:
        env_label = "🏜️ [Sandbox]" if args.sandbox else "🌐 [Production]"
        print(f"\n{env_label} eBay 搜索中...")

        if args.max_items > 0:
            print(f"  模式: 自动翻页，最大 {args.max_items} 条")
            items = searcher.search_all_pages(
                q=args.query,
                gtin=args.gtin,
                epid=args.epid,
                category_ids=args.category_ids,
                limit=args.limit,
                max_items=args.max_items,
                sort=args.sort,
                filters=f,
            )
            result = {
                "total": len(items),
                "itemSummaries": items,
                "listingMarketplaceId": args.marketplace,
            }
        else:
            result = searcher.search(
                q=args.query,
                gtin=args.gtin,
                epid=args.epid,
                category_ids=args.category_ids,
                limit=args.limit,
                offset=args.offset,
                sort=args.sort,
                fieldgroups=args.fieldgroups,
                filters=f,
            )

        # 输出
        total = result.get("total", 0)
        items = result.get("itemSummaries", [])

        print(f"  ✅ 找到 {total} 个商品，当前返回 {len(items)} 条")

        if not args.no_print:
            searcher.print_results(result, limit=20)

        # 保存
        if args.output:
            filepath = Path(__file__).parent / args.output
            with open(filepath, "w", encoding="utf-8") as fp:
                json.dump(result, fp, ensure_ascii=False, indent=2)
            print(f"  💾 结果已保存到: {filepath}")

    except ValueError as e:
        print(f"\n❌ 搜索错误: {e}")
        exit(1)
    except PermissionError as e:
        print(f"\n❌ 认证错误: {e}")
        exit(1)
    except RuntimeError as e:
        print(f"\n⚠️ 限流: {e}")
        exit(1)
    except Exception as e:
        print(f"\n❌ 未知错误: {type(e).__name__}: {e}")
        exit(1)
