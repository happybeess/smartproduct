"""
Product Hunt 热门产品数据源模块
============================
从 Product Hunt API 获取今日/本周热门产品数据，
供「美国热门趋势」面板展示。
"""

import json
import os
import ssl as _ssl
import time


# 从 .env 文件加载环境变量
from dotenv import load_dotenv
load_dotenv()


def fetch_product_hunt():
    """
    Product Hunt 今日/本周热门产品
    
    Returns:
        tuple: (list[dict], str|None) — 产品列表和错误信息
    """
    try:
        import urllib.request
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        url = 'https://api.producthunt.com/v2/api/graphql'
        query = '{"query":"{ posts(order: VOTES, first: 25) { edges { node { name tagline votesCount url website } } } }"}'
        req = urllib.request.Request(url, data=query.encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0',
                'Authorization': f'Bearer {os.getenv("PHONE_TOKEN", "")}',
            })
        with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        edges = data.get('data', {}).get('posts', {}).get('edges', [])
        if not edges:
            return [], 'Product Hunt API 无数据'
        results = []
        for e in edges:
            n = e['node']
            results.append({
                'title': n.get('name', ''),
                'desc': n.get('tagline', ''),
                'votes': n.get('votesCount', 0),
                'url': n.get('url', '') or n.get('website', ''),
            })
        return results, None
    except Exception as e:
        return [], f'获取失败: {str(e)[:80]}'


def render_ph_panel():
    """
    
    包含：缓存逻辑（30分钟）、错误处理、排名展示、刷新按钮。
    
    调用方式：
        from product_hunt_source import render_ph_panel
        render_ph_panel()
    """
    CACHE_KEY = "_gt_PH热门产品"
    EXPIRE_KEY = "_gt_ph_exp"
    ERR_KEY = "_gt_PH热门产品_err"
    
    # ── 缓存逻辑（30分钟）──
    if CACHE_KEY not in st.session_state or st.session_state.get(EXPIRE_KEY, 0) < time.time():
        with st.spinner("🔄 获取 Product Hunt 中..."):
            data, err = fetch_product_hunt()
        if err:
            st.session_state[CACHE_KEY] = []
            st.session_state[ERR_KEY] = err
        else:
            st.session_state[CACHE_KEY] = data
            st.session_state[ERR_KEY] = None
        st.session_state[EXPIRE_KEY] = time.time() + 1800
    
    items = st.session_state.get(CACHE_KEY, [])
    err_msg = st.session_state.get(ERR_KEY)
    
    if err_msg:
        st.sidebar.warning(f"⚠️ {err_msg}")
        if st.sidebar.button("🔄 重试", key="trend_retry_PH热门产品", use_container_width=True):
            st.session_state[EXPIRE_KEY] = 0
            st.rerun()
        return
    
    if not items:
        st.sidebar.caption("暂无数据，请刷新")
        return
    
    # ── 渲染列表 ──
    icon_map = {0: "🥇", 1: "🥈", 2: "🥉"}
    for idx, item in enumerate(items[:20]):
        title = item.get('title', '')
        if not title:
            continue
        icon = icon_map.get(idx, f"{idx+1}.")
        votes = item.get('votes', 0)
        url = item.get('url', '')
        extra = f"  ▲{votes:,}" if votes else ""
        desc = item.get('desc', '')
        label = f"{title[:35]}" + (f" — {desc[:20]}" if desc else "")
        display = f"{icon} [{label}]({url}){extra}" if url else f"{icon} {label}{extra}"
        st.sidebar.markdown(display)
    
    # ── 刷新按钮 ──
    st.sidebar.markdown("---")
    if st.sidebar.button("🔄 刷新", key="热门产品", use_container_width=True):
        st.session_state[EXPIRE_KEY] = 0
        st.rerun()


# ── 直接运行时测试 ──
if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    print("Testing Product Hunt API...")
    items, err = fetch_product_hunt()
    if err:
        print(f"Error: {err}")
    else:
        for i, item in enumerate(items[:10]):
            print(f"{i+1}. [{item['title']}] -- votes:{item['votes']}  url:{item['url']}")
