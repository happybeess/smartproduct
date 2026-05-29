"""
SmartSeller 后端服务
提供 Product Hunt API 等需要保护 token 的接口
"""

import json
import re
import ssl
import sys
import sqlite3
import time
import threading
from datetime import datetime
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory, make_response
from dotenv import load_dotenv

# ── Windows 控制台 UTF-8 编码 ──
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import os

load_dotenv()

# ── 智能代理检测：如果代理端口未开放则自动清除代理设置 ─_
def _check_proxy_alive():
    """检测 .env 中配置的代理是否可用，不可用则清除代理环境变量"""
    import socket as _sock
    proxy = os.getenv('HTTPS_PROXY') or os.getenv('https_proxy') or os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
    if not proxy:
        return
    # 提取 host:port
    m = re.match(r'(?:https?://)?([^:]+):(\d+)', proxy)
    if not m:
        return
    host, port = m.group(1), int(m.group(2))
    # 仅对本地代理做端口探测
    if host in ('127.0.0.1', 'localhost', '0.0.0.0', '::1'):
        s = _sock.socket()
        s.settimeout(2)
        try:
            s.connect((host, port))
            s.close()
        except (OSError, _sock.timeout, _sock.error):
            # 代理未运行，清除所有代理变量
            for k in ['HTTPS_PROXY','HTTP_PROXY','https_proxy','http_proxy','ALL_PROXY','all_proxy']:
                os.environ.pop(k, None)
            print(f'[Proxy] 代理 {proxy} 不可用，已自动切换为直连模式')

_check_proxy_alive()

# 确保代理环境变量被正确加载（在智能检测之后）
for key in ['HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy']:
    if key.upper() in os.environ:
        os.environ[key.lower()] = os.environ[key.upper()]

# 延迟导入 pandas (仅在 google-trends 接口中使用)
try:
    import pandas as pd
except ImportError:
    pd = None

# 确保 ebay/ 下的模块可以导入
import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ebay'))
try:
    import purchase_history as ph
    print("[INIT] purchase_history 加载成功")
except ImportError as _e:
    print(f"[INIT] purchase_history 加载失败: {_e}")
    ph = None

try:
    from amazon import amazon_sp_api as sp_api
    print("[INIT] amazon_sp_api 加载成功")
except ImportError as _e:
    print(f"[INIT] amazon_sp_api 加载失败: {_e}")
    sp_api = None

app = Flask(__name__, static_folder='.', static_url_path='')

# 请求日志中间件 - 所有请求都打印到终端
@app.before_request
def log_request():
    import sys
    if request.path.startswith('/api/'):
        print(f"\n[API] {request.method} {request.path} {dict(request.args)}")
        sys.stdout.flush()

# 三个独立数据库
DB_DIR = os.path.join(os.path.dirname(__file__), 'DB')
os.makedirs(DB_DIR, exist_ok=True)
TRENDS_DB = os.path.join(DB_DIR, 'trends.db')
PRODUCTS_DB = os.path.join(DB_DIR, 'products.db')
EBAY_CACHE_DB = os.path.join(DB_DIR, 'ebay_cache.db')
USERS_DB = os.path.join(DB_DIR, 'users.db')


# ===== 用户认证系统 =====
def init_users_db():
    """初始化用户数据库"""
    conn = sqlite3.connect(USERS_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    # 创建默认管理员账号（如果不存在）
    import hashlib
    admin_hash = hashlib.sha256('admin123'.encode()).hexdigest()
    conn.execute('''
        INSERT OR IGNORE INTO users (username, password_hash, role)
        VALUES ('admin', ?, 'admin')
    ''', (admin_hash,))
    conn.commit()
    conn.close()
    print("[INIT] 用户数据库初始化完成")

init_users_db()

# ===== 用户产品库（存储在用户数据库中）=====
def init_user_products_table():
    """初始化用户产品库表"""
    conn = sqlite3.connect(USERS_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id TEXT NOT NULL,
            product_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, product_id)
        )
    ''')
    conn.commit()
    conn.close()
    print("[INIT] 用户产品库表初始化完成")

init_user_products_table()


def init_db(db_path):
    """初始化数据库"""
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS daily_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT,
            data TEXT NOT NULL,
            fetch_date TEXT NOT NULL,
            UNIQUE(cache_key)
        )
    ''')
    conn.commit()
    conn.close()


def get_daily_data(db_path):
    """获取今日数据，如果没有则返回None"""
    today = time.strftime('%Y-%m-%d')
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            'SELECT data FROM daily_data WHERE fetch_date = ?',
            (today,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            print(f"[DB] HIT from {os.path.basename(db_path)}")
            return json.loads(row[0])
    except Exception as e:
        print(f"[DB] ERROR: {e}")
    return None


def save_daily_data(db_path, data):
    """保存今日数据（覆盖同一天的数据）"""
    today = time.strftime('%Y-%m-%d')
    print(f"[DB] Saving to {os.path.basename(db_path)}")
    conn = sqlite3.connect(db_path)
    conn.execute('''
        INSERT OR REPLACE INTO daily_data (data, fetch_date)
        VALUES (?, ?)
    ''', (json.dumps(data, ensure_ascii=False), today))
    conn.commit()
    conn.close()
    print(f"[DB] Saved to {os.path.basename(db_path)}")


# 初始化数据库
init_db(TRENDS_DB)
init_db(PRODUCTS_DB)


# ===== eBay 搜索缓存（SQLite） =====

def init_ebay_cache():
    conn = sqlite3.connect(EBAY_CACHE_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ebay_search_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keywords TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'EBAY-US',
            day_range INTEGER NOT NULL DEFAULT 180,
            tab_name TEXT NOT NULL DEFAULT 'SOLD',
            data TEXT NOT NULL,
            total INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_ebay_cache()

def get_ebay_cache(keywords, marketplace, day_range, tab_name):
    """按关键词查询缓存，一个月以内的记录有效"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        thirty_days_ago = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 30 * 86400))
        row = conn.execute(
            '''SELECT data, total FROM ebay_search_cache
               WHERE keywords=? AND marketplace=? AND day_range=? AND tab_name=?
               AND created_at >= ?
               ORDER BY created_at DESC LIMIT 1''',
            (keywords, marketplace, day_range, tab_name, thirty_days_ago)
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row[0]), row[1]
    except Exception as e:
        print(f"[eBay Cache] 查询失败: {e}")
    return None, None

def save_ebay_cache(keywords, marketplace, day_range, tab_name, data, total):
    """保存搜索结果到缓存"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        conn.execute(
            '''INSERT INTO ebay_search_cache (keywords, marketplace, day_range, tab_name, data, total)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (keywords, marketplace, day_range, tab_name, json.dumps(data, ensure_ascii=False), total)
        )
        conn.commit()
        conn.close()
        print(f"[eBay Cache] 已缓存: {keywords}")
    except Exception as e:
        print(f"[eBay Cache] 保存失败: {e}")

def add_market_analysis(data, keywords, marketplace):
    """为搜索数据添加市场分析"""
    try:
        from ebay.ebay_search import eBaySearcher, SearchFilters
        from collections import Counter
    except Exception as e:
        print(f"[market_analysis] 加载失败: {e}")

# ===== 卖家精灵搜索缓存（SQLite） =====
def init_sellersprite_cache():
    conn = sqlite3.connect(EBAY_CACHE_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sellersprite_search_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            market TEXT NOT NULL DEFAULT 'US',
            pages INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_sellersprite_cache()

# ===== OE搜索缓存（SQLite） =====
def init_oe_search_cache():
    conn = sqlite3.connect(EBAY_CACHE_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS oe_search_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'both',
            data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_oe_search_cache()

def get_oe_search_cache(keyword, platform):
    """查询OE搜索缓存，1天以内的记录有效"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        one_day_ago = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 1 * 86400))
        row = conn.execute(
            '''SELECT data FROM oe_search_cache
               WHERE keyword=? AND platform=? AND created_at >= ?
               ORDER BY created_at DESC LIMIT 1''',
            (keyword, platform, one_day_ago)
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception as e:
        print(f"[OE Cache] 查询失败: {e}")
    return None

def save_oe_search_cache(keyword, platform, data):
    """保存OE搜索结果到缓存"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        conn.execute(
            '''INSERT INTO oe_search_cache (keyword, platform, data)
               VALUES (?, ?, ?)''',
            (keyword, platform, json.dumps(data, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
        print(f"[OE Cache] 已缓存: {keyword} ({platform})")
    except Exception as e:
        print(f"[OE Cache] 保存失败: {e}")

def get_sellersprite_cache(keyword, market, pages):
    """按关键词+市场+页数查询缓存，1天以内的记录有效"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        one_day_ago = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 1 * 86400))
        row = conn.execute(
            '''SELECT data FROM sellersprite_search_cache
               WHERE keyword=? AND market=? AND pages=? AND created_at >= ?
               ORDER BY created_at DESC LIMIT 1''',
            (keyword, market, pages, one_day_ago)
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception as e:
        print(f"[SS Cache] 查询失败: {e}")
    return None

def save_sellersprite_cache(keyword, market, pages, data):
    """保存卖家精灵搜索结果到缓存"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        conn.execute(
            '''INSERT INTO sellersprite_search_cache (keyword, market, pages, data)
               VALUES (?, ?, ?, ?)''',
            (keyword, market, pages, json.dumps(data, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
        print(f"[SS Cache] 已缓存: {keyword} ({market}, {pages}页)")
    except Exception as e:
        print(f"[SS Cache] 保存失败: {e}")


# ===== 亚马逊关键词趋势缓存 =====
def init_kw_trend_cache():
    conn = sqlite3.connect(EBAY_CACHE_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS keyword_trend_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            site TEXT NOT NULL DEFAULT 'US',
            data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_kw_trend_cache()

def get_kw_trend_cache(keyword, site):
    """查询关键词趋势缓存，1天内有效"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        one_day_ago = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 86400))
        row = conn.execute(
            '''SELECT data FROM keyword_trend_cache
               WHERE keyword=? AND site=? AND created_at >= ?
               ORDER BY created_at DESC LIMIT 1''',
            (keyword, site, one_day_ago)
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception as e:
        print(f"[KW Trend Cache] 查询失败: {e}")
    return None

def save_kw_trend_cache(keyword, site, data):
    """保存关键词趋势到缓存"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        conn.execute(
            '''INSERT INTO keyword_trend_cache (keyword, site, data)
               VALUES (?, ?, ?)''',
            (keyword, site, json.dumps(data, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
        print(f"[KW Trend Cache] 已缓存: {keyword} ({site})")
    except Exception as e:
        print(f"[KW Trend Cache] 保存失败: {e}")

        marketplace_map = {
            'EBAY-US': 'EBAY_US',
            'EBAY-GB': 'EBAY_GB',
            'EBAY-DE': 'EBAY_DE',
            'EBAY-AU': 'EBAY_AU',
            'EBAY-CA': 'EBAY_CA',
        }
        api_marketplace = marketplace_map.get(marketplace, 'EBAY_US')

        searcher = eBaySearcher(marketplace_id=api_marketplace)

        active_items = searcher.search_all_pages(
            q=keywords,
            limit=100,
            max_items=500,
            delay=0.2,
        )

        seller_counter = Counter()
        seller_data = {}
        for item in active_items:
            seller = item.get("seller", {}) or {}
            username = seller.get("username", "")
            price_info = item.get("price", {}) or {}
            try:
                price_value = float(price_info.get("value", 0) or 0)
            except (ValueError, TypeError):
                price_value = 0.0
            try:
                fb_score = int(seller.get("feedbackScore", 0) or 0)
                fb_pct = float(seller.get("feedbackPercentage", 0) or 0)
            except (ValueError, TypeError):
                fb_score = 0
                fb_pct = 0.0
            if username:
                seller_counter[username] += 1
                if username not in seller_data:
                    seller_data[username] = {
                        "feedback_score": fb_score,
                        "feedback_percent": fb_pct,
                        "sales_amount": price_value,
                    }
                else:
                    seller_data[username]["sales_amount"] += price_value

        total_items = len(active_items)
        unique_sellers = len(seller_counter)

        top_sellers_by_sales = sorted(seller_data.items(), key=lambda x: x[1]["sales_amount"], reverse=True)[:10]

        top3_sellers = []
        top5_sellers = []
        top10_sellers = []
        for rank, (username, sdata) in enumerate(top_sellers_by_sales, 1):
            count = seller_counter.get(username, 0)
            pct = round(count / total_items * 100, 1) if total_items > 0 else 0
            seller_info = {
                "rank": rank,
                "username": username,
                "count": count,
                "pct": pct,
                "feedback_score": sdata.get("feedback_score", 0),
                "feedback_percent": sdata.get("feedback_percent", 0),
            }
            if rank <= 3:
                top3_sellers.append(seller_info)
            if rank <= 5:
                top5_sellers.append(seller_info)
            if rank <= 10:
                top10_sellers.append(seller_info)

        top3_seller_count = sum(s["count"] for s in top3_sellers)
        top3_seller_pct = round(top3_seller_count / total_items * 100, 1) if total_items > 0 else 0
        top5_seller_count = sum(s["count"] for s in top5_sellers)
        top5_seller_pct = round(top5_seller_count / total_items * 100, 1) if total_items > 0 else 0
        top10_seller_count = sum(s["count"] for s in top10_sellers)
        top10_seller_pct = round(top10_seller_count / total_items * 100, 1) if total_items > 0 else 0

        top3_product_pct = round(top3_seller_count / unique_sellers * 100, 1) if unique_sellers > 0 else 0
        top5_product_pct = round(top5_seller_count / unique_sellers * 100, 1) if unique_sellers > 0 else 0
        top10_product_pct = round(top10_seller_count / unique_sellers * 100, 1) if unique_sellers > 0 else 0

        top3_avg_feedback = round(sum(float(s.get("feedback_percent", 0) or 0) for s in top3_sellers) / len(top3_sellers), 1) if top3_sellers else 0

        data['market_analysis'] = {
            'unique_sellers': unique_sellers,
            'avg_items_per_seller': round(total_items / unique_sellers, 1) if unique_sellers > 0 else 0,
            'top3_sellers': top3_sellers,
            'top5_sellers': top5_sellers,
            'top10_sellers': top10_sellers,
            'top3_seller_pct': top3_seller_pct,
            'top5_seller_pct': top5_seller_pct,
            'top10_seller_pct': top10_seller_pct,
            'top3_product_pct': top3_product_pct,
            'top5_product_pct': top5_product_pct,
            'top10_product_pct': top10_product_pct,
            'top3_avg_feedback': top3_avg_feedback,
        }
    except Exception as e:
        data['market_analysis'] = {'error': str(e)[:100]}


# ===== eBay 单个商品详情+动销缓存（SQLite） =====
def init_item_cache():
    conn = sqlite3.connect(EBAY_CACHE_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ebay_item_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL UNIQUE,
            detail TEXT,
            purchase TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_item_cache()

def get_item_cache(item_id):
    """按 item_id 查询缓存，7天有效"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        seven_days_ago = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 7 * 86400))
        row = conn.execute(
            '''SELECT detail, purchase FROM ebay_item_cache
               WHERE item_id=? AND created_at >= ?''',
            (item_id, seven_days_ago)
        ).fetchone()
        conn.close()
        if row:
            detail = json.loads(row[0]) if row[0] else None
            purchase = json.loads(row[1]) if row[1] else None
            return detail, purchase
    except Exception as e:
        print(f"[Item Cache] 查询失败: {e}")
    return None, None

def save_item_cache(item_id, detail=None, purchase=None):
    """保存单个商品详情和动销到缓存（INSERT OR REPLACE）"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        conn.execute(
            '''INSERT INTO ebay_item_cache (item_id, detail, purchase, created_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(item_id) DO UPDATE SET
               detail=COALESCE(excluded.detail, detail),
               purchase=COALESCE(excluded.purchase, purchase),
               created_at=CURRENT_TIMESTAMP''',
            (item_id,
             json.dumps(detail, ensure_ascii=False) if detail else None,
             json.dumps(purchase, ensure_ascii=False) if purchase else None)
        )
        conn.commit()
        conn.close()
        print(f"[Item Cache] 已缓存: {item_id}")
    except Exception as e:
        print(f"[Item Cache] 保存失败: {e}")


@app.route('/api/product-hunt')
def product_hunt():
    """
    获取 Product Hunt 热门产品
    支持查询参数:
        - days: 热门天数 (1=今日, 7=本周), 默认 7
        - limit: 返回数量, 默认 20
        - refresh: 强制刷新 (1=刷新)
    """
    from dotenv import load_dotenv
    load_dotenv()
    import os

    days = int(request.args.get('days', 7))
    limit = min(int(request.args.get('limit', 40)), 40)

    # 检查数据库是否有今日数据
    cached = get_daily_data(PRODUCTS_DB)
    if cached:
        return jsonify({'data': cached, 'cached': True})

    try:
        import urllib.request

        # 根据日期选择排序方式 (Product Hunt API 只支持 VOTES)
        order_by = "VOTES"

        url = 'https://api.producthunt.com/v2/api/graphql'
        query = {
            "query": f"""{{
                posts(order: {order_by}, first: {limit}) {{
                    edges {{
                        node {{
                            name
                            tagline
                            votesCount
                            url
                            website
                            createdAt
                            topics {{
                                edges {{
                                    node {{
                                        name
                                    }}
                                }}
                            }}
                            thumbnail {{
                                url
                            }}
                        }}
                    }}
                }}
            }}"""
        }

        token = os.getenv('PHONE_TOKEN')
        if not token:
            return jsonify({'error': '未配置 PHONE_TOKEN'}), 500

        req = urllib.request.Request(
            url,
            data=json.dumps(query).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'SmartSeller/1.0',
                'Authorization': f'Bearer {token}',
            }
        )

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # 使用代理（如果配置了）
        proxies = {}
        proxy = os.getenv('HTTPS_PROXY') or os.getenv('https_proxy') or os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
        if proxy:
            proxies['https'] = proxy
            proxy_handler = urllib.request.ProxyHandler(proxies)
            opener = urllib.request.build_opener(proxy_handler, urllib.request.HTTPSHandler(context=ctx))
        else:
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

        with opener.open(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        edges = data.get('data', {}).get('posts', {}).get('edges', [])
        if not edges:
            return jsonify({'error': 'Product Hunt API 无数据'}), 404

        results = []
        for e in edges:
            n = e['node']
            # 提取话题标签
            topics = [t['node']['name'] for t in n.get('topics', {}).get('edges', [])[:5]]
            results.append({
                'title': n.get('name', ''),
                'desc': n.get('tagline', ''),
                'votes': n.get('votesCount', 0),
                'url': n.get('url', '') or n.get('website', ''),
                'createdAt': n.get('createdAt', ''),
                'topics': topics,
                'thumbnail': n.get('thumbnail', {}).get('url', '') if n.get('thumbnail') else '',
            })
        save_daily_data(PRODUCTS_DB, results)
        return jsonify({'data': results, 'cached': False})

    except Exception as e:
        err_str = str(e)
        # HTTP 403/401 通常意味着 Token 过期或权限不足
        if '403' in err_str or '401' in err_str or 'HTTP Error 403' in err_str:
            cached_data = get_daily_data(PRODUCTS_DB)
            if cached_data:
                return jsonify({'data': cached_data, 'cached': True, 'warning': 'Product Hunt Token可能已过期，已返回缓存数据'})
            return jsonify({'error': 'Product Hunt API Token无效或已过期(403)，请在 .env 中更新 PHONE_TOKEN'}), 503
        # 其他错误：尝试缓存兜底
        cached_data = get_daily_data(PRODUCTS_DB)
        if cached_data:
            return jsonify({'data': cached_data, 'cached': True, 'warning': f'实时获取失败，已返回缓存: {err_str[:80]}'})
        return jsonify({'error': f'获取失败: {err_str[:100]}'}), 500


@app.route('/api/product-hunt/save', methods=['POST'])
def save_product_hunt():
    """手动保存 Product Hunt 数据到本地数据库"""
    try:
        data = request.get_json()
        if not data or not isinstance(data, list):
            return jsonify({'success': False, 'error': '无效数据'}), 400
        save_daily_data(PRODUCTS_DB, data)
        return jsonify({'success': True, 'count': len(data)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ===== AlphaShop Ranking API 代理 =====
_ALPHASHOP_BASE = "https://selection.alphashop.cn"
_ALPHASHOP_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://www.alphashop.cn",
    "Referer": "https://www.alphashop.cn/ranking",
}


@app.route('/api/alphashop/config')
def alphashop_config():
    """获取平台/国家/排名类型配置"""
    try:
        r = _requests.post(f"{_ALPHASHOP_BASE}/opp/ranking/overall/list",
                           json={}, headers=_ALPHASHOP_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        return jsonify(data)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/alphashop/categories')
def alphashop_categories():
    """获取类目树"""
    platform = request.args.get('platform', 'amazon')
    country = request.args.get('country', 'US')
    try:
        r = _requests.post(f"{_ALPHASHOP_BASE}/opp/ranking/product/category",
                           json={"platform": platform, "country": country},
                           headers=_ALPHASHOP_HEADERS, timeout=15, proxies={'http': None, 'https': None})
        r.raise_for_status()
        data = r.json()
        return jsonify(data)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/alphashop/ranking', methods=['POST'])
def alphashop_ranking():
    """获取排行榜产品列表"""
    body = request.get_json(force=True)
    try:
        r = _requests.post(f"{_ALPHASHOP_BASE}/opp/ranking/product/list",
                           json=body, headers=_ALPHASHOP_HEADERS, timeout=15, proxies={'http': None, 'https': None})
        r.raise_for_status()
        data = r.json()
        return jsonify(data)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/alphashop/keyword/categories')
def alphashop_keyword_categories():
    """获取关键词榜单类目树（机会赛道榜）"""
    platform = request.args.get('platform', 'amazon')
    country = request.args.get('country', 'US')
    try:
        r = _requests.post(f"{_ALPHASHOP_BASE}/opp/ranking/keyword/category",
                           json={"platform": platform, "country": country},
                           headers=_ALPHASHOP_HEADERS, timeout=15, proxies={'http': None, 'https': None})
        r.raise_for_status()
        data = r.json()
        return jsonify(data)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/alphashop/keyword/ranking', methods=['POST'])
def alphashop_keyword_ranking():
    """获取关键词榜单列表（蓝海词/热销词）"""
    body = request.get_json(force=True)
    try:
        r = _requests.post(f"{_ALPHASHOP_BASE}/opp/ranking/keyword/list",
                           json=body, headers=_ALPHASHOP_HEADERS, timeout=15, proxies={'http': None, 'https': None})
        r.raise_for_status()
        data = r.json()
        return jsonify(data)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500




@app.route('/api/ebay-search')
def ebay_search():
    """
    eBay 关键词搜索（通过 CDP/WebSocket）
    支持查询参数:
        - keywords: 搜索关键词 (必需)
        - cdp_url: CDP 地址, 默认 http://localhost:9222
        - marketplace: 市场 (EBAY-US, EBAY-GB, EBAY-DE等), 默认 EBAY-US
        - day_range: 时间范围(天), 默认 180
        - limit: 返回数量, 默认 50
        - tab_name: 数据类型 (SOLD/ACTIVE), 默认 SOLD
        - price_min: 最低价格 (可选)
        - price_max: 最高价格 (可选)
        - include_market_analysis: 是否包含市场分析, 默认 true
    """
    keywords = request.args.get('keywords', '').strip()
    if not keywords:
        return jsonify({'error': '请提供搜索关键词'}), 400

    cdp_url = request.args.get('cdp_url', 'http://localhost:9222')
    marketplace = request.args.get('marketplace', 'EBAY-US')
    day_range = 180
    category_id = '0'
    limit = min(int(request.args.get('limit', 50)), 200)
    tab_name = request.args.get('tab_name', 'SOLD')
    include_market_analysis = request.args.get('include_market_analysis', 'true').lower() != 'false'

    price_min = None
    price_min_str = request.args.get('price_min')
    if price_min_str:
        try:
            price_min = float(price_min_str)
        except ValueError:
            pass

    price_max = None
    price_max_str = request.args.get('price_max')
    if price_max_str:
        try:
            price_max = float(price_max_str)
        except ValueError:
            pass

    # 检查缓存（同一天同一关键词直接返回）
    cached_data, cached_total = get_ebay_cache(keywords, marketplace, day_range, tab_name)
    print(f"[eBay搜索] 关键词={keywords}, 缓存={'有' if cached_data else '无'}")
    if cached_data:
        # 缓存数据也添加市场分析
        if include_market_analysis:
            if 'market_analysis' not in cached_data or not cached_data.get('market_analysis') or cached_data.get('market_analysis', {}).get('error'):
                add_market_analysis(cached_data, keywords, marketplace)
        else:
            cached_data['market_analysis'] = None
        return jsonify({'data': cached_data, 'cached': True})

    # eBay CDP 请求
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from ebay.ebay_research_api import search_products

        print(f"\n[eBay搜索] 开始: keywords={keywords}, marketplace={marketplace}, limit={limit}")
        sys.stdout.flush()

        result = search_products(
            keywords=keywords,
            cdp_url=cdp_url,
            marketplace=marketplace,
            category_id=category_id,
            day_range=day_range,
            limit=limit,
            tab_name=tab_name,
            price_min=price_min,
            price_max=price_max,
        )
        if result['success']:
            print(f"[eBay搜索] 成功! items={len(result['items'])}, aggregates={result['aggregates']}")
            sys.stdout.flush()
            data = {
                'items': result['items'],
                'aggregates': result['aggregates'],
                'total': result['total'],
            }

            # 添加市场分析（卖家竞争分析）
            if include_market_analysis:
                add_market_analysis(data, keywords, marketplace)
            else:
                data['market_analysis'] = None

            # 保存缓存
            save_ebay_cache(keywords, marketplace, day_range, tab_name, data, result['total'])
            return jsonify({'data': data, 'cached': False})
        else:
            print(f"[eBay搜索] 失败: {result.get('error')}")
            return jsonify({'error': result.get('error', '搜索失败')}), 500

    except Exception as e:
        import traceback
        print(f"[eBay搜索] 异常: {e}")
        traceback.print_exc()
        return jsonify({'error': f'搜索失败: {str(e)[:200]}'}), 500


@app.route('/api/ebay-search/stream')
def ebay_search_stream():
    """
    eBay 关键词搜索 SSE 流式接口
    前端用 EventSource 接收，每抓完一页实时推送
    架构：线程 + Queue
    """
    from flask import Response
    import json, uuid

    keywords = request.args.get('keywords', '').strip()
    cdp_url = request.args.get('cdp_url', 'http://localhost:9222')
    marketplace = request.args.get('marketplace', 'EBAY-US')
    day_range = 180
    limit = min(int(request.args.get('limit', 200)), 200)
    tab_name = request.args.get('tab_name', 'SOLD')

    price_min = None
    price_min_str = request.args.get('price_min')
    if price_min_str:
        try:
            price_min = float(price_min_str)
        except ValueError:
            pass

    price_max = None
    price_max_str = request.args.get('price_max')
    if price_max_str:
        try:
            price_max = float(price_max_str)
        except ValueError:
            pass

    if not keywords:
        return Response(
            f"event: error\ndata: {json.dumps({'error': '请提供搜索关键词'}, ensure_ascii=False)}\n\n",
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )

    # 检查缓存
    cached_data, cached_total = get_ebay_cache(keywords, marketplace, day_range, tab_name)
    if cached_data:
        return Response(
            f"event: cached\ndata: {json.dumps({'data': cached_data, 'cached': True}, ensure_ascii=False, default=str)}\n\n",
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )

    task_id = str(uuid.uuid4())[:8]
    q = queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_queues[task_id] = q

    def _run_in_thread():
        try:
            import sys
            sys.path.insert(0, os.path.dirname(__file__))
            from ebay.ebay_research_api import search_products

            def page_cb(page_num, page_items):
                payload = {
                    'type': 'page',
                    'page': page_num,
                    'items': page_items,
                    'count': len(page_items),
                }
                _sse_push(task_id, 'page', payload)

            result = search_products(
                keywords=keywords,
                cdp_url=cdp_url,
                marketplace=marketplace,
                day_range=day_range,
                limit=limit,
                tab_name=tab_name,
                price_min=price_min,
                price_max=price_max,
                page_callback=page_cb,
            )

            if result.get('success'):
                data = {
                    'items': result['items'],
                    'aggregates': result['aggregates'],
                    'total': result['total'],
                }
                add_market_analysis(data, keywords, marketplace)
                save_ebay_cache(keywords, marketplace, day_range, tab_name, data, result['total'])
                _sse_push(task_id, 'done', {'data': data, 'cached': False})
            else:
                _sse_push(task_id, 'error', {'error': result.get('error', '搜索失败')})

        except Exception as e:
            import traceback
            traceback.print_exc()
            _sse_push(task_id, 'error', {'error': str(e)[:200]})
        finally:
            try:
                q.put_nowait((None, None))
            except:
                pass
            with _sse_lock:
                _sse_queues.pop(task_id, None)

    t = threading.Thread(target=_run_in_thread, daemon=True)
    t.start()

    def gen():
        while True:
            try:
                event_type, data = q.get(timeout=180)
                if event_type is None:
                    break
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            except queue.Empty:
                yield f"event: error\ndata: {json.dumps({'error': '超时（3分钟无响应）'}, ensure_ascii=False)}\n\n"
                break

    return Response(
        gen(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


def _parse_serp_week_to_month(date_str, default_year=None):
    """将 SerpAPI 任意日期格式转为 '2026-02'（暴力兼容，100% 返回 YYYY-MM）"""
    if not date_str:
        return datetime.now().strftime('%Y-%m')
    month_abbr = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    s = date_str.strip()
    # 提取月名（3字母，忽略大小写和前后缀）
    mo_match = re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b', s, re.IGNORECASE)
    # 提取4位年份
    yr_match = re.search(r'\b(\d{4})\b', s)
    if mo_match:
        mo = month_abbr.get(mo_match.group(1).lower(), 1)
        if yr_match:
            return f"{yr_match.group(1)}-{mo:02d}"
        if default_year:
            return f"{default_year}-{mo:02d}"
        now_year = datetime.now().year
        now_month = datetime.now().month
        year = now_year if mo <= now_month else now_year - 1
        return f"{year}-{mo:02d}"
    # 无月名时的终极兜底
    return datetime.now().strftime('%Y-%m')


@app.route('/api/keyword-trend')
def keyword_trend():
    """
    关键词趋势对比 API（支持多关键词时间序列对比）
    GET /api/keyword-trend?keywords=iphone,samsung&geo=US&timeframe=today+3-m

    参数:
        - keywords: 逗号分隔的关键词，最多5个
        - geo: 国家代码，默认 US
        - timeframe: 时间范围，默认 today 3-m
        - compare: 1=启用多关键词对比（默认）
    """
    keywords_str = request.args.get('keywords', '').strip()
    if not keywords_str:
        return jsonify({'error': '请提供关键词'}), 400

    keywords = [k.strip() for k in keywords_str.split(',') if k.strip()][:5]
    geo = request.args.get('geo', 'US').upper()
    timeframe = request.args.get('timeframe', 'today 3-m')

    # 检查缓存（按 keywords+geo+timeframe 组合，加 v4 版本号，数据格式改为按周）
    cached = get_kw_trend_cache(','.join(sorted(keywords)), f"{geo}|{timeframe}|v4")
    if cached:
        cached['cached'] = True
        return jsonify(cached)

    geo_map = {
        'US': 'US', 'GB': 'GB', 'UK': 'GB',
        'DE': 'DE', 'FR': 'FR', 'JP': 'JP',
        'CN': 'CN', 'AU': 'AU', 'CA': 'CA',
        'IN': 'IN', 'BR': 'BR',
    }
    serp_geo = geo_map.get(geo.upper(), 'US')

    timeframe_map = {
        'now 1-d': 'today 1-d',
        'now 7-d': 'today 7-d',
        'today 1-m': 'today 1-m',
        'today 3-m': 'today 3-m',
        'today 12-m': 'today 12-m',
        'today+3-y': 'today 3-y',
    }
    serp_time = timeframe_map.get(timeframe, 'today 3-m')

    try:
        import os
        import requests

        serpapi_key = os.environ.get('SERPAPI_KEY') or os.environ.get('SERP_API_KEY') or os.environ.get('serp_api_key')
        if not serpapi_key:
            return jsonify({'error': '请在 .env 中配置 SERPAPI_KEY'}), 500

        serp_url = "https://serpapi.com/search"

        # 对每个关键词单独请求，获取时间序列
        all_timelines = {}
        all_related = {}
        failed_kws = []

        for kw in keywords:
            params = {
                'engine': 'google_trends',
                'q': kw,
                'geo': serp_geo,
                'date': serp_time,
                'api_key': serpapi_key,
            }
            try:
                resp = requests.get(serp_url, params=params, timeout=30)
                resp.raise_for_status()
                result = resp.json()

                        # 时间序列：按周返回（约48-52个数据点，比月度12个密集得多）
                weekly = []
                if 'interest_over_time' in result and 'timeline_data' in result['interest_over_time']:
                    for item in result['interest_over_time']['timeline_data']:
                        date_str = item.get('date', '')
                        vals = item.get('values', [])
                        val = vals[0].get('value', 0) if vals else 0
                        month_key = _parse_serp_week_to_month(date_str)
                        weekly.append({'month': month_key, 'value': int(val) if val != '' else 0})

                # 按周独立输出，同月多周加 -Wn 后缀区分
                week_counter = {}
                timeline = []
                for w in weekly:
                    m = w['month']
                    week_counter[m] = week_counter.get(m, 0) + 1
                    label = m if week_counter[m] == 1 else f"{m}-W{week_counter[m]}"
                    timeline.append({'date': label, 'value': w['value']})

                all_timelines[kw] = timeline

                # 相关查询（取第一个关键词的 top 查询）
                if kw == keywords[0] and 'related_queries' in result:
                    top = result['related_queries'].get('top', [])
                    all_related = [
                        {'query': rq.get('query', ''), 'value': rq.get('value', '')}
                        for rq in top[:10] if isinstance(rq, dict) and rq.get('query')
                    ]

            except Exception as e:
                failed_kws.append({'keyword': kw, 'error': str(e)[:100]})
                all_timelines[kw] = []

        # 按月合并时间线（统一日期轴，各关键词的月份可能不完全一致）
        all_months = set()
        for tl in all_timelines.values():
            for point in tl:
                all_months.add(point['date'])
        sorted_months = sorted(all_months)

        combined_timeline = []
        for month in sorted_months:
            point = {'date': month}
            for kw in keywords:
                tl = all_timelines.get(kw, [])
                val = next((p['value'] for p in tl if p['date'] == month), 0)
                point[kw] = val
            combined_timeline.append(point)

        # 趋势统计
        stats = {}
        for kw in keywords:
            tl = all_timelines.get(kw, [])
            if tl:
                values = [p['value'] for p in tl]
                max_val = max(values) if values else 0
                avg_val = sum(values) / len(values) if values else 0
                # 计算趋势方向：比较前后各取3个点的均值
                if len(values) >= 6:
                    recent_avg = sum(values[-3:]) / 3
                    older_avg = sum(values[:3]) / 3
                    trend_pct = round((recent_avg - older_avg) / older_avg * 100, 1) if older_avg > 0 else 0
                else:
                    trend_pct = 0
                stats[kw] = {
                    'max': max_val,
                    'avg': round(avg_val, 1),
                    'trend_pct': trend_pct,
                    'trend_dir': 'up' if trend_pct > 5 else ('down' if trend_pct < -5 else 'stable'),
                }

        result = {
            'success': True,
            'keywords': keywords,
            'geo': geo,
            'timeframe': timeframe,
            'timeline': combined_timeline,
            'stats': stats,
            'related_queries': all_related,
            'failed_keywords': failed_kws,
        }

        # 保存缓存
        save_kw_trend_cache(','.join(sorted(keywords)), f"{geo}|{timeframe}|v4", result)
        result['cached'] = False

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': f'获取失败: {str(e)[:200]}'}), 500


@app.route('/api/google-trends')
def google_trends():
    """
    Google Trends 热点查询
    支持查询参数:
        - geo: 国家代码 (如 US, GB, DE, JP, CN), 默认 US
        - timeframe: 时间范围 (now 1-d=今日, now 7-d=本周, today 3-m=3个月, today 12-m=12个月), 默认 now 7-d
        - limit: 返回数量, 默认 10
    """
    geo = request.args.get('geo', 'US').upper()
    timeframe = request.args.get('timeframe', 'now 7-d')
    limit = min(int(request.args.get('limit', 200)), 200)
    
    # 映射国家名称到代码
    country_map = {
        'US': 'US', '美国': 'US',
        'GB': 'GB', '英国': 'GB', 'UK': 'GB',
        'DE': 'DE', '德国': 'DE',
        'FR': 'FR', '法国': 'FR',
        'JP': 'JP', '日本': 'JP',
        'CN': 'CN', '中国': 'CN',
        'AU': 'AU', '澳大利亚': 'AU',
        'CA': 'CA', '加拿大': 'CA',
        'IN': 'IN', '印度': 'IN',
        'BR': 'BR', '巴西': 'BR',
    }
    
    # 如果输入的是国家名称，转换为代码
    if geo in country_map:
        geo = country_map[geo]
    
    # 检查数据库是否有今日数据
    cached = get_daily_data(TRENDS_DB)
    if cached:
        return jsonify({'data': cached, 'cached': True})
    
    try:
        import os
        
        # 获取 SerpAPI 密钥 (支持多种命名格式)
        serpapi_key = os.environ.get('SERPAPI_KEY') or os.environ.get('SERP_API_KEY') or os.environ.get('serp_api_key')
        if not serpapi_key:
            return jsonify({'error': '请在 .env 中配置 SERPAPI_KEY'}), 500
        
        # 国家代码映射 (SerpAPI 使用 ISO 3166-1 alpha-2)
        geo_map = {
            'US': 'US', 'GB': 'GB', 'UK': 'GB',
            'DE': 'DE', 'FR': 'FR', 'JP': 'JP',
            'CN': 'CN', 'AU': 'AU', 'CA': 'CA',
            'IN': 'IN', 'BR': 'BR',
        }
        serp_geo = geo_map.get(geo.upper(), 'US')
        
        # 时间范围映射
        timeframe_map = {
            'now 1-d': 'today 1-d',
            'now 7-d': 'today 7-d',
            'today 3-m': 'today 3-m',
            'today 12-m': 'today 12-m',
        }
        serp_time = timeframe_map.get(timeframe, 'today 7-d')
        
        # 调用 SerpAPI Google Trends（带重试机制）
        import requests
        serp_url = "https://serpapi.com/search"
        params = {
            'engine': 'google_trends_trending_now',
            'geo': serp_geo,
            'date': serp_time,
            'api_key': serpapi_key,
        }

        resp = None
        last_err = None
        for attempt in range(3):
            try:
                resp = requests.get(serp_url, params=params, timeout=(10, 20))  # connect=10s, read=20s
                resp.raise_for_status()
                break
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
                    requests.exceptions.ConnectionError, requests.exceptions.SSLError) as conn_err:
                last_err = conn_err
                if attempt < 2:
                    import time as _t; _t.sleep(1.5)
                    continue

        if resp is None:
            err_msg = str(last_err or '未知网络错误')[:150]
            # 尝试返回缓存数据作为兜底
            cached_data = get_daily_data(TRENDS_DB)
            if cached_data:
                return jsonify({'data': cached_data, 'cached': True, 'warning': f'实时获取失败，已返回缓存: {err_msg}'})
            return jsonify({'error': f'连接超时(SerpAPI无响应): {err_msg}'}), 504
        result = resp.json()
        
        results = []
        # SerpAPI 返回 trending_searches 而不是 trending_now
        if 'trending_searches' in result:
            items = result['trending_searches']
            for item in items[:limit]:
                title = item.get('title', '') or item.get('query', '')
                # 提取热度值: search_volume 或 increase_percentage
                search_volume = item.get('search_volume', '')
                increase_pct = item.get('increase_percentage', 0)
                if search_volume:
                    traffic = f"{search_volume // 1000}K+" if isinstance(search_volume, (int, float)) else str(search_volume)
                elif increase_pct:
                    traffic = f"+{increase_pct}%" if increase_pct > 0 else ""
                else:
                    traffic = ''
                results.append({
                    'title': title,
                    'traffic': traffic,
                    'image_url': item.get('image', ''),
                })
        
        data = {
            'geo': geo,
            'timeframe': timeframe,
            'trends': results,
            'topics': [],
        }
        
        save_daily_data(TRENDS_DB, data)
        return jsonify({'data': data, 'cached': False})
        
    except Exception as e:
        return jsonify({'error': f'获取失败: {str(e)[:200]}'}), 500


@app.route('/api/google-trends/save', methods=['POST'])
def save_google_trends():
    """手动保存 Google Trends 数据到本地数据库"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效数据'}), 400
        save_daily_data(TRENDS_DB, data)
        return jsonify({'success': True, 'count': len(data.get('trends', []))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/google-trends-interest')
def google_trends_interest():
    """
    Google Trends 关键词兴趣度查询
    支持查询参数:
        - keywords: 关键词列表，逗号分隔 (如 "iphone,samsung,google")
        - geo: 国家代码, 默认 US
        - timeframe: 时间范围, 默认 today 3-m
    """
    keywords_str = request.args.get('keywords', '')
    if not keywords_str:
        return jsonify({'error': '请提供关键词'}), 400
    
    keywords = [k.strip() for k in keywords_str.split(',') if k.strip()][:5]  # 最多5个关键词
    geo = request.args.get('geo', 'US').upper()
    timeframe = request.args.get('timeframe', 'today 3-m')
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    
    # 检查数据库是否有今日数据（除非强制刷新）
    if not force_refresh:
        cached = get_daily_data(TRENDS_DB)
        if cached:
            return jsonify({'data': cached, 'cached': True})
    
    try:
        import os
        import requests
        
        # 获取 SerpAPI 密钥 (支持多种命名格式)
        serpapi_key = os.environ.get('SERPAPI_KEY') or os.environ.get('SERP_API_KEY') or os.environ.get('serp_api_key')
        if not serpapi_key:
            return jsonify({'error': '请在 .env 中配置 SERPAPI_KEY'}), 500
        
        # 国家代码映射
        geo_map = {
            'US': 'US', 'GB': 'GB', 'UK': 'GB',
            'DE': 'DE', 'FR': 'FR', 'JP': 'JP',
            'CN': 'CN', 'AU': 'AU', 'CA': 'CA',
            'IN': 'IN', 'BR': 'BR',
        }
        serp_geo = geo_map.get(geo.upper(), 'US')
        
        # 时间范围映射
        timeframe_map = {
            'now 1-d': 'today 1-d',
            'now 7-d': 'today 7-d',
            'today 3-m': 'today 3-m',
            'today 12-m': 'today 12-m',
        }
        serp_time = timeframe_map.get(timeframe, 'today 3-m')
        
        # SerpAPI google_trends 引擎默认返回时间序列数据，不需要 data_type 参数
        serp_url = "https://serpapi.com/search"
        kw_list = keywords[0] if keywords else 'iphone'
        params = {
            'engine': 'google_trends',
            'q': kw_list,
            'geo': serp_geo,
            'date': serp_time,
            'api_key': serpapi_key,
        }
        
        resp = requests.get(serp_url, params=params, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        
        timeline = []
        if 'interest_over_time' in result and 'timeline_data' in result['interest_over_time']:
            weekly = []
            for item in result['interest_over_time']['timeline_data']:
                date_str = item.get('date', '')
                month_key = _parse_serp_week_to_month(date_str)
                vals = item.get('values', [])
                val = vals[0].get('value', 0) if vals else 0
                weekly.append({'month': month_key, 'value': int(val) if val != '' else 0})

            # 按周独立输出，同月多周加 -Wn 后缀区分
            week_counter = {}
            for w in weekly:
                m = w['month']
                week_counter[m] = week_counter.get(m, 0) + 1
                label = m if week_counter[m] == 1 else f"{m}-W{week_counter[m]}"
                timeline.append({'date': label, kw_list: w['value']})
        
        # 相关查询
        related_queries = []
        if 'related_queries' in result:
            for rq in result['related_queries'].get('top', [])[:10]:
                if isinstance(rq, dict):
                    related_queries.append({
                        'query': rq.get('query', ''),
                        'value': rq.get('value', ''),
                    })
        
        data = {
            'keywords': keywords,
            'geo': geo,
            'timeframe': timeframe,
            'timeline': timeline,
            'regions': [],
            'related_queries': related_queries,
        }
        
        save_daily_data(TRENDS_DB, data)
        return jsonify({'data': data, 'cached': False})
        
    except Exception as e:
        return jsonify({'error': f'获取失败: {str(e)[:200]}'}), 500


@app.route('/api/ebay-detail')
def ebay_detail():
    """
    eBay 商品详情（通过 Browse API）
    参数: item_id (必需)
    """
    item_id = request.args.get('item_id', '').strip()
    if not item_id:
        return jsonify({'error': '请提供商品ID'}), 400

    # 查缓存
    cached_detail, _ = get_item_cache(item_id)
    if cached_detail:
        return jsonify({'data': cached_detail, 'cached': True})

    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from ebay.ebay_browse_api import eBayItemFetcher

        # 重试机制：最多重试3次
        summary = None
        last_error = None
        for attempt in range(1, 4):
            try:
                fetcher = eBayItemFetcher()
                summary = fetcher.get_item_summary(item_id)
                if summary:
                    break
            except Exception as e:
                last_error = e
                if attempt < 3:
                    import time
                    print(f"[eBay详情] item_id={item_id} 第{attempt}次失败，重试中...")
                    sys.stdout.flush()
                    time.sleep(1)

        if summary:
            save_item_cache(item_id, detail=summary)
            return jsonify({'data': summary, 'cached': False})
        else:
            return jsonify({'error': f'获取详情失败: {str(last_error)[:200]}'}), 500
    except Exception as e:
        return jsonify({'error': f'获取详情失败: {str(e)[:200]}'}), 500


@app.route('/api/ebay-purchase-30d')
def ebay_purchase_30d():
    """
    eBay 近30天动销查询
    参数: item_id (必需), cdp_url (默认 localhost:9222)
    """
    item_id = request.args.get('item_id', '').strip()
    if not item_id:
        return jsonify({'error': '请提供商品ID'}), 400

    cdp_url = request.args.get('cdp_url', 'http://localhost:9222')

    # 查缓存
    _, cached_purchase = get_item_cache(item_id)
    if cached_purchase:
        return jsonify({'data': cached_purchase, 'cached': True})

    if ph is None:
        return jsonify({'error': 'purchase_history 模块未安装，无法查询30天动销'}), 503

    try:
        from datetime import datetime, timedelta

        results = ph.batch_fetch_purchase_history(
            [item_id], cdp_url=cdp_url, max_sales=100
        )
        if results:
            result = results[0]
            if result.get('error') and 'login expired' in result.get('error', '').lower():
                return jsonify({'error': result.get('error', '登录已过期，请重新登录eBay')}), 401
            if result.get('error') and 'connection failed' in result.get('error', '').lower():
                return jsonify({'error': '无法连接到Chrome，请确保CDP服务正在运行 (http://localhost:9222)'}), 503
            cutoff = datetime.now() - timedelta(days=30)
            records = []
            for rec in result.get('records', []):
                dt = rec.get('purchase_date', '')
                if dt and dt >= cutoff.strftime('%Y-%m-%d'):
                    records.append(rec)
            data = {
                'item_title': result.get('item_title', ''),
                'buy_it_now_price': result.get('buy_it_now_price', ''),
                'total_purchases': result.get('total_purchases', 0),
                'recent_30d': len(records),
                'truncated_at': result.get('truncated_at', 0),
                'error': result.get('error', ''),
                'records': records,
            }
            save_item_cache(item_id, purchase=data)
            return jsonify({'data': data, 'cached': False})
        else:
            return jsonify({'error': '查询无返回'}), 500
    except Exception as e:
        return jsonify({'error': f'查询失败: {str(e)[:200]}'}), 500


# ===== 卖家精灵 CDP 抓取代理 =====
_sellersprite_scraper = None

def _get_sellersprite_scraper():
    """延迟加载卖家精灵抓取模块"""
    global _sellersprite_scraper
    if _sellersprite_scraper is None:
        try:
            import sys
            print(f"[DEBUG] sys.path cwd={os.getcwd()}")
            from amazon.sellersprite_cdp_scraper import scrape_sellersprite_product
            _sellersprite_scraper = scrape_sellersprite_product
            print("[INIT] amazon.sellersprite_cdp_scraper loaded OK")
        except Exception as e:
            print(f"[INIT] amazon.sellersprite_cdp_scraper load failed: {type(e).__name__}: {e}")
    return _sellersprite_scraper


@app.route('/api/sellersprite/search')
def sellersprite_search():
    """
    卖家精灵产品研究 CDP 抓取
    参数:
        - keyword: 搜索关键词 (必需)
        - market: 站点 (US/UK/DE/JP 等), 默认 US
        - pages: 抓取页数 (1-10), 默认 1
        - cdp_port: CDP 端口, 默认 9222
    """
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        return jsonify({'error': '请输入关键词'}), 400

    market = request.args.get('market', 'US').upper()
    pages = int(request.args.get('pages', 4))
    cdp_port = int(request.args.get('cdp_port', 9222))

    print(f"[卖家精灵] 开始抓取: 关键词={keyword}, 市场={market}, 页数={pages}")

    # 检查缓存
    cached = get_sellersprite_cache(keyword, market, pages)
    if cached:
        print(f"[卖家精灵] 使用缓存: {keyword}")
        cached['cached'] = True
        return jsonify(cached)

    scraper = _get_sellersprite_scraper()
    if not scraper:
        print(f"[卖家精灵] 错误: scraper 模块未找到")
        return jsonify({'error': 'sellersprite_cdp_scraper 模块未找到，请确认文件存在'}), 503

    # 设置 CDP 端口
    import amazon.sellersprite_cdp_scraper as _ss_mod
    _ss_mod.CDP_PORT = cdp_port

    # 快速预检 CDP 连接（3秒内返回），避免用户长时间等待无响应
    try:
        cdp_test = _ss_mod.test_cdp_connection()
        if not cdp_test.get('success'):
            return jsonify({'success': False, 'error': f'CDP连接失败 ({cdp_port}): 无法连接到Chrome调试端口。请确保Chrome已开启远程调试模式（--remote-debugging-port={cdp_port})'}), 503
        print(f"[卖家精灵] CDP预检通过: {cdp_test.get('tabs', 0)} 个标签页")
    except Exception as cdp_err:
        return jsonify({'success': False, 'error': f'CDP连接异常: {str(cdp_err)[:100]}。请检查Chrome是否已启动调试模式'}), 503

    try:
        result = scraper(
            keyword=keyword,
            market=market,
            pages=pages,
        )

        if result.get('success') and result.get('products'):
            products = []
            for p in result['products']:
                if hasattr(p, 'to_dict'):
                    products.append(p.to_dict())
                elif isinstance(p, dict):
                    products.append(p)

            # 计算市场概览统计
            sales_list = [p.get('sales_parent', 0) for p in products if p.get('sales_parent', 0) > 0]
            price_list = [p.get('price', 0) for p in products if p.get('price', 0) > 0]
            rating_list = [p.get('rating', 0) for p in products if p.get('rating', 0) > 0]
            review_list = [p.get('review_count', 0) for p in products if p.get('review_count', 0) > 0]

            market_overview = {
                'total_products': result.get('total', len(products)),
                'avg_sales': round(sum(sales_list) / len(sales_list)) if sales_list else 0,
                'avg_price': round(sum(price_list) / len(price_list), 2) if price_list else 0,
                'avg_rating': round(sum(rating_list) / len(rating_list), 1) if rating_list else 0,
                'avg_reviews': round(sum(review_list) / len(review_list)) if review_list else 0,
                'top_sales': max(sales_list) if sales_list else 0,
                'total_sales_amount': round(sum(p.get('sales_amount', 0) for p in products), 2),
            }

            response_data = {
                'success': True,
                'products': products,
                'market': market_overview,
                'pages_scraped': result.get('pages_scraped', 1),
                'cached': False,
            }

            # 保存缓存
            save_sellersprite_cache(keyword, market, pages, response_data)
            print(f"[卖家精灵] 成功: {keyword}, 获取 {len(products)} 个产品")

            return jsonify(response_data)
        else:
            error = result.get('error', '未获取到数据')
            print(f"[卖家精灵] 失败: {keyword}, 错误: {error}")
            return jsonify({'success': False, 'error': error[:200]}), 500

    except Exception as e:
        import traceback
        print(f"[卖家精灵] 异常: {keyword}, 错误: {e}")
        traceback.print_exc()
        return jsonify({'error': f'抓取失败: {str(e)[:200]}'}), 500


@app.route('/api/sellersprite/test-connection')
def sellersprite_test_connection():
    """测试 CDP 连接"""
    cdp_port = int(request.args.get('cdp_port', 9222))
    try:
        import amazon.sellersprite_cdp_scraper as _ss_mod
        _ss_mod.CDP_PORT = cdp_port
        result = _ss_mod.test_cdp_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ═══════════════════ SSE 流式接口 ═══════════════════

import queue
import threading

# 全局任务队列存储（task_id → queue）
_sse_queues = {}
_sse_lock = threading.Lock()


def _sse_push(task_id: str, event_type: str, data: dict):
    """线程安全地向指定任务的队列推送数据"""
    with _sse_lock:
        if task_id in _sse_queues:
            try:
                _sse_queues[task_id].put_nowait((event_type, data))
            except:
                pass


# ===== 榜单扫描模块路由 =====
try:
    from ranking.rank_routes import register_routes as _reg_rank
    _reg_rank(app, _sse_push=_sse_push, _queues=_sse_queues, _lock=_sse_lock)
except Exception as ex:
    import warnings
    warnings.warn(f'[ranking] 模块加载失败: {ex}')


@app.route('/api/sellersprite/stream')
def sellersprite_stream():
    """
    卖家精灵 SSE 流式接口
    前端用 EventSource 接收，每抓完一页实时推送
    架构：线程 + Queue，主线程读队列 yield，子线程跑爬虫
    """
    from flask import Response
    import json, uuid

    keyword = request.args.get('keyword', '').strip()
    market = request.args.get('market', 'US').upper()
    pages = int(request.args.get('pages', 4))
    cdp_port = int(request.args.get('cdp_port', 9222))

    if not keyword:
        return Response(
            f"event: error\ndata: {json.dumps({'error': '缺少关键词'}, ensure_ascii=False)}\n\n",
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )

    import amazon.sellersprite_cdp_scraper as _ss_mod
    _ss_mod.CDP_PORT = cdp_port

    # 检查缓存
    cached = get_sellersprite_cache(keyword, market, pages)
    if cached:
        return Response(
            f"event: cached\ndata: {json.dumps(cached, ensure_ascii=False, default=str)}\n\n",
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )

    # 生成任务ID，创建队列
    task_id = str(uuid.uuid4())[:8]
    q = queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_queues[task_id] = q

    def _run_in_thread():
        """在子线程中运行爬虫，回调将数据放入队列"""
        try:
            import asyncio

            def page_cb(page_num, page_items):
                products = []
                for item in page_items:
                    p = _ss_mod._parse_row(
                        0, item.get("asin", ""), item.get("title", ""),
                        item.get("img", ""), None, item.get("cells", [])
                    )
                    for field in ['brand', 'seller', 'sellerCount', 'delivery', 'tags',
                                   'bsrMain', 'bsrSub']:
                        val = item.get(field)
                        if val is not None:
                            setattr(p, field, val)
                    products.append(p.to_dict())

                payload = {
                    'type': 'page',
                    'page': page_num,
                    'products': products,
                    'count': len(products),
                }
                _sse_push(task_id, 'page', payload)

            def progress_cb(pct, msg):
                _sse_push(task_id, 'progress', {'type': 'progress', 'pct': pct, 'msg': msg})

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            data = loop.run_until_complete(
                _ss_mod._scrape_impl(keyword, market, pages, page_cb, progress_cb)
            )
            loop.close()

            rows = data.get("rows", [])
            headers = data.get("headers", [])
            products = []
            seen = set()
            for idx, row in enumerate(rows):
                asin = row.get("asin", "")
                if asin and asin in seen:
                    continue
                if asin:
                    seen.add(asin)
                p = _ss_mod._parse_row(
                    idx, asin, row.get("title", ""),
                    row.get("img", ""), headers, row.get("cells", [])
                )
                for field in ['brand', 'seller', 'sellerCount', 'delivery', 'tags',
                               'bsrMain', 'bsrSub']:
                    val = row.get(field)
                    if val is not None:
                        setattr(p, field, val)
                products.append(p.to_dict())

            sales_list = [x['sales_parent'] for x in products if x.get('sales_parent')]
            price_list = [x['price'] for x in products if x.get('price')]
            rating_list = [x['rating'] for x in products if x.get('rating')]
            review_list = [x['review_count'] for x in products if x.get('review_count')]

            market_overview = {
                'total_products': len(products),
                'avg_sales': round(sum(sales_list)/len(sales_list)) if sales_list else 0,
                'avg_price': round(sum(price_list)/len(price_list), 2) if price_list else 0,
                'avg_rating': round(sum(rating_list)/len(rating_list), 1) if rating_list else 0,
                'avg_reviews': round(sum(review_list)/len(review_list)) if review_list else 0,
            }

            resp_data = {
                'success': True, 'products': products, 'market': market_overview,
                'pages_scraped': pages, 'cached': False,
            }
            save_sellersprite_cache(keyword, market, pages, resp_data)
            _sse_push(task_id, 'done', resp_data)

        except Exception as e:
            import traceback
            traceback.print_exc()
            _sse_push(task_id, 'error', {'error': str(e)[:200]})
        finally:
            # 爬虫结束，发送 None 信号
            try:
                q.put_nowait((None, None))
            except:
                pass
            with _sse_lock:
                _sse_queues.pop(task_id, None)

    # 启动子线程运行爬虫
    t = threading.Thread(target=_run_in_thread, daemon=True)
    t.start()

    def gen():
        while True:
            try:
                event_type, data = q.get(timeout=120)
                if event_type is None:
                    break
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            except queue.Empty:
                yield f"event: error\ndata: {json.dumps({'error': '超时（2分钟无响应）'}, ensure_ascii=False)}\n\n"
                break

    return Response(
        gen(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@app.route('/api/ebay/stream')
def ebay_stream():
    """
    eBay 爬虫 SSE 流式接口
    前端用 EventSource 接收，每抓完一页实时推送
    架构：线程 + Queue
    """
    from flask import Response
    import json, uuid

    keyword = request.args.get('keyword', '').strip()
    sort = request.args.get('sort', 'best_match')
    last_n = int(request.args.get('last', 999))
    max_pages = int(request.args.get('pages', 4))

    if not keyword:
        return Response(
            f"event: error\ndata: {json.dumps({'error': '缺少关键词'}, ensure_ascii=False)}\n\n",
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )

    task_id = str(uuid.uuid4())[:8]
    q = queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_queues[task_id] = q

    def _run_in_thread():
        def page_cb(page_num, page_items):
            payload = {
                'type': 'page',
                'page': page_num,
                'items': page_items,
                'count': len(page_items),
            }
            _sse_push(task_id, 'page', payload)

        try:
            from ebay.ebay_scraper import scrape
            result = scrape(
                keyword=keyword, sort=sort, last_n=last_n,
                max_pages=max_pages, progress_callback=page_cb,
            )
            _sse_push(task_id, 'done', result)
        except Exception as e:
            import traceback
            traceback.print_exc()
            _sse_push(task_id, 'error', {'error': str(e)[:200]})
        finally:
            try:
                q.put_nowait((None, None))
            except:
                pass
            with _sse_lock:
                _sse_queues.pop(task_id, None)

    t = threading.Thread(target=_run_in_thread, daemon=True)
    t.start()

    def gen():
        while True:
            try:
                event_type, data = q.get(timeout=180)
                if event_type is None:
                    break
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            except queue.Empty:
                yield f"event: error\ndata: {json.dumps({'error': '超时（3分钟无响应）'}, ensure_ascii=False)}\n\n"
                break

    return Response(
        gen(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@app.route('/')
def index():
    """提供前端页面"""
    resp = make_response(send_from_directory('app', 'selection_system.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.route('/trends.html')
def trends_page():
    """关键词趋势分析页面"""
    resp = make_response(send_from_directory('app', 'trends.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


# ===== OE号市场搜索接口 =====

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_EBAY_DIR = os.path.join(_APP_DIR, 'ebay')


def _run_scraper_direct(script_name: str, keyword: str) -> dict:
    """直接在线程中调用爬虫函数（避免子进程触发反爬）"""
    try:
        if script_name == 'amazon_scraper':
            from amazon.amazon_scraper import scrape_data as amazon_scrape
            result = amazon_scrape(keyword, last_n=999, max_pages=4)
        elif script_name == 'ebay_scraper':
            from ebay.ebay_scraper import scrape_data as ebay_scrape
            result = ebay_scrape(keyword, last_n=999, max_pages=4)
        else:
            return {"success": False, "error": f"未知爬虫: {script_name}"}

        return {
            'success': result.get('success', False),
            'error': result.get('error', ''),
            'items': result.get('items', []),
            'keyword': result.get('keyword', keyword),
            'total_results': result.get('amazon_total_results', result.get('ebay_total_results', 0)),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _filter_matched_items(items: list, oe_number: str) -> list:
    """过滤标题包含OE号的产品"""
    if not items:
        return []
    oe_clean = oe_number.upper().replace(' ', '').replace('/', ' ')
    filtered = []
    for item in items:
        title = item.get('title', '').upper()
        if oe_clean in title or oe_number.upper() in title:
            filtered.append(item)
    filtered.sort(key=lambda x: x.get('total', 0) or 99999)
    return filtered


def _mark_oe_matched(items: list, oe_number: str) -> list:
    """为每个商品标记是否标题包含OE号"""
    oe_clean = oe_number.upper().replace(' ', '').replace('/', ' ')
    for item in items:
        title = item.get('title', '').upper()
        item['oe_matched'] = bool(oe_clean in title or oe_number.upper() in title)
    return items


def _get_market_summary(items: list, oe_number: str) -> dict:
    """直接透传爬虫原始搜索结果，不做任何排序/匹配/统计处理"""
    if not items:
        return {"found": False, "count": 0, "items": []}

    # 直接返回原始结果，保持爬虫返回的原始顺序
    return {
        "found": True,
        "count": len(items),
        "items": items,
    }


_amazon_search_lock = threading.Lock()

@app.route('/api/amazon-title-search', methods=['GET', 'POST'])
def amazon_title_search():
    """
    用产品标题关键词搜索 Amazon 搜索结果
    GET /api/amazon-title-search?title=xxx&asin=xxx
    POST /api/amazon-title-search  (body: title=xxx&asin=xxx)

    返回:
        - amazon_total: Amazon 搜索结果总数
        - scraped_count: 实际抓取到的产品数
        - matched_count: 标题包含原产品名的产品数
        - price_distribution: 价格分布桶
        - matched_items: 匹配的产品列表 (前20)
    """
    title = request.values.get('title', '').strip() if request.method == 'POST' else request.args.get('title', '').strip()
    asin = request.values.get('asin', '').strip() if request.method == 'POST' else request.args.get('asin', '').strip()
    if not title:
        return jsonify({'error': 'title 参数必填'}), 400

    # 提取关键词：取标题前 60 个字符作为搜索词（太长会搜不到结果）
    # 去掉括号内内容和特殊字符
    import re
    search_kw = re.sub(r'[\(\[].*?[\)\]]', '', title)
    search_kw = re.sub(r'[,\.;:!?]', ' ', search_kw)
    search_kw = ' '.join(search_kw.split())[:80]
    if not search_kw:
        search_kw = title[:60]

    print(f"[Amazon标题搜索] ASIN={asin}, 搜索词={search_kw}, 原标题={title[:50]}")
    import sys as _sys; _sys.stdout.flush()

    try:
        with _amazon_search_lock:
            raw = _run_scraper_direct('amazon_scraper', search_kw)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'爬虫异常: {str(e)[:200]}', 'amazon_total': 0, 'scraped_count': 0, 'matched_count': 0}), 500
    if not raw.get('success') or not raw.get('items'):
        error = raw.get('error', '未搜到结果')
        print(f"[Amazon标题搜索] 失败: {error}")
        return jsonify({'success': False, 'error': error, 'amazon_total': 0, 'scraped_count': 0, 'matched_count': 0})

    items = raw['items']
    amazon_total = raw.get('amazon_total_results', raw.get('total_results', len(items)))

    # 匹配标题相似产品：原标题的关键词在搜索结果标题中出现 >= 3 个
    # 提取原标题的核心词（>=4字符的词，去掉常见停用词）
    stop_words = {'for', 'with', 'and', 'the', 'fit', 'compatible', 'kit', 'set', 'new', 'brand', 'high', 'quality', 'replacement', 'parts', 'accessories'}
    original_words = set(w.lower() for w in re.findall(r'[a-zA-Z0-9]+', title) if len(w) >= 4 and w.lower() not in stop_words)

    matched_items = []
    for item in items:
        item_title = item.get('title', '').lower()
        item_words = set(w.lower() for w in re.findall(r'[a-zA-Z0-9]+', item_title))
        overlap = original_words & item_words
        if len(overlap) >= 3:
            item['match_score'] = len(overlap)
            matched_items.append(item)

    matched_items.sort(key=lambda x: x.get('match_score', 0), reverse=True)

    # 价格分布 (10个桶)
    all_prices = [x.get('price', 0) or x.get('total', 0) for x in items if (x.get('price', 0) or x.get('total', 0)) > 0]
    price_dist = {}
    if all_prices:
        min_p, max_p = min(all_prices), max(all_prices)
        step = (max_p - min_p) / 10 if max_p > min_p else 1
        for i in range(10):
            lo = min_p + i * step
            hi = lo + step
            label = f"${lo:.0f}-${hi:.0f}"
            count = sum(1 for p in all_prices if (i == 9 and p >= lo and p <= hi) or (p >= lo and p < hi))
            price_dist[label] = count

    result = {
        'success': True,
        'search_keyword': search_kw,
        'amazon_total': amazon_total,
        'scraped_count': len(items),
        'matched_count': len(matched_items),
        'price_distribution': price_dist,
        'matched_items': matched_items[:20],  # 只返回前20个匹配的
        'all_prices': all_prices[:200],  # 供前端画分布图
    }
    print(f"[Amazon标题搜索] 成功: Amazon总数={amazon_total}, 抓取={len(items)}, 匹配={len(matched_items)}")
    return jsonify(result)


@app.route('/api/competitor-detail')
def competitor_detail():
    """竞品链接解析 - 根据 Amazon ASIN / eBay Item ID / 链接解析产品真实信息"""
    input_str = request.args.get('input', '').strip()
    if not input_str:
        return jsonify({'success': False, 'error': '请输入竞品链接或ID'})

    print(f"[竞品解析] 输入: {input_str}")

    # 识别平台
    platform = None
    identifier = None

    # Amazon 链接
    if 'amazon.' in input_str.lower() or '/dp/' in input_str or '/gp/product/' in input_str:
        platform = 'Amazon'
        m = re.search(r'(?:dp|gp/product|ASIN|offer-listing)/([A-Z0-9]{10})', input_str, re.IGNORECASE)
        if m:
            identifier = m.group(1).upper()
    # eBay 链接
    elif 'ebay.' in input_str.lower():
        platform = 'eBay'
        m = re.search(r'/itm/(\d{10,13})', input_str)
        if not m:
            m = re.search(r'item[=/](\d{10,13})', input_str)
        if m:
            identifier = m.group(1)
    # 纯 ASIN
    elif re.fullmatch(r'[A-Z0-9]{10}', input_str, re.IGNORECASE):
        platform = 'Amazon'
        identifier = input_str.upper()
    # 纯 eBay ID
    elif re.fullmatch(r'\d{10,13}', input_str):
        platform = 'eBay'
        identifier = input_str
    else:
        return jsonify({'success': False, 'error': '无法识别格式，请输入 Amazon 链接/ASIN 或 eBay 链接/Item ID'})

    if not identifier:
        return jsonify({'success': False, 'error': '无法从输入中提取商品ID'})

    if platform == 'Amazon':
        result = _fetch_amazon_detail(identifier)
    else:
        result = _fetch_ebay_detail(identifier)

    return jsonify(result)


def _fetch_amazon_detail(asin: str) -> dict:
    """通过 Playwright 解析 Amazon ASIN 详情"""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--disable-web-security"],
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="en-US",
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)
            page = context.new_page()
            page.set_default_timeout(30000)

            try:
                from amazon.competitor_detail import parse_amazon_asin
                result = parse_amazon_asin(page, asin)
                print(f"[竞品解析] Amazon {asin}: {result.get('title', '')[:60]}... | ${result.get('price', 0)}")
            finally:
                page.close()
                context.close()
                browser.close()

            return result

    except Exception as e:
        print(f"[竞品解析] Amazon {asin} 失败: {e}")
        return {"success": False, "platform": "Amazon", "error": str(e)}


def _fetch_ebay_detail(item_id: str) -> dict:
    """通过 eBay Browse API 解析商品详情"""
    try:
        from amazon.competitor_detail import parse_ebay_item
        result = parse_ebay_item(item_id)
        print(f"[竞品解析] eBay {item_id}: {result.get('title', '')[:60]}... | ${result.get('price', 0)}")
        return result
    except Exception as e:
        print(f"[竞品解析] eBay {item_id} 失败: {e}")
        return {"success": False, "platform": "eBay", "error": str(e)}


@app.route('/api/oe-search')
def oe_search():
    """OE号搜索 - 调用Amazon和eBay爬虫（带1天缓存）"""
    oe = request.args.get('oe', '').strip()
    platform = request.args.get('platform', 'both').strip()  # both / amazon / ebay
    if not oe:
        return jsonify({'error': '请输入关键词'}), 400

    # 查缓存
    cached = get_oe_search_cache(oe, platform)
    if cached:
        print(f"[OE Search] 命中缓存: {oe} ({platform})")
        cached['_from_cache'] = True
        return jsonify(cached)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    result = {
        "oe": oe,
        "amazon": {"found": False, "count": 0, "items": [], "error": ""},
        "ebay": {"found": False, "count": 0, "items": [], "error": ""},
    }

    def search_amazon():
        raw = _run_scraper_direct('amazon_scraper', oe)
        if raw.get('success') and raw.get('items'):
            return ('amazon', _get_market_summary(raw['items'], oe))
        return ('amazon', {"found": False, "count": 0, "items": [], "error": raw.get('error', '')})

    def search_ebay():
        raw = _run_scraper_direct('ebay_scraper', oe)
        print(f"[OE Search] eBay爬虫返回: success={raw.get('success')}, items数量={len(raw.get('items', []))}")
        if raw.get('success') and raw.get('items'):
            items = raw['items']
            print(f"[OE Search] eBay items[0] keys: {list(items[0].keys()) if items else 'empty'}")
            # 直接透传，不排序/不过滤
            return ('ebay', {"found": True, "count": len(items), "items": items, "error": ""})
        return ('ebay', {"found": False, "count": 0, "items": [], "error": raw.get('error', '')})

    tasks = []
    if platform in ('both', 'amazon'):
        tasks.append(search_amazon)
    if platform in ('both', 'ebay'):
        tasks.append(search_ebay)

    if tasks:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(fn): fn.__name__ for fn in tasks}
            for future in as_completed(futures):
                plat, data = future.result()
                result[plat] = data

    # 保存缓存
    save_oe_search_cache(oe, platform, result)

    return jsonify(result)


@app.route('/api/amazon-catalog/search', methods=['GET'])
def amazon_catalog_search():
    """
    Amazon SP-API listCatalogItems 关键词搜索
    GET /api/amazon-catalog/search?keyword=xxx&marketplace=US

    marketplace 支持: US CA MX BR UK DE FR IT ES NL SE PL JP AU SG AE IN
    """
    if sp_api is None:
        print(f"[Amazon目录] 错误: amazon_sp_api 模块未加载")
        return jsonify({"error": "amazon_sp_api 模块未加载，请检查 amazon_sp_api.py 是否存在"}), 500

    keyword    = request.args.get('keyword', '').strip()
    marketplace = request.args.get('marketplace', 'US').strip().upper()
    included_type = request.args.get('included_type', 'APPLICABLE').strip()

    print(f"[Amazon目录] 搜索: 关键词={keyword}, 市场={marketplace}")

    if not keyword:
        return jsonify({"error": "keyword 参数必填"}), 400

    if marketplace not in sp_api.MARKETPLACE_IDS:
        return jsonify({
            "error": f"不支持的 marketplace: {marketplace}",
            "supported": list(sp_api.MARKETPLACE_IDS.keys()),
        }), 400

    try:
        result = sp_api.list_catalog_items(
            keywords      = keyword,
            marketplace   = marketplace,
            included_type = included_type,
        )
        items = result.get('items', [])
        print(f"[Amazon目录] 成功: {keyword}, 获取 {len(items)} 个结果")
        return jsonify(result)
    except Exception as e:
        import traceback
        print(f"[Amazon目录] 异常: {keyword}, 错误: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/amazon-catalog/item/<asin>', methods=['GET'])
def amazon_catalog_item(asin):
    """
    Amazon SP-API getCatalogItem  - 根据 ASIN 获取商品详情
    GET /api/amazon-catalog/item/B09V3KXJPB?marketplace=US
    """
    if sp_api is None:
        return jsonify({"error": "amazon_sp_api 模块未加载"}), 500

    marketplace = request.args.get('marketplace', 'US').strip().upper()
    if marketplace not in sp_api.MARKETPLACE_IDS:
        return jsonify({"error": f"不支持的 marketplace: {marketplace}"}), 400

    try:
        result = sp_api.get_catalog_item(asin.strip(), marketplace=marketplace)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ===== 运德海外仓 API =====
import hashlib as _hashlib
import requests as _requests

_WEDE_API_BASE = "http://fg.wedoexpress.com"
_WEDE_SHIP_FEE_PATH = "/api.php?mod=apiManage&act=getShipFeeQuery"
_WEDE_CARRIER_PATH = "/api.php?mod=apiManage&act=queryCarrierInfo"
_WEDE_USER = "pjV391564"
_WEDE_TOKEN = "88C5D8B292E5B6153803682554FBA4F8"

_WEDE_EXCLUDE_KW = ["自有渠道", "自提", "自送", "调拨", "FBA", "卡车派送", "组合渠道",
                     "客户", "CBT", "SHEIN", "Xmiles", "XMILES", "SpeedX", "speedx"]
_WEDE_CARRIER_CACHE = {"data": None, "ts": 0}
_WEDE_CARRIER_TTL = 1800


def _wede_sign(params, token):
    sorted_keys = sorted(k for k in params if k != "sign")
    sign_str = "".join(str(params[k]) for k in sorted_keys) + token
    return _hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()


def _wede_post(path, content):
    content_json = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    params = {"userAccount": _WEDE_USER, "content": content_json}
    params["sign"] = _wede_sign(params, _WEDE_TOKEN)
    resp = _requests.post(_WEDE_API_BASE + path, data=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _wede_get_carriers(warehouse=""):
    """获取运德可用渠道"""
    global _WEDE_CARRIER_CACHE
    now = time.time()
    if _WEDE_CARRIER_CACHE["data"] is None or (now - _WEDE_CARRIER_CACHE["ts"]) > _WEDE_CARRIER_TTL:
        result = _wede_post(_WEDE_CARRIER_PATH, {"getCarrier": "wedo"})
        if str(result.get("errCode")) != "200":
            return []
        all_channels = []
        for wh_code, carriers in result.get("data", {}).items():
            for c in carriers:
                code = c.get("carrierCode", "")
                name = c.get("carrierName", "")
                if code and not any(kw in f"{code} {name}" for kw in _WEDE_EXCLUDE_KW):
                    all_channels.append({"code": code, "name": name, "warehouse": wh_code})
        _WEDE_CARRIER_CACHE = {"data": all_channels, "ts": now}
    if warehouse:
        return [c for c in _WEDE_CARRIER_CACHE["data"] if c["warehouse"] == warehouse]
    return _WEDE_CARRIER_CACHE["data"]


def _wede_query_fee(channel_codes, postcode, weight, length, width, height, country="US", city=""):
    """查询运德运费，返回 {渠道码: {shipFee, ...}}"""
    results = {}
    for code in channel_codes:
        try:
            content = {
                "channelCode": code, "country": country, "city": city,
                "postcode": str(postcode), "weight": str(weight),
                "length": str(length), "width": str(width), "height": str(height),
                "signatureService": 0,
            }
            result = _wede_post(_WEDE_SHIP_FEE_PATH, content)
            if str(result.get("errCode")) == "200":
                for ch_code, ch_data in result.get("data", {}).items():
                    fee = ch_data.get("shipFee")
                    if fee is not None and float(fee) > 1:
                        results[ch_code] = float(fee)
        except Exception:
            continue
    return results


def _get_exchange_rate():
    """获取实时汇率"""
    try:
        r = _requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        data = r.json()
        if data.get("rates", {}).get("CNY"):
            return round(data["rates"]["CNY"], 4)
    except Exception:
        pass
    return 7.25


@app.route('/api/profit-calc', methods=['POST'])
def profit_calc():
    """
    利润计算接口 - 默认固定邮编模式
    POST JSON body:
    {
        platform: "ebay" | "amazon",
        price_usd: 售价(美元),
        cost_cny: 产品成本(人民币，不含税),
        weight: 重量(kg),
        length/width/height: 尺寸(cm),
        warehouse: "BOTH" | "WDUSNJ" | "WDUSLG",
        head_freight_rate: 头程单价(元/m3),
        ship_fee_mode: "fixed_address" (默认) | "sample_zipcodes",
        fixed_postcode: 固定邮编 (默认91710),
        fixed_city: 固定城市 (默认 "Chino, CA"),
        // eBay 费用（勾选后才生效，未勾选传0）
        ebay_rate: 13.25,
        return_rate: 3,    // 0=不计算
        ad_rate: 4,        // 0=不计算
        storage_rate: 2.27, // 0=不计算
        // Amazon 费用（勾选后才生效，未勾选传0）
        amazon_commission_rate: 15.0,
        fba_fulfillment_rate: 3.5,
        amazon_return_rate: 5.0,  // 0=不计算
        amazon_ad_rate: 5.0,      // 0=不计算
    }
    """
    params = request.get_json()
    if not params:
        return jsonify({"error": "请提供参数"}), 400

    platform = params.get("platform", "ebay")
    price_usd = float(params.get("price_usd", 0))
    cost_cny = float(params.get("cost_cny", 0))
    weight = float(params.get("weight", 0))
    length = float(params.get("length", 0))
    width = float(params.get("width", 0))
    height = float(params.get("height", 0))
    warehouse = params.get("warehouse", "BOTH")
    head_freight_rate = float(params.get("head_freight_rate", 1600))
    ship_fee_mode = params.get("ship_fee_mode", "fixed_address")
    fixed_postcode = str(params.get("fixed_postcode", "91710"))
    fixed_city = str(params.get("fixed_city", "Chino, CA"))

    if price_usd <= 0 or cost_cny <= 0 or weight <= 0:
        return jsonify({"error": "售价、成本、重量必须大于0"}), 400
    if length <= 0 or width <= 0 or height <= 0:
        return jsonify({"error": "长宽高必须大于0"}), 400

    exchange_rate = _get_exchange_rate()
    price_cny = price_usd * exchange_rate

    # 头程
    volume_m3 = (length * width * height) / 1000000
    head_freight = volume_m3 * head_freight_rate
    pretax_cost = cost_cny + head_freight

    # 平台费用
    if platform == "ebay":
        ebay_rate = float(params.get("ebay_rate", 13.25))
        return_rate = float(params.get("return_rate", 3))
        ad_rate = float(params.get("ad_rate", 4))
        storage_rate = float(params.get("storage_rate", 2.27))
        platform_fee = price_cny * ebay_rate / 100
        other_fee = price_cny * (return_rate + ad_rate + storage_rate) / 100
        other_fee_parts = []
        if return_rate > 0:
            other_fee_parts.append(f"退{return_rate}%")
        if ad_rate > 0:
            other_fee_parts.append(f"广{ad_rate}%")
        if storage_rate > 0:
            other_fee_parts.append(f"仓{storage_rate}%")
        other_fee_label = "其他费用(" + "+".join(other_fee_parts) + ")" if other_fee_parts else "其他费用(无)"
    else:
        amazon_commission_rate = float(params.get("amazon_commission_rate", 15.0))
        fba_fulfillment_rate = float(params.get("fba_fulfillment_rate", 3.5))
        amazon_return_rate = float(params.get("amazon_return_rate", 5.0))
        amazon_ad_rate = float(params.get("amazon_ad_rate", 5.0))
        platform_fee = price_cny * amazon_commission_rate / 100
        other_fee = price_cny * (amazon_return_rate + amazon_ad_rate) / 100
        fba_fee_cny = fba_fulfillment_rate * exchange_rate
        other_fee_parts = []
        if amazon_return_rate > 0:
            other_fee_parts.append(f"退{amazon_return_rate}%")
        if amazon_ad_rate > 0:
            other_fee_parts.append(f"ACOS{amazon_ad_rate}%")
        other_fee_label = "其他费用(" + "+".join(other_fee_parts) + ")" if other_fee_parts else "其他费用(无)"

    # 仓库配置
    WAREHOUSE_INFO = {
        "WDUSLG": {"name": "美西仓", "location": "加利福尼亚州圣地亚哥",
                    "zipcodes": [{"zip": "92115", "city": "San Diego, CA"}, {"zip": "91710", "city": "Chino, CA"},
                                 {"zip": "95240", "city": "Lodi, CA"}, {"zip": "84045", "city": "Saratoga Springs, UT"},
                                 {"zip": "77066", "city": "Houston, TX"}, {"zip": "61856", "city": "Monticello, IL"},
                                 {"zip": "53531", "city": "Deerfield, WI"}]},
        "WDUSNJ": {"name": "美东仓", "location": "新泽西州罗宾斯维尔",
                    "zipcodes": [{"zip": "08753", "city": "Toms River, NJ"}, {"zip": "02816", "city": "Coventry, RI"},
                                 {"zip": "22835", "city": "Luray, VA"}, {"zip": "32114", "city": "Daytona Beach, FL"},
                                 {"zip": "33860", "city": "Mulberry, FL"}, {"zip": "49456", "city": "Spring Lake, MI"}]},
    }

    # Amazon固定邮编
    AMAZON_AVG_ZIPCODES = [
        {"zip": "08016", "city": "Burlington, NJ"},
        {"zip": "91710", "city": "Chino, CA"},
    ]

    if warehouse == "BOTH":
        wh_keys = ["WDUSNJ", "WDUSLG"]
    elif warehouse in WAREHOUSE_INFO:
        wh_keys = [warehouse]
    else:
        wh_keys = ["WDUSNJ", "WDUSLG"]

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def calc_warehouse(wh_key):
        wh_info = WAREHOUSE_INFO.get(wh_key)
        channels = _wede_get_carriers(wh_key)
        channel_codes = [c["code"] for c in channels]

        if not channel_codes:
            return {
                "warehouse_key": wh_key, "warehouse_name": wh_info["name"],
                "warehouse_location": wh_info["location"],
                "ship_fee_usd": 0, "ship_fee_cny": 0, "fee_details": [],
                "ship_fee_mode": ship_fee_mode,
                "profit_detail": {},
            }

        fee_details = []

        if ship_fee_mode == "fixed_address":
            # 固定邮编模式
            if platform == "amazon":
                # Amazon: 按08016和91710取最低运费后平均
                for zc in AMAZON_AVG_ZIPCODES:
                    fees = _wede_query_fee(channel_codes, zc["zip"], weight, length, width, height, "US", zc["city"])
                    valid_fees = [f for f in fees.values() if f > 1]
                    channel_fee = min(valid_fees) if valid_fees else 0
                    fee_details.append({"zip": zc["zip"], "city": zc["city"], "valid_channels": len(valid_fees),
                                        "min_fee_usd": channel_fee, "mode_note": "Amazon固定: 08016+91710最低值平均"})
                valid_fees_list = [d["min_fee_usd"] for d in fee_details if d["min_fee_usd"] > 0]
                ship_fee_usd = sum(valid_fees_list) / len(valid_fees_list) if valid_fees_list else 0
            else:
                # eBay: 按用户指定的固定邮编查询
                fees = _wede_query_fee(channel_codes, fixed_postcode, weight, length, width, height, "US", fixed_city)
                valid_fees = [f for f in fees.values() if f > 1]
                ship_fee_usd = min(valid_fees) if valid_fees else 0
                fee_details.append({"zip": fixed_postcode, "city": fixed_city, "valid_channels": len(valid_fees),
                                    "min_fee_usd": ship_fee_usd, "mode_note": "固定邮编所有渠道最低值"})
        else:
            # 样本邮编模式（遍历仓库所有样本邮编）
            for zc in wh_info["zipcodes"]:
                fees = _wede_query_fee(channel_codes, zc["zip"], weight, length, width, height, "US", zc["city"])
                valid_fees = [f for f in fees.values() if f > 1]
                channel_fee = min(valid_fees) if valid_fees else 0
                fee_details.append({"zip": zc["zip"], "city": zc["city"], "valid_channels": len(valid_fees),
                                    "min_fee_usd": channel_fee})
            all_medians = [d["min_fee_usd"] for d in fee_details if d["min_fee_usd"] > 0]
            ship_fee_usd = min(all_medians) if all_medians else 0

        ship_fee_cny = ship_fee_usd * exchange_rate

        # 利润计算
        if platform == "ebay":
            profit_cny = price_cny - pretax_cost - ship_fee_cny - platform_fee - other_fee
            profit_rate = (profit_cny / price_cny) * 100 if price_cny > 0 else 0
            profit_detail = {
                "售价(CNY)": round(price_cny, 2),
                "产品成本": round(cost_cny, 2),
                "头程运费": round(head_freight, 2),
                "售前成本": round(pretax_cost, 2),
                "尾程运费": round(ship_fee_cny, 2),
                f"eBay佣金({ebay_rate}%)": round(platform_fee, 2),
                other_fee_label: round(other_fee, 2),
                "利润(CNY)": round(profit_cny, 2),
                "利润率(%)": round(profit_rate, 2),
            }
        else:
            profit_cny = price_cny - pretax_cost - ship_fee_cny - platform_fee - fba_fee_cny - other_fee
            profit_rate = (profit_cny / price_cny) * 100 if price_cny > 0 else 0
            profit_detail = {
                "售价(CNY)": round(price_cny, 2),
                "产品成本": round(cost_cny, 2),
                "头程运费": round(head_freight, 2),
                "售前成本": round(pretax_cost, 2),
                "尾程运费": round(ship_fee_cny, 2),
                f"亚马逊佣金({amazon_commission_rate}%)": round(platform_fee, 2),
                f"FBA配送费(${fba_fulfillment_rate})": round(fba_fee_cny, 2),
                other_fee_label: round(other_fee, 2),
                "利润(CNY)": round(profit_cny, 2),
                "利润率(%)": round(profit_rate, 2),
            }

        return {
            "warehouse_key": wh_key, "warehouse_name": wh_info["name"],
            "warehouse_location": wh_info["location"],
            "ship_fee_usd": round(ship_fee_usd, 2), "ship_fee_cny": round(ship_fee_cny, 2),
            "fee_details": fee_details, "ship_fee_mode": ship_fee_mode,
            "profit_detail": profit_detail,
        }

    with ThreadPoolExecutor(max_workers=2) as executor:
        wh_results = list(executor.map(calc_warehouse, wh_keys))

    # 计算平均利润
    combined = None
    valid_profits = [wh["profit_detail"].get("利润(CNY)", 0) for wh in wh_results if wh["profit_detail"]]
    if len(valid_profits) >= 2:
        avg_profit = sum(valid_profits) / len(valid_profits)
        avg_rate = (avg_profit / price_cny) * 100 if price_cny > 0 else 0
        combined = {"avg_profit_cny": round(avg_profit, 2), "avg_profit_rate": round(avg_rate, 2)}

    return jsonify({
        "exchange_rate": exchange_rate,
        "volume_m3": round(volume_m3, 6),
        "head_freight": round(head_freight, 2),
        "pretax_cost": round(pretax_cost, 2),
        "platform_fee": round(platform_fee, 2),
        "other_fee": round(other_fee, 2),
        "ship_fee_mode": ship_fee_mode,
        "fixed_postcode": fixed_postcode if platform != "amazon" else "08016 + 91710",
        "combined_profit": combined,
        "warehouses": wh_results,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# 用户认证 API
# ═══════════════════════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    """密码哈希"""
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()


def generate_token() -> str:
    """生成随机 token"""
    import secrets
    return secrets.token_hex(32)


def verify_token(token: str) -> dict:
    """验证 token，返回用户信息或 None"""
    try:
        conn = sqlite3.connect(USERS_DB)
        row = conn.execute('''
            SELECT u.id, u.username, u.role, u.is_active, t.expires_at
            FROM user_tokens t
            JOIN users u ON t.user_id = u.id
            WHERE t.token = ? AND t.expires_at > datetime('now') AND u.is_active = 1
        ''', (token,)).fetchone()
        conn.close()
        if row:
            return {"id": row[0], "username": row[1], "role": row[2], "is_active": row[3]}
    except Exception as e:
        print(f"[Auth] Token验证失败: {e}")
    return None


def require_auth(f):
    """登录验证装饰器"""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({"error": "未登录", "code": "NOT_LOGIN"}), 401
        user = verify_token(token)
        if not user:
            return jsonify({"error": "登录已过期", "code": "TOKEN_EXPIRED"}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """管理员验证装饰器"""
    from functools import wraps
    @wraps(f)
    @require_auth
    def decorated(*args, **kwargs):
        if request.current_user.get('role') != 'admin':
            return jsonify({"error": "需要管理员权限", "code": "ADMIN_REQUIRED"}), 403
        return f(*args, **kwargs)
    return decorated


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    """用户登录"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return jsonify({"success": False, "error": "用户名和密码不能为空"}), 400

        conn = sqlite3.connect(USERS_DB)
        user = conn.execute(
            'SELECT id, username, password_hash, role, is_active FROM users WHERE username = ?',
            (username,)
        ).fetchone()
        conn.close()

        if not user:
            return jsonify({"success": False, "error": "用户名或密码错误"}), 401

        user_id, db_username, db_hash, role, is_active = user

        if is_active != 1:
            return jsonify({"success": False, "error": "账号已被禁用"}), 401

        if hash_password(password) != db_hash:
            return jsonify({"success": False, "error": "用户名或密码错误"}), 401

        # 生成 token（有效期7天）
        token = generate_token()
        expires_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + 7 * 86400))

        conn = sqlite3.connect(USERS_DB)
        # 删除旧 token
        conn.execute('DELETE FROM user_tokens WHERE user_id = ?', (user_id,))
        # 插入新 token
        conn.execute(
            'INSERT INTO user_tokens (user_id, token, expires_at) VALUES (?, ?, ?)',
            (user_id, token, expires_at)
        )
        # 更新最后登录时间
        conn.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()

        print(f"[Auth] 用户登录: {username} (role={role})")

        return jsonify({
            "success": True,
            "token": token,
            "user": {
                "id": user_id,
                "username": db_username,
                "role": role,
            }
        })

    except Exception as e:
        import traceback
        print(f"[Auth] 登录异常: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/auth/logout', methods=['POST'])
@require_auth
def auth_logout():
    """用户登出"""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        conn = sqlite3.connect(USERS_DB)
        conn.execute('DELETE FROM user_tokens WHERE token = ?', (token,))
        conn.commit()
        conn.close()
        print(f"[Auth] 用户登出: {request.current_user['username']}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/auth/check', methods=['GET'])
def auth_check():
    """检查登录状态"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        return jsonify({"logged_in": False})
    user = verify_token(token)
    if user:
        return jsonify({"logged_in": True, "user": user})
    return jsonify({"logged_in": False})


@app.route('/api/auth/register', methods=['POST'])
@require_admin
def auth_register():
    """注册新用户（仅管理员）"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '')
        role = data.get('role', 'user')

        if not username or not password:
            return jsonify({"success": False, "error": "用户名和密码不能为空"}), 400

        if role not in ('user', 'admin'):
            return jsonify({"success": False, "error": "无效的角色"}), 400

        conn = sqlite3.connect(USERS_DB)
        # 检查用户名是否已存在
        existing = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if existing:
            conn.close()
            return jsonify({"success": False, "error": "用户名已存在"}), 400

        # 创建用户
        password_hash = hash_password(password)
        conn.execute(
            'INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
            (username, password_hash, role)
        )
        conn.commit()
        user_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()

        print(f"[Auth] 管理员创建用户: {username} (role={role})")

        return jsonify({
            "success": True,
            "user": {"id": user_id, "username": username, "role": role}
        })

    except Exception as e:
        import traceback
        print(f"[Auth] 注册异常: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/auth/users', methods=['GET'])
@require_admin
def auth_list_users():
    """获取所有用户列表（仅管理员）"""
    try:
        conn = sqlite3.connect(USERS_DB)
        users = conn.execute('''
            SELECT id, username, role, created_at, last_login, is_active
            FROM users ORDER BY id
        ''').fetchall()
        conn.close()

        user_list = []
        for u in users:
            user_list.append({
                "id": u[0],
                "username": u[1],
                "role": u[2],
                "created_at": u[3],
                "last_login": u[4],
                "is_active": u[5] == 1,
            })

        return jsonify({"success": True, "users": user_list})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/auth/users/<int:user_id>', methods=['PUT'])
@require_admin
def auth_update_user(user_id):
    """更新用户信息（仅管理员）"""
    try:
        data = request.get_json()
        new_password = data.get('password')
        new_role = data.get('role')
        is_active = data.get('is_active')

        conn = sqlite3.connect(USERS_DB)

        # 检查用户是否存在
        existing = conn.execute('SELECT id, username, role FROM users WHERE id = ?', (user_id,)).fetchone()
        if not existing:
            conn.close()
            return jsonify({"success": False, "error": "用户不存在"}), 404

        # 不允许修改管理员自己的角色
        if user_id == request.current_user['id'] and new_role and new_role != 'admin':
            conn.close()
            return jsonify({"success": False, "error": "不能修改自己的管理员角色"}), 400

        updates = []
        params = []

        if new_password:
            updates.append("password_hash = ?")
            params.append(hash_password(new_password))

        if new_role in ('user', 'admin'):
            updates.append("role = ?")
            params.append(new_role)

        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)

        if updates:
            params.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

        conn.close()

        print(f"[Auth] 管理员更新用户 {user_id}: role={new_role}, is_active={is_active}")

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/auth/users/<int:user_id>', methods=['DELETE'])
@require_admin
def auth_delete_user(user_id):
    """删除用户（仅管理员）"""
    try:
        conn = sqlite3.connect(USERS_DB)

        # 检查用户是否存在
        existing = conn.execute('SELECT id, username, role FROM users WHERE id = ?', (user_id,)).fetchone()
        if not existing:
            conn.close()
            return jsonify({"success": False, "error": "用户不存在"}), 404

        # 不允许删除自己
        if user_id == request.current_user['id']:
            conn.close()
            return jsonify({"success": False, "error": "不能删除自己"}), 400

        # 不允许删除最后一个管理员
        admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1").fetchone()[0]
        if existing[2] == 'admin' and admin_count <= 1:
            conn.close()
            return jsonify({"success": False, "error": "不能删除最后一个管理员"}), 400

        # 删除用户的 token
        conn.execute('DELETE FROM user_tokens WHERE user_id = ?', (user_id,))
        # 删除用户
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()

        print(f"[Auth] 管理员删除用户: {existing[1]} (id={user_id})")

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/auth/change-password', methods=['POST'])
@require_auth
def auth_change_password():
    """用户修改自己的密码"""
    try:
        data = request.get_json()
        old_password = data.get('old_password', '')
        new_password = data.get('new_password', '')

        if not old_password or not new_password:
            return jsonify({"success": False, "error": "旧密码和新密码都不能为空"}), 400

        if len(new_password) < 6:
            return jsonify({"success": False, "error": "新密码长度至少6位"}), 400

        conn = sqlite3.connect(USERS_DB)
        user = conn.execute(
            'SELECT id, password_hash FROM users WHERE id = ?',
            (request.current_user['id'],)
        ).fetchone()

        if not user or hash_password(old_password) != user[1]:
            conn.close()
            return jsonify({"success": False, "error": "旧密码错误"}), 401

        conn.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                     (hash_password(new_password), user[0]))
        # 删除所有旧 token，强制重新登录
        conn.execute('DELETE FROM user_tokens WHERE user_id = ?', (user[0],))
        conn.commit()
        conn.close()

        print(f"[Auth] 用户修改密码: {request.current_user['username']}")

        return jsonify({"success": True, "message": "密码已修改，请重新登录"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# 用户产品库 API
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/user-products', methods=['GET'])
@require_auth
def get_user_products():
    """获取当前用户的产品库"""
    try:
        user_id = request.current_user['id']
        conn = sqlite3.connect(USERS_DB)
        rows = conn.execute(
            'SELECT product_id, product_data, created_at, updated_at FROM user_products WHERE user_id = ? ORDER BY updated_at DESC',
            (user_id,)
        ).fetchall()
        conn.close()

        products = []
        for row in rows:
            try:
                products.append({
                    "id": row[0],
                    "data": json.loads(row[1]),
                    "created_at": row[2],
                    "updated_at": row[3],
                })
            except:
                pass

        return jsonify({"success": True, "products": products})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/user-products', methods=['POST'])
@require_auth
def save_user_products():
    """保存整个产品库（覆盖）"""
    try:
        user_id = request.current_user['id']
        data = request.get_json()
        products = data.get('products', [])

        conn = sqlite3.connect(USERS_DB)

        # 获取现有产品ID列表
        existing_ids = set(r[0] for r in conn.execute(
            'SELECT product_id FROM user_products WHERE user_id = ?', (user_id,)
        ).fetchall())

        # 新产品ID列表
        new_ids = set(p.get('id', '') for p in products if p.get('id'))

        # 删除不在新产品列表中的
        to_delete = existing_ids - new_ids
        if to_delete:
            placeholders = ','.join('?' * len(to_delete))
            conn.execute(f'DELETE FROM user_products WHERE user_id = ? AND product_id IN ({placeholders})',
                        [user_id] + list(to_delete))

        # 插入或更新产品
        for p in products:
            product_id = p.get('id', '')
            if not product_id:
                continue
            product_data = json.dumps(p.get('data', p), ensure_ascii=False, default=str)
            conn.execute('''
                INSERT INTO user_products (user_id, product_id, product_data, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, product_id) DO UPDATE SET
                product_data = excluded.product_data,
                updated_at = CURRENT_TIMESTAMP
            ''', (user_id, product_id, product_data))

        conn.commit()
        conn.close()

        return jsonify({"success": True, "count": len(products)})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/user-products/<product_id>', methods=['POST'])
@require_auth
def add_user_product(product_id):
    """添加单个产品到用户产品库"""
    try:
        user_id = request.current_user['id']
        product_data = request.get_json()

        conn = sqlite3.connect(USERS_DB)
        conn.execute('''
            INSERT INTO user_products (user_id, product_id, product_data, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, product_id) DO UPDATE SET
            product_data = excluded.product_data,
            updated_at = CURRENT_TIMESTAMP
        ''', (user_id, product_id, json.dumps(product_data, ensure_ascii=False, default=str)))
        conn.commit()
        conn.close()

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/user-products/<product_id>', methods=['DELETE'])
@require_auth
def delete_user_product(product_id):
    """从用户产品库删除单个产品"""
    try:
        user_id = request.current_user['id']

        conn = sqlite3.connect(USERS_DB)
        conn.execute('DELETE FROM user_products WHERE user_id = ? AND product_id = ?',
                    (user_id, product_id))
        conn.commit()
        conn.close()

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# 管理员可以查看所有用户的产品库
@app.route('/api/admin/all-products', methods=['GET'])
@require_admin
def get_all_user_products():
    """管理员获取所有用户的产品库汇总"""
    try:
        conn = sqlite3.connect(USERS_DB)
        rows = conn.execute('''
            SELECT u.username, up.product_id, up.product_data, up.created_at, up.updated_at
            FROM user_products up
            JOIN users u ON up.user_id = u.id
            ORDER BY up.updated_at DESC
        ''').fetchall()
        conn.close()

        result = {}
        for row in rows:
            username = row[0]
            if username not in result:
                result[username] = []
            try:
                result[username].append({
                    "id": row[1],
                    "data": json.loads(row[2]),
                    "created_at": row[3],
                    "updated_at": row[4],
                })
            except:
                pass

        return jsonify({"success": True, "users": result})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ===== 评论分析缓存 =====
def init_reviews_cache():
    conn = sqlite3.connect(EBAY_CACHE_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reviews_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            product_id TEXT NOT NULL,
            filter_type TEXT NOT NULL DEFAULT 'all',
            data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS review_analysis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            product_id TEXT NOT NULL,
            analysis TEXT NOT NULL,
            review_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_reviews_cache()


def get_reviews_cache(platform, product_id, filter_type='all'):
    """查询评论缓存，3天内有效"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        three_days_ago = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 3 * 86400))
        row = conn.execute(
            '''SELECT data FROM reviews_cache
               WHERE platform=? AND product_id=? AND filter_type=? AND created_at >= ?
               ORDER BY created_at DESC LIMIT 1''',
            (platform, product_id, filter_type, three_days_ago)
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception as e:
        print(f"[Reviews Cache] 查询失败: {e}")
    return None


def save_reviews_cache(platform, product_id, filter_type, data):
    """保存评论到缓存"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        conn.execute(
            '''INSERT INTO reviews_cache (platform, product_id, filter_type, data)
               VALUES (?, ?, ?, ?)''',
            (platform, product_id, filter_type, json.dumps(data, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Reviews Cache] 保存失败: {e}")


def get_analysis_cache(platform, product_id):
    """查询AI分析缓存，7天内有效"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        seven_days_ago = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 7 * 86400))
        row = conn.execute(
            '''SELECT analysis, review_count FROM review_analysis_cache
               WHERE platform=? AND product_id=? AND created_at >= ?
               ORDER BY created_at DESC LIMIT 1''',
            (platform, product_id, seven_days_ago)
        ).fetchone()
        conn.close()
        if row:
            return row[0], row[1]
    except Exception as e:
        print(f"[Analysis Cache] 查询失败: {e}")
    return None, None


def save_analysis_cache(platform, product_id, analysis, review_count):
    """保存AI分析到缓存"""
    try:
        conn = sqlite3.connect(EBAY_CACHE_DB)
        conn.execute(
            '''INSERT INTO review_analysis_cache (platform, product_id, analysis, review_count)
               VALUES (?, ?, ?, ?)''',
            (platform, product_id, analysis, review_count)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Analysis Cache] 保存失败: {e}")


def call_ai_analyze_reviews(reviews: list, product_title: str = '', platform: str = 'amazon') -> str:
    """调用AI API分析差评内容"""
    # 过滤差评（1-3星）
    bad_reviews = [r for r in reviews if r.get('rating', 0) <= 3 and r.get('rating', 0) > 0]
    # 如果没有带星级的评论，用全部评论
    if not bad_reviews:
        bad_reviews = reviews

    if not bad_reviews:
        return '暂无评论数据可供分析'

    # 构建评论摘要文本（最多500条，V4 Flash支持100万token上下文）
    review_texts = []
    for i, r in enumerate(bad_reviews[:500]):
        rating_str = f"★{r.get('rating', '?')}" if r.get('rating') else ''
        title_str = (r.get('title') or '').strip()
        body_str = (r.get('body') or '').strip()
        if body_str or title_str:
            # 每条评论保留最多500字，避免截断关键信息
            review_texts.append(f"{i+1}. {rating_str} 【{title_str}】\n{body_str[:500]}")

    if not review_texts:
        return '评论内容为空，无法分析'

    # 拼接时控制总长度，防止超出token限制（约保留8万字符，约2万token）
    reviews_content = ''
    for rt in review_texts:
        if len(reviews_content) + len(rt) > 80000:
            break
        reviews_content += rt + '\n\n'

    prompt = f"""你是一名资深的产品改进顾问，精通电商用户评论挖掘。请对以下{'Amazon' if platform=='amazon' else 'eBay'}商品的用户评论进行深度分析，重点挖掘可落地的产品改良点。

商品：{product_title or '未知商品'}
平台：{'Amazon' if platform == 'amazon' else 'eBay'}
有效评论数：{len(bad_reviews)}条（差评/低分评论）

评论内容：
{reviews_content}

请严格按以下结构输出（使用中文，每个改良点必须引用评论中的具体描述作为依据）：

## 1. 总体评价
用2-3句话概括用户对该产品的主要不满（引用1-2条典型评论摘要）

## 2. 可改良点清单（重点！）
按优先级排序，列出用户反馈中最值得改进的方向。**每条必须包含**：
- 🔧 改良方向名称
- 📊 反馈热度：多少条评论提到了这个问题（如"约35条提及"）
- 💬 典型用户原话（引用1-2条真实评论摘要，不要编造）
- ✅ 具体改良建议（给卖家/制造商的明确行动方案，要可落地，如"将XX材料更换为YY，厚度从1mm增加到2mm"）
- 💰 预估成本影响（低/中/高）

最多列8条，按反馈热度从高到低排序。

## 3. 用户最在意的TOP3痛点
用一句话总结用户最不能容忍的3个问题，作为产品迭代的优先级参考。

## 4. 竞品机会窗口
基于这些痛点，分析如果竞争对手解决了这些问题，会在哪些维度形成竞争优势（价格/质量/口碑/复购率）。

## 5. 快速改进 vs 长期改进
将改良点分为两类：
- 🚀 快速改进（1-4周内可完成，如包装、说明书、配件）
- 🏗️ 长期改进（需重新开模/换供应商，如材料、结构、核心功能）

请确保每条改良建议都有评论数据支撑，言之有物，不搞空洞结论。"""

    # 尝试调用多种AI API
    api_key = os.getenv('OPENAI_API_KEY') or os.getenv('openai_api_key')
    deepseek_key = os.getenv('DEEPSEEK_API_KEY') or os.getenv('deepseek_api_key')
    doubao_key = os.getenv('DOUBAO_API_KEY') or os.getenv('ARK_API_KEY') or os.getenv('ark_api_key')

    # 优先使用DeepSeek（成本低、中文好）
    if deepseek_key:
        try:
            import requests as _req
            resp = _req.post(
                'https://api.deepseek.com/chat/completions',
                headers={'Authorization': f'Bearer {deepseek_key}', 'Content-Type': 'application/json'},
                json={
                    'model': 'deepseek-v4-flash',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 4096,
                    'temperature': 0.3,
                },
                timeout=60
            )
            result = resp.json()
            return result['choices'][0]['message']['content']
        except Exception as e:
            print(f"[AI分析] DeepSeek调用失败: {e}")

    # 备选：豆包/火山方舟
    if doubao_key:
        try:
            import requests as _req
            model_id = os.getenv('DOUBAO_MODEL_ID', 'doubao-pro-32k')
            resp = _req.post(
                'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
                headers={'Authorization': f'Bearer {doubao_key}', 'Content-Type': 'application/json'},
                json={
                    'model': model_id,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 4096,
                    'temperature': 0.3,
                },
                timeout=60
            )
            result = resp.json()
            return result['choices'][0]['message']['content']
        except Exception as e:
            print(f"[AI分析] 豆包调用失败: {e}")

    # 备选：OpenAI
    if api_key:
        try:
            import requests as _req
            resp = _req.post(
                'https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={
                    'model': 'gpt-4o-mini',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 4096,
                    'temperature': 0.3,
                },
                timeout=60
            )
            result = resp.json()
            return result['choices'][0]['message']['content']
        except Exception as e:
            print(f"[AI分析] OpenAI调用失败: {e}")

    return '⚠️ AI分析服务未配置。请在.env中配置DEEPSEEK_API_KEY、OPENAI_API_KEY或DOUBAO_API_KEY之一。\n\n**差评主要内容摘要（前20条）：**\n' + '\n'.join(
        f"- ★{r.get('rating','?')} {r.get('title','')} {r.get('body','')[:100]}"
        for r in bad_reviews[:20]
    )


@app.route('/api/reviews/amazon', methods=['GET'])
def amazon_reviews():
    """
    抓取Amazon商品评论
    GET /api/reviews/amazon?asin=B09XXXX&pages=5&filter=critical&cdp_url=http://localhost:9222
    参数:
        - asin: 商品ASIN (必需)
        - pages: 最大页数, 默认5
        - filter: all/critical/one_star/two_star/three_star, 默认critical
        - cdp_url: CDP地址 (可选)
        - force: 1=强制刷新
    """
    asin = request.args.get('asin', '').strip().upper()
    if not asin or not re.fullmatch(r'[A-Z0-9]{10}', asin):
        return jsonify({'success': False, 'error': '请提供有效的ASIN（10位字母数字）'}), 400

    max_pages = int(request.args.get('pages', 5))
    star_filter = request.args.get('filter', 'critical')
    cdp_url = request.args.get('cdp_url', None)
    force = request.args.get('force', '0') == '1'

    # 检查缓存
    if not force:
        cached = get_reviews_cache('amazon', asin, star_filter)
        if cached:
            print(f"[Amazon评论API] 命中缓存: {asin}")
            cached['_from_cache'] = True
            return jsonify(cached)

    print(f"[Amazon评论API] 开始抓取: ASIN={asin}, 页数={max_pages}, 筛选={star_filter}")

    try:
        from amazon.amazon_reviews import scrape_amazon_reviews
        result = scrape_amazon_reviews(asin, max_pages=max_pages, star_filter=star_filter, cdp_url=cdp_url)

        if result.get('success') and result.get('reviews'):
            save_reviews_cache('amazon', asin, star_filter, result)

        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'asin': asin, 'error': str(e)[:300], 'reviews': []}), 500


@app.route('/api/reviews/ebay', methods=['GET'])
def ebay_reviews_api():
    """
    抓取eBay商品评论/评价
    GET /api/reviews/ebay?item_id=123456789&pages=3&cdp_url=http://localhost:9222
    参数:
        - item_id: eBay商品ID (必需)
        - pages: 最大页数, 默认3
        - cdp_url: CDP地址 (可选)
        - force: 1=强制刷新
    """
    item_id = request.args.get('item_id', '').strip()
    if not item_id or not re.fullmatch(r'\d{6,15}', item_id):
        return jsonify({'success': False, 'error': '请提供有效的eBay商品ID（纯数字）'}), 400

    max_pages = int(request.args.get('pages', 3))
    cdp_url = request.args.get('cdp_url', None)
    force = request.args.get('force', '0') == '1'

    # 检查缓存
    if not force:
        cached = get_reviews_cache('ebay', item_id, 'all')
        if cached:
            print(f"[eBay评论API] 命中缓存: {item_id}")
            cached['_from_cache'] = True
            return jsonify(cached)

    print(f"[eBay评论API] 开始抓取: item_id={item_id}, 页数={max_pages}")

    try:
        from ebay.ebay_reviews import scrape_ebay_reviews
        result = scrape_ebay_reviews(item_id, max_pages=max_pages, cdp_url=cdp_url)

        if result.get('success'):
            save_reviews_cache('ebay', item_id, 'all', result)

        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'item_id': item_id, 'error': str(e)[:300], 'reviews': []}), 500


@app.route('/api/reviews/analyze', methods=['POST'])
def analyze_reviews():
    """
    对评论进行AI分析
    POST JSON body:
    {
        "platform": "amazon" | "ebay",
        "product_id": "ASIN或item_id",
        "product_title": "商品标题（可选）",
        "reviews": [...评论数组，可选，不传则从缓存读取],
        "force": false
    }
    """
    data = request.get_json(force=True) or {}
    platform = data.get('platform', 'amazon')
    product_id = data.get('product_id', '').strip()
    product_title = data.get('product_title', '')
    reviews = data.get('reviews', [])
    force = data.get('force', False)

    if not product_id:
        return jsonify({'success': False, 'error': '请提供product_id'}), 400

    # 检查分析缓存
    if not force:
        cached_analysis, cached_count = get_analysis_cache(platform, product_id)
        if cached_analysis:
            return jsonify({
                'success': True,
                'analysis': cached_analysis,
                'review_count': cached_count,
                '_from_cache': True,
            })

    # 如果没有传评论，从评论缓存读取
    if not reviews:
        cached_reviews = get_reviews_cache(platform, product_id, 'critical' if platform == 'amazon' else 'all')
        if not cached_reviews:
            cached_reviews = get_reviews_cache(platform, product_id, 'all')
        if cached_reviews:
            reviews = cached_reviews.get('reviews', [])

    if not reviews:
        return jsonify({
            'success': False,
            'error': '没有评论数据，请先抓取评论再进行分析',
        }), 400

    print(f"[AI分析] {platform} {product_id}: {len(reviews)}条评论")

    try:
        analysis = call_ai_analyze_reviews(reviews, product_title=product_title, platform=platform)
        bad_count = len([r for r in reviews if r.get('rating', 0) <= 3 and r.get('rating', 0) > 0]) or len(reviews)
        save_analysis_cache(platform, product_id, analysis, bad_count)
        return jsonify({
            'success': True,
            'analysis': analysis,
            'review_count': bad_count,
            '_from_cache': False,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)[:300]}), 500


@app.route('/api/inspiration', methods=['GET'])
def get_inspiration():
    """读取每日 TikTok 推荐产品数据 (product.txt)
    
    支持格式:
    1. JSON 数组: [{"title":"...", "url":"..."}, ...]
    2. JSON 对象: {"products": [...]}
    3. Markdown 报告: ## 类目标题 + ### 1. 产品名 (英文名)：描述 +  bullet points
    4. 纯文本: 每行一个产品
    """
    import json as _json
    # 使用项目根目录作为基准，避免 __file__ 解析问题
    product_file = r'D:\smartproduct\product.txt'

    # 检查文件是否存在
    if not os.path.exists(product_file):
        return jsonify({
            'success': False,
            'error': '今日暂无推荐数据',
            'detail': f'未找到文件: {product_file}',
            'products': [],
            'count': 0,
            'updated_at': None
        }), 404

    try:
        # 获取文件修改时间
        mtime = os.path.getmtime(product_file)
        updated_at = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')

        with open(product_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        if not content:
            return jsonify({
                'success': True,
                'products': [],
                'count': 0,
                'updated_at': updated_at,
                'message': '文件为空，暂无推荐产品'
            })

        # 尝试解析 JSON
        try:
            data = _json.loads(content)
            # 支持两种格式: 直接数组 或 {products: [...]} 对象
            if isinstance(data, list):
                products = data
            elif isinstance(data, dict) and 'products' in data:
                products = data['products']
            else:
                products = [data]
            # JSON 格式走标准化流程
            normalized = []
            for p in products:
                if isinstance(p, str):
                    p = {'title': p}
                item = {
                    'title': str(p.get('title', p.get('name', '未命名产品'))).strip(),
                    'url': p.get('url', p.get('link', p.get('productUrl', ''))),
                    'image': p.get('image', p.get('img', p.get('productImg', ''))),
                    'price': p.get('price', ''),
                    'platform': p.get('platform', ''),
                    'category': p.get('category', p.get('cateName', '')),
                    'note': p.get('note', p.get('desc', p.get('description', ''))),
                    'raw': p
                }
                if item['title']:
                    normalized.append(item)
            return jsonify({
                'success': True,
                'products': normalized,
                'count': len(normalized),
                'updated_at': updated_at,
                'file_path': product_file
            })
        except _json.JSONDecodeError:
            pass  # 不是 JSON，继续尝试 Markdown 解析

        # ===== Markdown 报告解析 =====
        # 匹配模式: ### 1. 产品中文名 (English Name)：描述
        product_pattern = re.compile(
            r'^###\s*\d+\.\s*([^\n(]+)(?:\s*\(([^)]+)\))?\s*[:：]\s*(.+?)$',
            re.MULTILINE
        )
        
        # 类目标题模式: ## 一、类目名 (Category)
        category_pattern = re.compile(
            r'^##\s+[^、]*、\s*(.+?)(?:\s*\(([^)]+)\))?\s*$',
            re.MULTILINE
        )

        products = []
        lines = content.split('\n')
        current_category = ''
        current_category_en = ''
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # 检测类目标题
            cat_match = category_pattern.match(line)
            if cat_match:
                current_category = cat_match.group(1).strip()
                current_category_en = cat_match.group(2).strip() if cat_match.group(2) else ''
                i += 1
                continue
            
            # 检测产品标题
            prod_match = product_pattern.match(line)
            if prod_match:
                title_cn = prod_match.group(1).strip()
                title_en = prod_match.group(2).strip() if prod_match.group(2) else ''
                desc = prod_match.group(3).strip()
                
                # 组合标题
                title = title_cn
                if title_en:
                    title = f"{title_cn} ({title_en})"
                if desc and desc not in title:
                    title = f"{title}：{desc}"
                
                # 收集后续 bullet points（爆款潜力、创意脚本等）
                note_lines = []
                i += 1
                while i < len(lines):
                    next_line = lines[i].strip()
                    # 遇到下一个产品标题或类目标题或分隔线就停止
                    if (product_pattern.match(next_line) or 
                        category_pattern.match(next_line) or
                        next_line.startswith('---') or
                        next_line.startswith('## ')):
                        break
                    # 收集 bullet points（以 * 或 - 开头）
                    if next_line.startswith('*') or next_line.startswith('-'):
                        # 清理 markdown 格式
                        clean = re.sub(r'^\s*[*-]\s*', '', next_line)
                        clean = re.sub(r'\*\*(.+?)\*\*', r'\1', clean)  # 去掉加粗
                        note_lines.append(clean)
                    i += 1
                
                note = '\n'.join(note_lines) if note_lines else ''
                
                products.append({
                    'title': title,
                    'url': '',
                    'image': '',
                    'price': '',
                    'platform': 'TikTok',
                    'category': current_category,
                    'note': note,
                    'raw': {'title_cn': title_cn, 'title_en': title_en, 'desc': desc}
                })
                continue
            
            i += 1

        # 如果没解析到任何产品，回退到纯文本行解析
        if not products:
            for line in content.split('\n'):
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('---'):
                    continue
                urls = re.findall(r'https?://[^\s<>"{}|\\^`[\]]+', line)
                product = {'title': line, 'url': '', 'image': '', 'price': '', 'platform': '', 'category': '', 'note': '', 'raw': line}
                if urls:
                    product['url'] = urls[0]
                    product['title'] = line.replace(urls[0], '').strip(' -|')
                if product['title']:
                    products.append(product)

        return jsonify({
            'success': True,
            'products': products,
            'count': len(products),
            'updated_at': updated_at,
            'file_path': product_file
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)[:300],
            'products': [],
            'count': 0
        }), 500


if __name__ == '__main__':
    print("[OK] SmartSeller 服务启动: http://localhost:8004")
    app.run(host='0.0.0.0', port=8004, debug=False, use_reloader=False, threaded=True)
