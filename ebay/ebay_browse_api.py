"""
eBay 商品信息提取工具
基于 eBay Browse API - getItem 端点
文档: https://developer.ebay.com/api-docs/buy/browse/resources/item/methods/getItem

使用前需配置:
1. 在 https://developer.ebay.com/ 注册开发者账号
2. 创建应用获取 Client ID 和 Client Secret
3. 将凭据填入下方配置或设置环境变量

环境变量方式(推荐):
  EBAY_CLIENT_ID=你的Client_ID
  EBAY_CLIENT_SECRET=你的Client_Secret
"""

import os
import re
import json
import time
import requests
from datetime import datetime
from pathlib import Path


# ============================================================
# 加载 .env 文件（如果存在）
# ============================================================
def _load_env_file():
    """从 .env 文件加载环境变量，支持当前目录和父目录"""
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
# 配置区域 - 支持多种凭据命名方式
# ============================================================
# 方式1: 标准命名 (Browse API)
CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")

# 方式2: 兼容 .env 中的命名 (Trading/Finding API 命名)
if not CLIENT_ID:
    CLIENT_ID = os.environ.get("EBAY_APP_ID", "")
if not CLIENT_SECRET:
    CLIENT_SECRET = os.environ.get("EBAY_CERT_ID", "")

# 目标站点，默认美国站。可选: EBAY_US, EBAY_GB, EBAY_DE, EBAY_FR, EBAY_IT, EBAY_ES, EBAY_AU, EBAY_CA 等
MARKETPLACE_ID = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")

# ============================================================
# API 端点
# ============================================================
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
ITEM_API_URL = "https://api.ebay.com/buy/browse/v1/item/{item_id}"
SANDBOX_TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
SANDBOX_ITEM_API_URL = "https://api.sandbox.ebay.com/buy/browse/v1/item/{item_id}"

# 是否使用沙箱环境
USE_SANDBOX = os.environ.get("EBAY_USE_SANDBOX", "false").lower() == "true"


class eBayItemFetcher:
    """通过 eBay Browse API 获取商品详情"""

    def __init__(self, client_id: str = None, client_secret: str = None,
                 marketplace_id: str = None, sandbox: bool = False):
        self.client_id = client_id or CLIENT_ID
        self.client_secret = client_secret or CLIENT_SECRET
        self.marketplace_id = marketplace_id or MARKETPLACE_ID
        self.sandbox = sandbox or USE_SANDBOX

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "缺少 eBay API 凭据！\n"
                "请设置环境变量 EBAY_CLIENT_ID / EBAY_APP_ID 和 EBAY_CLIENT_SECRET / EBAY_CERT_ID，\n"
                "或在初始化时传入 client_id 和 client_secret 参数。\n"
                "获取凭据: https://developer.ebay.com/\n\n"
                "检测到的环境变量:\n"
                f"  EBAY_CLIENT_ID={os.environ.get('EBAY_CLIENT_ID', '未设置')}\n"
                f"  EBAY_APP_ID={os.environ.get('EBAY_APP_ID', '未设置')}\n"
                f"  EBAY_CLIENT_SECRET={os.environ.get('EBAY_CLIENT_SECRET', '未设置')[:10] + '...' if os.environ.get('EBAY_CLIENT_SECRET') else '未设置'}\n"
                f"  EBAY_CERT_ID={os.environ.get('EBAY_CERT_ID', '未设置')[:10] + '...' if os.environ.get('EBAY_CERT_ID') else '未设置'}"
            )

        self._access_token = None
        self._token_expires_at = 0

    @property
    def token_url(self):
        return SANDBOX_TOKEN_URL if self.sandbox else TOKEN_URL

    @property
    def item_api_url(self):
        return SANDBOX_ITEM_API_URL if self.sandbox else ITEM_API_URL

    def _get_access_token(self) -> str:
        """使用 Client Credentials Grant 获取 OAuth 访问令牌"""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }

        response = requests.post(
            self.token_url,
            headers=headers,
            data=data,
            auth=(self.client_id, self.client_secret),
        )
        response.raise_for_status()
        token_data = response.json()

        self._access_token = token_data["access_token"]
        # 提前60秒过期，留出安全余量
        self._token_expires_at = time.time() + token_data.get("expires_in", 7200) - 60
        return self._access_token

    @staticmethod
    def normalize_item_id(item_id: str) -> str:
        """
        将各种格式的商品ID标准化为 eBay RESTful ID 格式
        支持输入:
          - 传统数字ID (如 123456789012) -> v1|123456789012|0
          - RESTful ID (如 v1|123456789012|0) -> 原样
          - eBay URL (自动提取ID)
        """
        # 如果是URL，尝试提取ID
        if "ebay.com" in item_id or "ebay." in item_id:
            # 匹配 /itm/XXXX 或 /itm/XXXX-xxx 模式
            m = re.search(r"/itm/(\d+)", item_id)
            if m:
                item_id = m.group(1)
            else:
                # 尝试匹配 ?item=XXXX
                m = re.search(r"item[=/](\d+)", item_id)
                if m:
                    item_id = m.group(1)

        item_id = item_id.strip()

        # 已经是RESTful格式
        if item_id.startswith("v1|"):
            return item_id

        # 纯数字 -> 转为 RESTful 格式
        if item_id.isdigit():
            return f"v1|{item_id}|0"

        return item_id

    def get_item(self, item_id: str, fieldgroups: list = None,
                 quantity: int = None) -> dict:
        """
        获取商品详情

        参数:
            item_id: eBay 商品ID（支持传统ID、RESTful ID、商品URL）
            fieldgroups: 可选字段组，如 ["PRODUCT", "ADDITIONAL_SELLER_DETAILS"]
            quantity: 用于运费估算的商品数量

        返回:
            商品详情字典
        """
        token = self._get_access_token()
        restful_id = self.normalize_item_id(item_id)

        url = self.item_api_url.format(item_id=restful_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            "Content-Type": "application/json",
        }

        params = {}
        if fieldgroups:
            params["fieldgroups"] = ",".join(fieldgroups)
        if quantity:
            params["quantity_for_shipping_estimate"] = quantity

        response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code == 404:
            raise ValueError(f"商品未找到: {item_id} (RESTful ID: {restful_id})")
        if response.status_code == 400:
            error_data = response.json().get("errors", [{}])
            raise ValueError(f"请求错误: {error_data}")
        response.raise_for_status()

        return response.json()

    def get_item_with_retry(self, item_id: str, retries: int = 3, delay: float = 2.0,
                            fieldgroups: list = None, quantity: int = None) -> dict:
        """带重试机制的获取商品详情"""
        import time as _time
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                return self.get_item(item_id, fieldgroups=fieldgroups, quantity=quantity)
            except Exception as e:
                last_err = e
                if attempt < retries:
                    print(f"[eBay API] 第{attempt}次请求失败({e})，{delay}秒后重试...")
                    _time.sleep(delay * attempt)
        raise last_err

    def get_item_summary(self, item_id: str) -> dict:
        """
        获取商品关键信息摘要（提取核心字段，方便快速查看，带重试）

        返回结构化的商品摘要
        """
        raw = self.get_item_with_retry(item_id, fieldgroups=["PRODUCT", "ADDITIONAL_SELLER_DETAILS"])
        return self._extract_summary(raw)

    @staticmethod
    def _extract_summary(raw: dict) -> dict:
        """从原始API返回中提取核心字段"""
        # 预计算销量
        avail = raw.get("estimatedAvailabilities") or []
        sold_qty = 0
        if avail and isinstance(avail[0], dict):
            sold_qty = avail[0].get("estimatedSoldQuantity") or 0

        summary = {
            # 基本信息
            "商品ID": raw.get("itemId", ""),
            "传统ID": raw.get("legacyItemId", ""),
            "标题": raw.get("title", ""),
            "短描述": raw.get("shortDescription", ""),
            "商品链接": raw.get("itemWebUrl", ""),

            # 价格
            "价格": None,
            "货币": None,
            "当前竞拍价": None,
            "营销价格": None,
            "购买方式": raw.get("buyingOptions", []),

            # 状态
            "商品状况": raw.get("condition", ""),
            "可用性": None,

            # 分类
            "类目ID": raw.get("categoryId", ""),
            "类目路径": raw.get("categoryPath", ""),

            # 品牌/型号
            "品牌": raw.get("brand", ""),
            "MPN": raw.get("mpn", ""),
            "GTIN": raw.get("gtin", ""),
            "ePID": raw.get("epid", ""),

            # 图片
            "主图URL": raw.get("image", {}).get("imageUrl", "") if raw.get("image") else "",
            "图片数量": len(raw.get("additionalImages", [])) + (1 if raw.get("image") else 0),

            # 卖家
            "卖家": None,
            "好评率": None,
            "反馈评分": None,
            "卖家类型": None,
            "所在地": None,

            # 运输
            "运输选项": [],

            # 退货
            "退货政策": None,

            # 商品属性
            "商品属性": {},

            # 评分
            "平均评分": None,
            "评论数": None,

            # 销量（estimatedAvailabilities 里是估算值）
            "总销量": sold_qty,

            # 时间
            "上架时间": raw.get("itemCreationDate", ""),
            "结束时间": raw.get("itemEndDate", ""),
        }

        # 价格
        price_obj = raw.get("price", {})
        if price_obj:
            summary["价格"] = price_obj.get("value", "")
            summary["货币"] = price_obj.get("currency", "")

        # 竞拍价
        bid_price = raw.get("currentBidPrice", {})
        if bid_price:
            summary["当前竞拍价"] = f"{bid_price.get('value', '')} {bid_price.get('currency', '')}"

        # 营销价格
        mkt_price = raw.get("marketingPrice", {})
        if mkt_price:
            orig = mkt_price.get("originalPrice", {})
            summary["营销价格"] = f"原价: {orig.get('value', '')} {orig.get('currency', '')}"

        # 可用性
        avail = raw.get("estimatedAvailabilities", [])
        if avail:
            first = avail[0]
            summary["可用性"] = {
                "状态": first.get("estimatedAvailabilityStatus", ""),
                "数量": first.get("estimatedAvailableQuantity", ""),
            }

        # 卖家
        seller = raw.get("seller", {})
        if seller:
            summary["卖家"] = seller.get("username", "")
            summary["好评率"] = seller.get("feedbackPercentage", "")
            summary["反馈评分"] = seller.get("feedbackScore", "")
            summary["卖家类型"] = seller.get("sellerAccountType", "")

        # 所在地
        location = raw.get("itemLocation", {})
        if location:
            parts = []
            city = location.get("city", "")
            state = location.get("stateOrProvince", "")
            country = location.get("country", "")
            if city:
                parts.append(city)
            if state:
                parts.append(state)
            if country:
                parts.append(country)
            summary["所在地"] = ", ".join(parts) if parts else None

        # 运输选项
        shipping_opts = raw.get("shippingOptions", [])
        for opt in shipping_opts:
            cost = opt.get("shippingCost", {})
            summary["运输选项"].append({
                "类型": opt.get("shippingOptionType", ""),
                "运费": f"{cost.get('value', '0')} {cost.get('currency', '')}" if cost else "免运费",
                "最早送达": opt.get("minEstimatedDeliveryDate", ""),
                "最晚送达": opt.get("maxEstimatedDeliveryDate", ""),
                "承运商": opt.get("shippingCarrierCode", ""),
            })

        # 退货政策
        return_terms = raw.get("returnTerms", {})
        if return_terms:
            summary["退货政策"] = {
                "退货期限(天)": return_terms.get("returnPeriod", ""),
                "退款方式": return_terms.get("refundMethod", ""),
                "退货运费承担": return_terms.get("shippingCostPayer", ""),
            }

        # 商品属性
        aspects = raw.get("localizedAspects", [])
        for aspect in aspects:
            name = aspect.get("name", "")
            value = aspect.get("value", "")
            if name and value:
                summary["商品属性"][name] = value

        # 评分
        review = raw.get("primaryProductReviewRating", {})
        if review:
            summary["平均评分"] = review.get("averageRating", "")
            summary["评论数"] = review.get("reviewCount", "")

        return summary

    def batch_get_items(self, item_ids: list, delay: float = 0.5) -> list:
        """
        批量获取商品信息

        参数:
            item_ids: 商品ID列表
            delay: 每次请求间隔(秒)，避免触发限流

        返回:
            商品摘要列表
        """
        results = []
        for i, iid in enumerate(item_ids):
            try:
                print(f"[{i+1}/{len(item_ids)}] 获取商品: {iid}")
                summary = self.get_item_summary(iid)
                results.append({"item_id": iid, "status": "success", "data": summary})
            except Exception as e:
                results.append({"item_id": iid, "status": "error", "error": str(e)})
            if i < len(item_ids) - 1:
                time.sleep(delay)
        return results


def print_summary(summary: dict):
    """格式化打印商品摘要"""
    print("\n" + "=" * 70)
    print(f"  {summary.get('标题', 'N/A')}")
    print("=" * 70)

    basic_fields = [
        ("商品ID", "商品ID"),
        ("传统ID", "传统ID"),
        ("商品链接", "商品链接"),
        ("价格", "价格"),
        ("货币", "货币"),
        ("当前竞拍价", "当前竞拍价"),
        ("营销价格", "营销价格"),
        ("购买方式", "购买方式"),
        ("商品状况", "商品状况"),
        ("可用性", "可用性"),
        ("品牌", "品牌"),
        ("MPN", "MPN"),
        ("GTIN", "GTIN"),
        ("类目路径", "类目路径"),
        ("图片数量", "图片数量"),
        ("主图URL", "主图URL"),
    ]

    print("\n📋 基本信息:")
    for label, key in basic_fields:
        val = summary.get(key, "N/A")
        if val and val != "N/A":
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            print(f"  {label}: {val}")

    # 卖家信息
    if summary.get("卖家"):
        print(f"\n👤 卖家信息:")
        print(f"  用户名: {summary['卖家']}")
        print(f"  好评率: {summary['好评率']}%")
        print(f"  反馈评分: {summary['反馈评分']}")
        print(f"  类型: {summary['卖家类型']}")

    # 运输选项
    if summary.get("运输选项"):
        print(f"\n🚚 运输选项:")
        for i, opt in enumerate(summary["运输选项"], 1):
            print(f"  选项{i}: 运费={opt['运费']}, 类型={opt['类型']}, 送达={opt['最早送达']} ~ {opt['最晚送达']}")

    # 退货政策
    if summary.get("退货政策"):
        print(f"\n↩️  退货政策:")
        rp = summary["退货政策"]
        print(f"  退货期限: {rp.get('退货期限(天)', 'N/A')}")
        print(f"  退款方式: {rp.get('退款方式', 'N/A')}")
        print(f"  退货运费: {rp.get('退货运费承担', 'N/A')}")

    # 商品属性
    if summary.get("商品属性"):
        print(f"\n🏷️  商品属性:")
        for k, v in summary["商品属性"].items():
            print(f"  {k}: {v}")

    # 评分
    if summary.get("平均评分"):
        print(f"\n⭐ 评分: {summary['平均评分']} / 5 ({summary['评论数']} 条评论)")

    print("\n" + "=" * 70)


def save_to_json(data, filename: str = None):
    """保存结果到JSON文件"""
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ebay_item_{timestamp}.json"
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 结果已保存到: {filepath}")
    return filepath


# ============================================================
# 主程序 - 交互式使用
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="eBay 商品信息提取工具")
    parser.add_argument("item_ids", nargs="*", help="eBay 商品ID或URL（支持多个）")
    parser.add_argument("--client-id", "-i", help="eBay Client ID")
    parser.add_argument("--client-secret", "-s", help="eBay Client Secret")
    parser.add_argument("--marketplace", "-m", default="EBAY_US",
                        help="目标站点 (默认: EBAY_US)")
    parser.add_argument("--sandbox", action="store_true", help="使用沙箱环境")
    parser.add_argument("--output", "-o", help="输出JSON文件名")
    parser.add_argument("--full", "-f", action="store_true",
                        help="输出完整API返回（不提取摘要）")

    args = parser.parse_args()

    # 如果没有通过参数传入ID，交互式输入
    if not args.item_ids:
        print("eBay 商品信息提取工具")
        print("-" * 40)
        raw_input_ids = input("请输入商品ID或URL（多个用逗号分隔）: ").strip()
        if not raw_input_ids:
            print("未输入商品ID，退出。")
            exit(0)
        args.item_ids = [x.strip() for x in raw_input_ids.split(",") if x.strip()]

    # 初始化
    try:
        fetcher = eBayItemFetcher(
            client_id=args.client_id,
            client_secret=args.client_secret,
            marketplace_id=args.marketplace,
            sandbox=args.sandbox,
        )
    except ValueError as e:
        print(f"\n❌ {e}")
        print("\n获取 eBay API 凭据步骤:")
        print("1. 访问 https://developer.ebay.com/")
        print("2. 注册/登录开发者账号")
        print("3. 创建应用 (App Creation)")
        print("4. 获取 App ID 和 Cert ID")
        print("5. 配置方式:")
        print("   a) .env 文件: EBAY_APP_ID=xxx, EBAY_CERT_ID=xxx")
        print("   b) 环境变量: set EBAY_CLIENT_ID=xxx & set EBAY_CLIENT_SECRET=xxx")
        print("   c) 命令行: python ebay_item_fetcher.py --client-id xxx --client-secret xxx")
        exit(1)

    # 获取商品信息
    all_results = []
    for item_id in args.item_ids:
        try:
            if args.full:
                raw_data = fetcher.get_item(item_id, fieldgroups=["PRODUCT", "ADDITIONAL_SELLER_DETAILS"])
                print(json.dumps(raw_data, ensure_ascii=False, indent=2))
                all_results.append({"item_id": item_id, "status": "success", "data": raw_data})
            else:
                summary = fetcher.get_item_summary(item_id)
                print_summary(summary)
                all_results.append({"item_id": item_id, "status": "success", "data": summary})
        except Exception as e:
            print(f"\n❌ 获取商品 {item_id} 失败: {e}")
            all_results.append({"item_id": item_id, "status": "error", "error": str(e)})

    # 保存结果
    if args.output or len(args.item_ids) > 1:
        save_to_json(all_results, args.output)

    print(f"\n✅ 完成! 共处理 {len(args.item_ids)} 个商品")
