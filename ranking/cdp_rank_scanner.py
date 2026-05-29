# -*- coding: utf-8 -*-
"""
CDP 榜单扫描引擎
基于 SellerSprite 市场研究(Market Research)功能，通过 CDP 自动化采集细分市场数据。

参考: D:/seller -pro/cdp_sellersprite_full.py
改造点:
  - 命令行工具 → 可调用模块
  - 添加 progress/page 回调支持 SSE 流式推送
  - 清理编码，统一字段名为英文 key
"""

import json
import time
import websockets

# ── 默认配置 ──────────────────────────────────────────────
CDP_PORT = 9222
DEFAULT_MARKET = "US"
DEFAULT_NEW_MONTHS = 3
PAGE_TIMEOUT = 30  # 每页等待超时(秒)

# 支持的市场站点
MARKETS = ["US", "UK", "DE", "FR", "IT", "ES", "JP", "CA"]

# 可选类目（与 SellerSprite 市场研究一致）
CATEGORIES = [
    "Appliances",
    "Arts, Crafts & Sewing",
    "Automotive",
    "Baby Products",
    "Beauty & Personal Care",
    "Books",
    "Cell Phones & Accessories",
    "Clothing, Shoes & Jewelry",
    "Electronics",
    "Grocery & Gourmet Food",
    "Health & Household",
    "Home & Kitchen",
    "Hunting & Fishing",
    "Industrial & Scientific",
    "Lights, Bulbs & Indicators",
    "Musical Instruments",
    "Office Products",
    "Patio, Lawn & Garden",
    "Pet Supplies",
    "Power & Hand Tools",
    "Small Appliance Parts & Accessories",
    "Sports & Outdoors",
    "Tools & Home Improvement",
    "Toys & Games",
    "Video Games",
]

# 英文类目 → 中文显示名映射（与前端 RANK_CATEGORY_LABELS 对应）
CATEGORY_CN_MAP = {
    "Appliances": "家用电器",
    "Arts, Crafts & Sewing": "手工缝纫",
    "Automotive": "汽车配件",
    "Baby Products": "母婴用品",
    "Beauty & Personal Care": "美妆个护",
    "Books": "图书",
    "Cell Phones & Accessories": "手机配件",
    "Clothing, Shoes & Jewelry": "服饰珠宝",
    "Electronics": "电子产品",
    "Grocery & Gourmet Food": "食品杂货",
    "Health & Household": "健康家居",
    "Home & Kitchen": "厨房家居",
    "Hunting & Fishing": "狩猎钓鱼",
    "Industrial & Scientific": "工业科学",
    "Lights, Bulbs & Indicators": "灯具照明",
    "Musical Instruments": "乐器配件",
    "Office Products": "办公用品",
    "Patio, Lawn & Garden": "庭院园艺",
    "Pet Supplies": "宠物用品",
    "Power & Hand Tools": "电动工具",
    "Small Appliance Parts & Accessories": "小家电配件",
    "Sports & Outdoors": "运动户外",
    "Tools & Home Improvement": "五金工具",
    "Toys & Games": "玩具游戏",
    "Video Games": "游戏机",
}

# 反向映射：中文 → 英文（用于从弹窗中识别中文类目名）
CN_TO_CATEGORY = {v: k for k, v in CATEGORY_CN_MAP.items()}


def _resolve_category_aliases(category_name):
    """解析类目名称，返回所有可能的匹配候选（英文原名 + 中文 + 关键词）"""
    aliases = set()
    if not category_name:
        return aliases

    # 1. 原始输入（通常是英文）
    aliases.add(category_name)

    # 2. 通过英文→中文映射找中文名
    cn_name = CATEGORY_CN_MAP.get(category_name)
    if cn_name:
        aliases.add(cn_name)

    # 3. 拆分关键词：按逗号/空格/&/and 拆分
    for sep in [', ', ',', ' & ', '&', ' ', ' and ', ' And ']:
        if sep in category_name:
            for part in category_name.split(sep):
                part = part.strip()
                if part:
                    aliases.add(part)
                    # 单独的中文关键词也加进去
                    part_cn = CATEGORY_CN_MAP.get(part)
                    if part_cn:
                        aliases.add(part_cn)
                        # 再拆中文
                        for cn_char in part_cn:
                            pass  # 单字太短，不拆

    # 4. 去掉太短的候选（< 2字符）
    return {a for a in aliases if len(a) >= 2}


def _get_fuzzy_keywords(category_name):
    """从类目名中提取模糊匹配用的关键词

    用于在精确/包含匹配全部失败后的兜底策略。
    提取每个单词的前5个字符作为模糊关键词。
    """
    keywords = set()
    for part in category_name.replace(',', ' ').replace('&', ' ').replace('-', ' ').split():
        word = part.strip().lower()
        if len(word) >= 3:
            # 截取前5个字符作为模糊关键词
            keywords.add(word[:5])
    return list(keywords)


# ── CDP 工具函数 ───────────────────────────────────────────

async def _cdp_send(ws, method, params=None):
    """发送 CDP 命令并返回结果"""
    msg_id = int(time.time() * 1000) % 100000
    payload = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params
    await ws.send(json.dumps(payload))
    while True:
        raw = await ws.recv()
        resp = json.loads(raw)
        if resp.get("id") == msg_id:
            return resp


async def _cdp_eval(ws, expression):
    """在页面上下文中执行 JS 表达式"""
    result = await _cdp_send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    result_data = result.get("result", {}).get("result", {})
    if result_data.get("type") == "undefined":
        return None
    return result_data.get("value")


async def _wait_for_selector(ws, selector, timeout=10):
    """等待选择器出现"""
    start = time.time()
    while time.time() - start < timeout:
        val = await _cdp_eval(
            ws, f"!!document.querySelector('{selector}')"
        )
        if val:
            return True
        time.sleep(0.5)
    return False


async def _click(ws, selector):
    """点击元素"""
    await _cdp_eval(ws, f"""
        const el = document.querySelector('{selector}');
        if(el){{ el.click(); 'clicked' }} else {{ 'not_found' }}
    """)


async def _set_select_value(ws, selector, value):
    """设置 select/下拉框的值（触发 react/vue 变更）"""
    await _cdp_eval(ws, f"""
        const sel = document.querySelector('{selector}');
        if(sel) {{
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLSelectElement.prototype, 'value'
            ).set;
            nativeInputValueSetter.call(sel, '{value}');
            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
            sel.dispatchEvent(new Event('input', {{ bubbles: true }}));
            'set';
        }} else {{ 'not_found' }}
    """)


async def _wait_loading(ws, timeout=15):
    """等待页面加载完成（检测 spinner/loading 状态）"""
    start = time.time()
    while time.time() - start < timeout:
        # 检查是否有 loading/spinner 元素可见
        loading = await _cdp_eval(ws, """
            (function(){
                const loaders = document.querySelectorAll('[class*="loading"], [class*="spinner"], [class*="Loading"], .ant-spin, .el-loading-mask');
                for(const el of loaders){
                    if(el.offsetParent !== null) return true;
                }
                return false;
            })()
        """)
        if not loading:
            # 再等一下确保数据渲染完成
            time.sleep(1)
            return True
        time.sleep(0.5)
    return False


# ── 核心扫描流程 ─────────────────────────────────────────

async def connect(cdp_port=None):
    """连接 Chrome DevTools WebSocket（直接连接到 SellerSprite 页面 tab）

    严格筛选：只选择 sellersprite.com 域名的 tab。
    如果没有找到 SellerSprite tab，抛出明确错误提示用户先打开 SellerSprite。
    """
    port = cdp_port or CDP_PORT
    import urllib.request

    # 获取所有打开的页面 tabs
    try:
        http_url = f"http://127.0.0.1:{port}/json"
        resp = urllib.request.urlopen(http_url, timeout=5)
        tabs = json.loads(resp.read().decode())
    except Exception as e:
        raise Exception(f"无法获取 Chrome tabs 列表 (端口 {port}): {e}")

    if not tabs:
        raise Exception(f"Chrome 没有打开任何页面 (端口 {port})")

    # 列出所有 tab URL 便于调试
    all_urls = [t.get("url", "") for t in tabs]

    # 严格筛选：只找 sellersprite.com 的 tab
    preferred = None
    fallback = None
    for t in tabs:
        url = (t.get("url") or "").lower()
        if "sellersprite" in url and "market-research" in url:
            preferred = t
            break
        if "sellersprite" in url and not fallback:
            fallback = t

    if not preferred and not fallback:
        # 没有 SellerSprite tab — 报错让用户先打开
        raise Exception(
            "未找到 SellerSprite 页面! "
            "请在 Chrome 中先登录并打开 https://www.sellersprite.com/v2/market-research ，"
            f"当前打开的页面: {[u[:80] for u in all_urls]}"
        )

    target = preferred or fallback
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise Exception("目标页面没有 webSocketDebuggerUrl: " + target.get("url", "unknown"))

    ws = await websockets.connect(ws_url, max_size=50 * 1024 * 1024)
    return ws


async def ensure_market_research_page(ws, market=DEFAULT_MARKET):
    """导航到 SellerSprite 市场研究页面并等待关键元素加载

    精确检测 SellerSprite 页面特征元素:
    - a.category-dropdown[data-q="nav"]  导航模式切换
    - #btn-nav-node-dialog            类目弹窗按钮
    - button.station-button[data-market]  站点选择按钮

    注意: 只有在页面明显不对时才重新导航，避免覆盖用户已选的类目
    """
    import asyncio

    await _cdp_send(ws, "Page.enable")

    info = await _cdp_eval(ws, """
        (function(){
            return JSON.stringify({
                href: location.href,
                title: document.title,
                hasNavMode: !!document.querySelector('a.category-dropdown[data-q="nav"]'),
                hasDialogButton: !!document.querySelector('#btn-nav-node-dialog'),
                hasMarketButton: !!document.querySelector('button.station-button')
            });
        })()
        """)

    # 解析信息
    if isinstance(info, str):
        import json
        try:
            info = json.loads(info)
        except Exception:
            info = {}
    elif not isinstance(info, dict):
        info = {}

    href = info.get("href", "")

    # ⚠️ 检测是否被重定向到登录页
    if "sellersprite" in href and ("login" in href.lower() or "auth" in href.lower() or "signin" in href.lower()):
        raise Exception(
            "SellerSprite 需要登录! "
            f"当前页面: {href[:120]}。"
            "请在 Chrome 中手动完成登录后重试。"
        )

    # 放宽导航条件：只有在不在 sellersprite 域名或核心元素缺失时才导航
    # 不再因为 URL 中没有 market-research 就导航（URL 可能在选类目后变化）
    should_navigate = (
        "sellersprite.com" not in (href or "")
        or not info.get("hasNavMode")
        or not info.get("hasDialogButton")
    )

    if should_navigate:
        target_url = "https://www.sellersprite.com/v2/market-research"

        # 先用 JS 检查是否会重定向（避免 CDP Page.navigate 直接报错）
        redirect_check = await _cdp_eval(ws, "(function(){ return location.href; })()")
        if isinstance(redirect_check, str) and "login" in redirect_check.lower():
            raise Exception("SellerSprite 未登录，无法访问市场研究页面。请先在 Chrome 中登录。")

        navigate_result = await _cdp_send(ws, "Page.navigate", {"url": target_url})

        # 检查导航结果
        if navigate_result:
            err_text = (navigate_result.get("result") or {}).get("errorText")
            if err_text:
                if "redirect" in err_text.lower() or "ERR_TOO_MANY" in err_text:
                    raise Exception(
                        f"SellerSprite 页面出现无限重定向 ({err_text})。"
                        "可能原因: 1)未登录 2)账号权限不足 3)网络问题。"
                        "请检查 Chrome 中 SellerSprite 是否正常登录并可访问市场研究页面。"
                    )
                raise Exception("Page.navigate 失败: " + err_text)

    # 等待关键元素出现（同时检测是否被重定向到登录页）
    for attempt in range(20):
        await asyncio.sleep(1)
        state = await _cdp_eval(ws, "(function(){" + \
            "return JSON.stringify({" + \
                "href: location.href," + \
                "title: document.title," + \
                "hasNavMode: !!document.querySelector('a.category-dropdown[data-q=\"nav\"]')," + \
                "hasDialogButton: !!document.querySelector('#btn-nav-node-dialog')," + \
                "hasMarketButton: !!document.querySelector('button.station-button')" + \
            "});" + \
        "})()")
        if isinstance(state, str):
            import json
            try:
                state = json.loads(state)
            except Exception:
                state = {}

        current_href = state.get("href", "")

        # 等待过程中检测被重定向到登录页
        if "sellersprite" in current_href and ("login" in current_href.lower() or "auth" in current_href.lower()):
            raise Exception(
                "导航后被重定向到登录页! SellerSprite 可能需要重新登录。"
                f"当前 URL: {current_href[:120]}"
            )

        if (state.get("hasNavMode") and state.get("hasDialogButton")
                and state.get("hasMarketButton")):
            return True

    raise RuntimeError(
        f"SellerSprite 市场研究页面未完成加载 (尝试了20次)。当前状态: {state}"
    )


async def set_params(ws, market=DEFAULT_MARKET, month_range=None, new_months=DEFAULT_NEW_MONTHS):
    """设置扫描参数: 市场站点、时间范围、新品月数

    SellerSprite 专用选择器:
    - button.station-button[data-market="US"]  站点按钮组
    - button.monthName-button[data-month-id]   时间范围按钮
    - select[name=newReleaseNumSelect]          新品月数下拉

    注意: 不再清空 nodeIdPath，由类目选择流程统一管理
    """
    import asyncio

    safe_market = market.replace("\\", "\\\\").replace("'", "\\'")
    safe_month_range = (month_range or "").replace("\\", "\\\\").replace("'", "\\'")
    safe_new_months = str(new_months).replace("'", "\\")

    result = await _cdp_eval(ws, "(function(){" + \
        "function triggerChange(el) {" + \
        "    if (!el) return;" + \
        "    el.dispatchEvent(new Event('change', { bubbles: true }));" + \
        "    el.dispatchEvent(new Event('input', { bubbles: true }));" + \
        "}" + \
        "" + \
        "// 1. 选择市场/站点" + \
        "var mBtn = document.querySelector('button.station-button[data-market=\"" + safe_market + "\"]');" + \
        "if (mBtn && !mBtn.classList.contains('active')) mBtn.click();" + \
        "" + \
        "// 2. 选择时间范围（最近N个月）" + \
        "var mrBtn = null;" + \
        "if ('" + safe_month_range + "') {" + \
        "    mrBtn = document.querySelector('button.monthName-button[data-month-id=\"" + safe_month_range + "\"]');" + \
        "    if (mrBtn && !mrBtn.classList.contains('active')) mrBtn.click();" + \
        "}" + \
        "" + \
        "// 3. 设置新品月数" + \
        "var monthSelect = document.querySelector('select[name=newReleaseNumSelect]');" + \
        "if (monthSelect) {" + \
        "    monthSelect.value = '" + safe_new_months + "';" + \
        "    triggerChange(monthSelect);" + \
        "}" + \
        "" + \
        "// 注意: 不再清空 nodeIdPath，由后续类目选择流程管理" + \
        "" + \
        "var newMonthsInput = document.querySelector('input[name=newReleaseNum]');" + \
        "return JSON.stringify({" + \
        "    market: mBtn ? (mBtn.textContent||'').trim() : 'not found'," + \
        "    monthRange: mrBtn ? (mrBtn.textContent||'').trim() : 'not_set'," + \
        "    newMonths: newMonthsInput ? (newMonthsInput.value || 'unknown') : 'unknown'," + \
        "    currentNodeId: (document.querySelector('input[name=nodeIdPath]') || {}).value || ''" + \
        "});" + \
        "})()")
    return result


async def input_search_keyword(ws, keyword):
    """在 SellerSprite 搜索模式中输入关键词

    SellerSprite 市场研究页面默认就是搜索模式，
    关键词输入框: input[name="departmentKeyword"]
    输入后点击筛选市场按钮提交
    """
    import asyncio
    try:
        safe_kw = keyword.replace("\\", "\\\\").replace("'", "\\'")
        result = await _cdp_eval(ws, (
            "(function(){"
            "var kwInput = document.querySelector('input[name=\"departmentKeyword\"]');"
            "if (!kwInput) {"
            "  kwInput = document.querySelector('input[placeholder*=\"关键词\"]') "
            "    || document.querySelector('input[placeholder*=\"搜索\"]') "
            "    || document.querySelector('.search-input input') "
            "    || document.querySelector('#departmentKeyword');"
            "}"
            "if (!kwInput) return JSON.stringify({error:'keyword_input_not_found'});"
            "kwInput.focus();"
            "kwInput.value = '';"
            "kwInput.value = '" + safe_kw + "';"
            "kwInput.dispatchEvent(new Event('input', {bubbles:true}));"
            "kwInput.dispatchEvent(new Event('change', {bubbles:true}));"
            "var nativeInputValueSetter = Object.getOwnPropertyDescriptor("
            "  window.HTMLInputElement.prototype, 'value').set;"
            "nativeInputValueSetter.call(kwInput, '" + safe_kw + "');"
            "kwInput.dispatchEvent(new Event('input', {bubbles:true}));"
            "kwInput.dispatchEvent(new Event('change', {bubbles:true}));"
            "return JSON.stringify({success:true, value:kwInput.value});"
            "})()"
        ))
        await asyncio.sleep(0.5)
        return result
    except Exception as e:
        return f"input_keyword_error: {e}"


async def fetch_keyword_suggestions(keyword, cdp_port=None):
    """在 SellerSprite 搜索框输入关键词，获取自动补全下拉列表

    SellerSprite 的 input[name=departmentKeyword] 输入后会弹出
    自动补全下拉框（如截图所示：显示类目路径 + 数量）。
    此函数：
      1. 连接 CDP
      2. 检测并切换到搜索模式（如当前在类目导航模式）
      3. 聚焦搜索框并填入关键词
      4. 触发 input 事件让 React/Vue 状态更新
      5. 等待自动补全下拉出现（最多 5 秒）
      6. 提取下拉列表所有选项的文本和数量（分离类目路径和数量）
      7. 返回 JSON 数组

    Args:
        keyword: 搜索关键词
        cdp_port: CDP 端口（默认 9222）

    Returns:
        str: JSON 字符串
    """
    import asyncio
    import json as _json
    try:
        # 连接 CDP
        ws = await connect(cdp_port)

        safe_kw = keyword.replace("\\", "\\\\").replace("'", "\\'")

        # ============================================================
        # Step 0: 全局 DOM 诊断 + 模式检测
        # ============================================================
        mode_check = await _cdp_eval(ws, (
            "(function(){"
            "var info = {};"
            "/* 1. 当前 URL */"
            "info.current_url = location.href.substring(0, 150);"
            "/* 2. 所有可见 input */"
            "var allInputs = document.querySelectorAll('input');"
            "info.all_inputs = [];"
            "for (var ii = 0; ii < allInputs.length; ii++) {"
            "  var inp = allInputs[ii];"
            "  if (inp.offsetParent === null && inp.offsetWidth === 0) continue;"
            "  info.all_inputs.push({"
            "    name: inp.name || '', id: inp.id || '',"
            "    type: inp.type || '', placeholder: inp.placeholder || '',"
            "    className: (inp.className || '').substring(0, 80),"
            "    value: (inp.value || '').substring(0, 40)"
            "  });"
            "}"
            "/* 3. Ant Design Select 组件数量 */"
            "info.ant_select_count = document.querySelectorAll('.ant-select').length;"
            "info.ant_select_dropdowns = document.querySelectorAll('.ant-select-dropdown').length;"
            "/* 4. 已存在的可见下拉/弹出层 */"
            "var visibleDropdowns = [];"
            "document.querySelectorAll('[class*=\"dropdown\"]').forEach(function(d){"
            "  if (d.offsetParent !== null) visibleDropdowns.push({tag:d.tagName, cls:(d.className||'').substring(0,60), children:d.children.length});"
            "});"
            "info.visible_dropdowns = visibleDropdowns;"
            "/* 5. 导航区域 */"
            "var navBtn = document.querySelector('a.category-dropdown[data-q=\"nav\"]');"
            "info.nav_btn_found = !!navBtn;"
            "info.nav_btn_text = navBtn ? (navBtn.textContent || '').trim().substring(0, 50) : '';"
            "/* 6. 搜索框（name 选择器） */"
            "var kwInput = document.querySelector('input[name=\"departmentKeyword\"]');"
            "info.search_input_by_name = !!kwInput;"
            "info.search_input_visible = kwInput ? (kwInput.offsetParent !== null) : false;"
            "return JSON.stringify(info);"
            "})()"
        ))
        if isinstance(mode_check, str):
            try:
                mode_info = _json.loads(mode_check)
            except Exception:
                mode_info = {}
        else:
            mode_info = {}

        # 如果搜索框不可见，尝试点击导航按钮切换到搜索模式
        if not mode_info.get('search_input_visible') and mode_info.get('nav_btn_found'):
            await _cdp_eval(ws, (
                "(function(){"
                "var navBtn = document.querySelector('a.category-dropdown[data-q=\"nav\"]');"
                "if (navBtn) { navBtn.click(); return 'clicked_nav'; }"
                "return 'nav_not_found';"
                "})()"
            ))
            await asyncio.sleep(1.0)  # 等 UI 切换动画完成

        # ============================================================
        # Step 1: 多策略查找输入框 + 填入关键词 + 完整 React 事件链
        # ============================================================
        fill_result = await _cdp_eval(ws, (
            "(function(){"
            "var kwInput = null;"
            "var strategy = '';"

            "/* 策略 A: name 选择器 */"
            "kwInput = document.querySelector('input[name=\"departmentKeyword\"]');"
            "if (kwInput) strategy = 'name_selector';"

            "/* 策略 B: placeholder 匹配 */"
            "if (!kwInput) {"
            "  var phSelectors = ["
            "    'input[placeholder*=\"类目\"]',"
            "    'input[placeholder*=\"关键词\"]',"
            "    'input[placeholder*=\"搜索\"]',"
            "    'input[placeholder*=\"Select\"]',"
            "    'input[placeholder*=\"select\"]'"
            "  ];"
            "  for (var pi = 0; pi < phSelectors.length; pi++) {"
            "    kwInput = document.querySelector(phSelectors[pi]);"
            "    if (kwInput) { strategy = 'placeholder_' + pi; break; }"
            "  }"
            "}"

            "/* 策略 C: Ant Design Select 内部 input */"
            "if (!kwInput) {"
            "  var antSelectors = ["
            "    '.ant-select input',"
            "    '.ant-select-search__field',"
            "    '.ant-select input[type=\"text\"]',"
            "    '.ant-select-selection-search-input'"
            "  ];"
            "  for (var ai = 0; ai < antSelectors.length; ai++) {"
            "    var els = document.querySelectorAll(antSelectors[ai]);"
            "    for (var aj = 0; aj < els.length; aj++) {"
            "      if (els[aj].offsetParent !== null || els[aj].offsetWidth > 0) {"
            "        kwInput = els[aj];"
            "        strategy = 'antd_' + ai + '_' + aj;"
            "        break;"
            "      }"
            "    }"
            "    if (kwInput) break;"
            "  }"
            "}"

            "/* 所有策略都失败 → 返回诊断信息 */"
            "if (!kwInput) {"
            "  var diag = [];"
            "  document.querySelectorAll('input').forEach(function(inp, idx) {"
            "    if (idx >= 20) return;"
            "    diag.push({n:inp.name||'', id:inp.id||'', ph:inp.placeholder||'', vis: inp.offsetParent!==null, cls:(inp.className||'').substring(0,50)});"
            "  });"
            "  return JSON.stringify({error:'no_input_found', strategy:'none', inputs:diag});"
            "}"

            "/* 找到输入框 — 使用 nativeSetter + 完整事件链触发 React 更新 */"
            "kwInput.focus();"
            "var nativeSetter = Object.getOwnPropertyDescriptor("
            "  window.HTMLInputElement.prototype, 'value').set;"
            "nativeSetter.call(kwInput, '" + safe_kw + "');"
            "/* React 需要这些事件才能检测到值变化 */"
            "kwInput.dispatchEvent(new Event('input', {bubbles:true}));"
            "kwInput.dispatchEvent(new Event('change', {bubbles:true}));"
            "kwInput.dispatchEvent(new KeyboardEvent('keydown', {key:'a', code:'KeyA', bubbles:true}));"
            "kwInput.dispatchEvent(new KeyboardEvent('keyup', {key:'a', code:'KeyA', bubbles:true}));"
            "return JSON.stringify({success:true, strategy:strategy, tag:kwInput.tagName, name:kwInput.name||'', id:kwInput.id||'', cls:(kwInput.className||'').substring(0,60)});"
            "})()"
        ))
        if isinstance(fill_result, str) and 'error' in fill_result:
            return fill_result

        # ============================================================
        # Step 2: 等待下拉出现 + 提取选项（循环检测最多 5 秒，10次x0.5s）
        # ============================================================
        last_error_detail = None
        for attempt in range(10):
            await asyncio.sleep(0.5)
            result = await _cdp_eval(ws, """
                (function(){
                    var dropdown = null;

                    // 策略1: Ant Design 标准下拉选择器（优先级最高）
                    var selectors = [
                        '.ant-select-dropdown:not([style*="display: none"])',
                        '.ant-select-dropdown',
                        '[class*="suggest-list"]',
                        '[class*="autocomplete"]',
                        '.rc-virtual-list',
                        '[class*="select-dropdown"]',
                        '[role="listbox"]'
                    ];
                    for (var i = 0; i < selectors.length; i++) {
                        var el = document.querySelector(selectors[i]);
                        if (el && el.offsetParent !== null) {
                            dropdown = el;
                            break;
                        }
                    }

                    // 策略2: body 直属的浮动弹出层（Ant Design Select 的 popup 通常挂载在 body 下）
                    if (!dropdown) {
                        var bodyChildren = document.body.children;
                        for (var bi = 0; bi < bodyChildren.length; bi++) {
                            var bc = bodyChildren[bi];
                            if (bc.offsetParent === null && bc.offsetWidth > 0 &&
                                bc.offsetHeight > 80 && bc.offsetHeight < 600) {
                                var cls = bc.className || '';
                                if (cls.indexOf('dropdown') >= 0 ||
                                    cls.indexOf('popup') >= 0 ||
                                    cls.indexOf('select') >= 0 ||
                                    cls.indexOf('option') >= 0) {
                                    dropdown = bc;
                                    break;
                                }
                                // 也检查子元素数量
                                if (bc.children.length >= 3) {
                                    var firstTxt = (bc.children[0].textContent || '').trim();
                                    if (firstTxt.length > 8 && /\\d+[,.]?\\d*$/.test(firstTxt)) {
                                        dropdown = bc;
                                        break;
                                    }
                                }
                            }
                        }
                    }

                    // 策略3: 全文档扫描带类目特征的 div
                    if (!dropdown) {
                        var allDivs = document.querySelectorAll('div');
                        for (var j = 0; j < allDivs.length; j++) {
                            var d = allDivs[j];
                            if (d.offsetParent !== null && d.offsetHeight > 100 && d.offsetHeight < 500
                                && d.children.length >= 2) {
                                var txt = (d.children[0].textContent || '').trim();
                                if (txt.length > 10 && /:.*\\d+[,.]?\\d*$/.test(txt)) {
                                    dropdown = d;
                                    break;
                                }
                            }
                        }
                    }

                    if (!dropdown) {
                        return JSON.stringify({found:false});
                    }

                    // 提取所有选项 — 多策略
                    var items = [];
                    var itemSelectors = [
                        '.ant-select-item-option-content',
                        '.ant-select-item-option',
                        '.rc-virtual-list .ant-select-item',
                        '[role="option"]',
                        '[class*="option"]',
                        'li',
                        '[class*="item"]'
                    ];
                    var optionEls = [];
                    for (var k = 0; k < itemSelectors.length; k++) {
                        optionEls = dropdown.querySelectorAll(itemSelectors[k]);
                        if (optionEls.length > 1) break; // 至少2项才算有效
                    }
                    if (optionEls.length === 0) {
                        optionEls = dropdown.children;
                    }

                    for (var idx = 0; idx < optionEls.length; idx++) {
                        var opt = optionEls[idx];
                        var rawText = (opt.textContent || '').trim();
                        if (!rawText || rawText.length < 5) continue;

                        // 尝试分离 "类目路径" 和 "数量"
                        // SellerSprite 格式: "Beauty & Personal Care:Skin Care:Lip Care    4,539"
                        var displayText = rawText;
                        var countMatch = rawText.match(/(\\d[\\d,.]*)$/);
                        var countVal = countMatch ? countMatch[1].replace(/,/g, '') : '';
                        if (countVal) {
                            displayText = rawText.replace(/\\s*(\\d[\\d,.]*)$/, '').trim();
                        }

                        items.push({
                            index: idx,
                            text: displayText,
                            full_text: rawText,
                            count: countVal,
                            htmlSnippet: rawText.substring(0, 150)
                        });
                    }

                    return JSON.stringify({
                        found: true,
                        count: items.length,
                        dropdownTag: dropdown.tagName,
                        dropdownClass: (dropdown.className || '').substring(0, 80),
                        suggestions: items
                    });
                })()
            """)
            try:
                parsed = _json.loads(result)
                if isinstance(parsed, dict) and parsed.get('found') and parsed.get('count', 0) > 0:
                    return _json.dumps({
                        'success': True,
                        'keyword': keyword,
                        'count': parsed['count'],
                        'suggestions': parsed['suggestions'],
                    }, ensure_ascii=False)
                # 记录最后一次检测的详情（用于超时诊断）
                if isinstance(parsed, dict):
                    last_error_detail = parsed
            except Exception:
                pass

        # 等待超时，没找到下拉 — 返回详细诊断信息
        return _json.dumps({
            'success': False,
            'keyword': keyword,
            'error': 'suggestions_dropdown_timeout',
            'message': '输入 "' + keyword + '" 后未检测到自动补全下拉框（等待5秒）',
            'mode_info': mode_info,
            'last_check': last_error_detail,
        }, ensure_ascii=False)

    except Exception as e:
        import json as _json
        return _json.dumps({
            'success': False,
            'keyword': keyword,
            'error': 'exception',
            'message': str(e)[:200]
        }, ensure_ascii=False)


async def select_keyword_suggestion(keyword_text, cdp_port=None):
    """在自动补全下拉框中选择一个选项

    根据用户从下拉列表中选择的文本，点击对应的下拉选项。

    Args:
        keyword_text: 用户选择的完整文本（如 "Automotive:Lights & Lighting... 18,900"）
        cdp_port: CDP 端口（默认 9222）

    Returns:
        str: 操作结果
    """
    import asyncio
    try:
        # 连接 CDP
        ws = await connect(cdp_port)

        safe_text = keyword_text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")

        result = await _cdp_eval(ws, (
            "(function(){"
            "var targetText = '" + safe_text + "';"
            "var containers = document.querySelectorAll("
            "  '.ant-select-dropdown,[class*=\"suggest\"],[class*=\"autocomplete\"],"
            "  [class*=\"dropdown\"][class*=\"menu\"],.rc-virtual-list,"
            "  [role=\"listbox\"]'"
            ");"
            "for (var ci = 0; ci < containers.length; ci++) {"
            "  var c = containers[ci];"
            "  if (c.offsetParent === null) continue;"
            "  var items = c.querySelectorAll('li,[role=\"option\"],.ant-select-item,[class*=\"option\"]');"
            "  if (items.length === 0) items = c.children;"
            "  for (var ii = 0; ii < items.length; ii++) {"
            "    var it = items[ii];"
            "    var txt = (it.textContent || '').trim();"
            "    if (txt.indexOf(targetText) >= 0 || targetText.indexOf(txt.substring(0,40)) >= 0) {"
            "      it.click();"
            "      return JSON.stringify({success:true, clicked:txt.substring(0,80), index:ii});"
            "    }"
            "  }"
            "}"
            "return JSON.stringify({error:'suggestion_item_not_found', text:targetText});"
            "})()"
        ))
        await asyncio.sleep(0.5)
        return result
    except Exception as e:
        return f"select_suggestion_error: {e}"


async def switch_nav_mode(ws):
    """切换导航模式为类目浏览模式

    SellerSprite 市场研究页面有一个导航模式切换按钮:
    a.category-dropdown[data-q="nav"]
    点击后从搜索模式切换到类目导航模式
    """
    try:
        clicked = await _cdp_eval(ws, """
            (function(){
                // 精确选择器: SellerSprite 导航模式切换按钮
                var btn = document.querySelector('a.category-dropdown[data-q="nav"]');
                if (!btn) return 'nav_mode_not_found: a.category-dropdown[data-q="nav"]';
                btn.click();
                return 'ok';
            })()
        """)
        await asyncio.sleep(0.5)
        return clicked
    except Exception as e:
        return f"switch_nav_error: {e}"


async def open_nav_dialog(ws):
    """打开类目选择对话框

   SellerSprite 的类目导航弹窗通过 #btn-nav-node-dialog 按钮触发,
   弹窗内容在 #modal-breadcrumb-node-dialog 中
   需要等待弹窗动画完成 + 子级类目列表加载
   返回详细诊断信息便于排查"点不上"问题
   """
    try:
        # === 阶段0: 先检查页面状态（关键诊断）===
        page_state = await _cdp_eval(ws, """
            (function(){
                // 检查导航模式按钮
                var navMode = document.querySelector('a.category-dropdown[data-q="nav"]');
                // 检查弹窗触发按钮
                var dialogBtn = document.querySelector('#btn-nav-node-dialog');
                // 检查弹窗是否已打开
                var dialog = document.querySelector('#modal-breadcrumb-node-dialog');
                return JSON.stringify({
                    nav_mode_found: !!navMode,
                    nav_mode_visible: navMode ? (navMode.offsetParent !== null) : false,
                    nav_mode_text: navMode ? (navMode.textContent||'').trim().substring(0,50) : '',
                    dialog_btn_found: !!dialogBtn,
                    dialog_btn_visible: dialogBtn ? (dialogBtn.offsetParent !== null) : false,
                    dialog_already_open: dialog ? (dialog.offsetParent !== null) : false,
                    current_url: location.href.substring(0, 120)
                });
            })()
        """)
        if isinstance(page_state, str):
            try:
                page_state = json.loads(page_state)
            except Exception:
                page_state = {}

        # 如果弹窗已经打开，直接返回当前状态
        if page_state.get('dialog_already_open'):
            items = await _cdp_eval(ws, """
                (function(){
                    var items = document.querySelectorAll('#modal-breadcrumb-node-dialog .children-nodes li');
                    var list = [];
                    items.forEach(function(item, i){
                        list.push({
                            i:i,
                            label: item.getAttribute('data-label') || '',
                            text: (item.textContent || '').trim().split('\\n')[0].substring(0,60),
                            html: item.outerHTML.substring(0, 150)
                        });
                    });
                    return JSON.stringify({dialog_already_open:true, count:list.length, categories:list});
                })()
            """)
            return items

        # 如果找不到弹窗按钮，返回详细错误
        if not page_state.get('dialog_btn_found'):
            return JSON.stringify({
                error: 'dialog_button_not_found',
                diagnosis: page_state,
                hint: '#btn-nav-node-dialog 元素不存在，可能页面未正确加载或 SellerSprite 已改版'
            })

        # === 阶段1: 点击打开类目导航弹窗按钮 ===
        clicked = await _cdp_eval(ws, """
            (function(){
                var btn = document.querySelector('#btn-nav-node-dialog');
                if (!btn) return 'dialog_button_not_found: #btn-nav-node-dialog';
                btn.click();
                return 'ok';
            })()
        """)

        # 等待弹窗出现并渲染子级类目（最多8秒）
        dialog_ready = None
        for wait_i in range(16):
            await asyncio.sleep(0.5)
            dialog_ready = await _cdp_eval(ws, """
                (function(){
                    var dialog = document.querySelector('#modal-breadcrumb-node-dialog');
                    if (!dialog || dialog.offsetParent === null) return JSON.stringify({ready:false, reason:'dialog_not_visible', visible: false});
                    var items = dialog.querySelectorAll('.children-nodes li');
                    if (items.length === 0) return JSON.stringify({ready:false, reason:'no_children_items', count:0, visible: true});
                    return JSON.stringify({ready:true, count:items.length, visible: true});
                })()
            """)
            if isinstance(dialog_ready, str):
                try:
                    dialog_ready = json.loads(dialog_ready)
                except Exception:
                    dialog_ready = {}
            if isinstance(dialog_ready, dict) and dialog_ready.get('ready'):
                break

        # 列出可见的顶级类目，用于调试确认（含 HTML 片段 + hasChildren）
        cats = await _cdp_eval(ws, "(function(){" +
            "var items = document.querySelectorAll('#modal-breadcrumb-node-dialog .children-nodes li');" +
            "var list = [];" +
            "items.forEach(function(item, i){" +
            "    list.push({" +
            "        i: i," +
            "        label: item.getAttribute('data-label') || ''," +
            "        text: (item.textContent || '').trim().split('\\n')[0].substring(0,60)," +
            "        html: item.outerHTML.substring(0, 150)," +
            "        hasChildren: !!(item.querySelector('ul') || item.querySelector('.children'))" +
            "    });" +
            "});" +
            "var dlg = document.querySelector('#modal-breadcrumb-node-dialog');" +
            "var dlgVis = !!(dlg && dlg.offsetParent !== null);" +
            "return JSON.stringify({" +
            "    clicked: " + json.dumps(clicked) + "," +
            "    page_state: " + json.dumps(page_state, ensure_ascii=False) + "," +
            "    wait_rounds: " + str(wait_i + 1) + "," +
            "    dialog_open: dlgVis," +
            "    dialog_visible: dlgVis," +
            "    ready_state: dialog_ready," +
            "    count: list.length," +
            "    categories: list" +
            "});" +
            "})()")
        return cats
    except Exception as e:
        import traceback
        return f"open_dialog_error: {e}"


async def select_category(ws, category_name):
    """在类目导航弹窗中选择指定类目

    匹配策略（按优先级）:
    0. 先用多重选择器扫描弹窗内所有可点击元素，打印完整内容到日志
    1. data-label / textContent 精确匹配（忽略大小写和首尾空白）
    2. data-label / textContent 相互包含匹配（indexOf，不分方向）
    3. 拆词匹配：将 category_name 拆为单词，逐个与每项做 indexOf
    4. 模糊关键词：取每个单词前5字符做兜底

    返回 JSON: success/matched mode/totalItems/allItems/snapshot
    """
    import asyncio
    try:
        # ── 阶段0：用多重选择器探测弹窗内的实际 DOM 结构 ──
        _js_dom_probe = (
            "(function(){"
            "var dialog = document.querySelector('#modal-breadcrumb-node-dialog');"
            "if (!dialog) return JSON.stringify({error:'dialog_not_found'});"
            "var visible = dialog.offsetParent !== null;"
            "var selectors = ["
            "  '.children-nodes li',"
            "  '.children-nodes [class*=node]',"
            "  '.children-nodes > *',"
            "  '.category-node',"
            "  'li[data-label]',"
            "  '.tree-node',"
            "  '[role=listbox] > li',"
            "  '[role=option]'"
            "];"
            "var results = {};"
            "selectors.forEach(function(sel){"
            "  var els = dialog.querySelectorAll(sel);"
            "  if (els.length > 0) { results[sel] = els.length; }"
            "});"
            "return JSON.stringify({"
            "  visible: visible,"
            "  selector_results: results,"
            "  dialog_html_preview: dialog.innerHTML.substring(0, 800)"
            "});"
            "})()"
        )
        dom_probe = await _cdp_eval(ws, _js_dom_probe)


        # ── 阶段1：获取所有类目项的完整信息（核心诊断）──
        snapshot = await _cdp_eval(ws, "(function(){" +
            "var dialog = document.querySelector('#modal-breadcrumb-node-dialog');" +
            "if (!dialog) return JSON.stringify({error:'dialog_not_found'});" +
            "// 主选择器" +
            "var items = dialog.querySelectorAll('.children-nodes li');" +
            "var fallbackItems = items.length === 0 ? dialog.querySelectorAll('.children-nodes > *, li[data-label], [class*=node-item], [class*=tree-node]') : [];" +
            "var targetList = items.length > 0 ? items : fallbackItems;" +
            "var allItems = [];" +
            "targetList.forEach(function(item, i){" +
            "    var rawText = (item.textContent || '').trim();" +
            "    var label = item.getAttribute('data-label') || '';" +
            "    var firstLine = rawText.split('\\n')[0];" +
            "    allItems.push({" +
            "        idx: i," +
            "        tag: item.tagName || ''," +
            "        className: (item.className || '').toString()," +
            "        label: label," +
            "        text: firstLine.substring(0, 150)," +
            "        fullText: rawText.substring(0, 300)," +
            "        html: item.outerHTML.substring(0, 400)," +
            "        childCount: item.children.length," +
            "        hasChildUl: !!item.querySelector('ul')," +
            "        hasChildDiv: !!item.querySelector('div')" +
            "    });" +  
            "});" +
            "return JSON.stringify({" +
            "  count: allItems.length," +
            "  used_fallback: items.length === 0," +
            "  items: allItems" +
            "});" +
            "})()")

        # 解析 snapshot 用于日志输出
        snap_data = None
        if isinstance(snapshot, str):
            try:
                snap_data = json.loads(snapshot)
            except Exception:
                pass

        # 将快照信息打印到控制台（关键诊断！）
        if snap_data and isinstance(snap_data, dict):
            items_list = snap_data.get('items', [])
            print(f"[select_category] 弹窗内类目数: {snap_data.get('count', 0)}, 使用fallback: {snap_data.get('used_fallback', False)}")
            for it in items_list:
                print(f"  [{it.get('idx')}] tag={it.get('tag')} class={str(it.get('className',''))[:30]} "
                      f"label={it.get('label','')!r} text={it.get('text','')!r}")

        # ── 阶段2：构建匹配并执行点击 ──
        aliases = _resolve_category_aliases(category_name)

        safe_aliases = []
        for a in aliases:
            sa = a.replace("\\", "\\\\").replace("'", "\\'")
            safe_aliases.append(sa)

        safe_aliases_json = json.dumps(safe_aliases, ensure_ascii=False)

        # 提取模糊关键词
        fuzzy_kws = _get_fuzzy_keywords(category_name)
        fuzzy_kws_json = json.dumps(fuzzy_kws, ensure_ascii=False)

        js_code = "(function(){" + \
            "var dialog = document.querySelector('#modal-breadcrumb-node-dialog');" + \
            "if (!dialog) return JSON.stringify({success:false, error:'dialog_not_found'});" + \
            "var items = dialog.querySelectorAll('.children-nodes li');" + \
            "var targetItems = items.length > 0 ? items : dialog.querySelectorAll('.children-nodes > *, li[data-label], [class*=node-item]');" + \
            "var allItems = [];" + \
            "targetItems.forEach(function(item, i){" + \
            "    var text = (item.textContent || '').trim();" + \
            "    var label = item.getAttribute('data-label') || '';" + \
            "    allItems.push({idx:i, label:label, text:text.substring(0,100), html:item.outerHTML.substring(0, 200)});" + \
            "});" + \
            "" + \
            "var candidates = " + safe_aliases_json + ";" + \
            "var found = null;" + \
            "" + \
            "for (var k = 0; k < targetItems.length && !found; k++) {" + \
            "    var t = (targetItems[k].textContent || '').trim().toLowerCase();" + \
            "    var l = (targetItems[k].getAttribute('data-label') || '').toLowerCase();" + \
            "" + \
            "    for (var c = 0; c < candidates.length && !found; c++) {" + \
            "        var cc = candidates[c].toLowerCase();" + \
            "" + \
            "        // 1. 精确匹配 label 或 text" + \
            "        if (l === cc || t === cc) { found = {idx:k, mode:'exact', alias:candidates[c], label:l, text:t.substring(0,60)}; break; }" + \
            "" + \
            "        // 2. 包含匹配（双向，不限长度）—— 核心匹配！" + \
            "        if (l.indexOf(cc) >= 0 || cc.indexOf(l) >= 0) { found = {idx:k, mode:'contains', alias:candidates[c], label:l, text:t.substring(0,60)}; break; }" + \
            "        if (t.indexOf(cc) >= 0 || cc.indexOf(t) >= 0) { found = {idx:k, mode:'contains_text', alias:candidates[c], label:l, text:t.substring(0,60)}; break; }" + \
            "    }" + \
            "" + \
            "    // 3. 拆词匹配：将候选拆成单词逐个 indexOf" + \
            "    if (!found) {" + \
            "        for (var c2 = 0; c2 < candidates.length && !found; c2++) {" + \
            "            var words = candidates[c2].toLowerCase().split(/[\\s&,]+/);" + \
            "            for (var w = 0; w < words.length && !found; w++) {" + \
            "                var word = words[w].trim();" + \
            "                if (word.length < 3) continue;" + \
            "                if (t.indexOf(word) >= 0 || l.indexOf(word) >= 0) { found = {idx:k, mode:'word_match', alias:word, label:l, text:t.substring(0,60)}; break; }" + \
            "            }" + \
            "            if (found) break;" + \
            "        }" + \
            "    }" + \
            "}" + \
            "" + \
            "// 4. 兜底：模糊关键词匹配" + \
            "if (!found) {" + \
            "    var keywords = " + fuzzy_kws_json + ";" + \
            "    for (var k2 = 0; k2 < targetItems.length && !found; k2++) {" + \
            "        var t2 = (targetItems[k2].textContent || '').trim().toLowerCase();" + \
            "        var l2 = (targetItems[k2].getAttribute('data-label') || '').toLowerCase();" + \
            "        for (var kw = 0; kw < keywords.length && !found; kw++) {" + \
            "            var keyw = keywords[kw];" + \
            "            if (keyw.length < 3) continue;" + \
            "            if (t2.indexOf(keyw) >= 0 || l2.indexOf(keyw) >= 0) { found = {idx:k2, mode:'fuzzy_kw', alias:keyw, label:l2, text:t2.substring(0,60)}; }" + \
            "        }" + \
            "    }" + \
            "}" + \
            "" + \
            "if (found) {" + \
            "    targetItems[found.idx].click();" + \
            "    return JSON.stringify({success:true, matched:found, totalItems:allItems.length, allItems:allItems, snapshot:snapshot, searched:categoryName, candidatesUsed:candidates});" + \
            "}" + \
            "return JSON.stringify({success:false, searched:categoryName, totalItems:allItems.length, allItems:allItems, snapshot:snapshot, candidatesUsed:candidates});" + \
            ")()"

        # 注入 category_name 到 JS
        js_code = js_code.replace('categoryName', json.dumps(category_name, ensure_ascii=False))

        selected = await _cdp_eval(ws, js_code)
        await asyncio.sleep(1.5)
        return selected
    except Exception as e:
        import traceback
        return f"select_category_error: {e}\n{traceback.format_exc()}"


async def confirm_category(ws):
    """确认类目选择

    点击弹窗内的 '使用当前节点' 按钮:
    #modal-breadcrumb-node-dialog button[name=nav-use-current-node]
    然后验证 nodeIdPath 是否已填入值
    """
    import asyncio
    try:
        confirmed = await _cdp_eval(ws, """
            (function(){
                var btn = document.querySelector('#modal-breadcrumb-node-dialog button[name=nav-use-current-node]');
                if (!btn) return 'confirm_btn_not_found: button[name=nav-use-current-node]';
                btn.click();
                return 'ok';
            })()
        """)
        await asyncio.sleep(1)

        # 验证表单状态
        state = await _cdp_eval(ws, """
            (function(){
                return JSON.stringify({
                    nodeIdPath: (document.querySelector('input[name=nodeIdPath]') || {}).value || '',
                    keyword: (document.querySelector('input[name=departmentKeyword]') || {}).value || '',
                    isNav: !!(document.querySelector('.change-category') &&
                             document.querySelector('.change-category').classList.contains('isNav'))
                });
            })()
        """)
        return state
    except Exception as e:
        return f"confirm_error: {e}"


async def submit_search(ws):
    """提交搜索/筛选请求

    优先查找包含"筛选市场"文本的按钮,
    然后尝试 #form-condition-search 表单提交
    """
    import asyncio
    try:
        submitted = await _cdp_eval(ws, """
            (function(){
                // 方法1: 查找"筛选市场"按钮（SellerSprite 专用）
                var candidates = Array.from(document.querySelectorAll(
                    'button, a, input[type=button], input[type=submit], .el-button'
                ));
                var submitBtn = candidates.find(function(el){
                    var text = (el.textContent || el.value || '').trim();
                    return text.indexOf('筛选市场') >= 0;
                });
                if (submitBtn) {
                    submitBtn.click();
                    return 'submitted: 点击了筛选市场按钮';
                }

                // 方法2: 提交表单
                var form = document.querySelector('#form-condition-search');
                if (!form) return 'form_not_found: #form-condition-search';
                if (typeof form.requestSubmit === 'function') {
                    form.requestSubmit();
                    return 'submitted: form.requestSubmit';
                }
                form.submit();
                return 'submitted: form.submit';
            })()
        """)
        await asyncio.sleep(3)
        return submitted
    except Exception as e:
        return f"submit_error: {e}"


async def get_page_info(ws):
    """获取当前表格的分页信息（总页数、当前页）"""
    info = await _cdp_eval(ws, """
        (function(){
            // 尝试多种分页组件的选择器
            const paginations = document.querySelectorAll(
                '.ant-pagination, .pagination, [class*="Pagination"], .el-pagination'
            );
            for(const pg of paginations){
                if(pg.offsetParent === null) continue;
                const totalText = pg.querySelector(
                    '.ant-pagination-total-text, .total, [class*="total"]'
                );
                const currentPg = pg.querySelector(
                    '.ant-pagination-item-active, .active, .current, [class*="active"]'
                );
                return JSON.stringify({
                    total_text: totalText ? totalText.textContent.trim() : '',
                    current_page: currentPg ? parseInt(currentPg.textContent) || 1 : 1,
                    page_items: pg.querySelectorAll('li, .page-item, [class*="item"]').length
                });
            }

            // 备用：从表格数据估算页数
            const rows = document.querySelectorAll('table tbody tr, [class*="table"] tbody tr');
            return JSON.stringify({
                total_text: rows.length + ' rows visible',
                current_page: 1,
                page_items: 0,
                row_count: rows.length
            });
        })()
    """)
    if info:
        try:
            return json.loads(info)
        except (json.JSONDecodeError, TypeError):
            pass
    return {"current_page": 1, "total_pages": 1}


async def goto_page(ws, page_num):
    """跳转到指定页码"""
    import asyncio
    try:
        js_code = """
            (function(){
                const pageNum = """ + str(page_num) + """;

                // 方法1: 点击指定页码按钮
                const pageItems = document.querySelectorAll(
                    '.ant-pagination-item, .pagination li, .page-item, [class*="page"] li'
                );
                for(const item of pageItems){
                    const text = item.textContent?.trim();
                    if(parseInt(text) === pageNum){
                        item.click();
                        return 'jumped: click: '+pageNum;
                    }
                }

                // 方法2: 使用页码输入跳转
                const jumpInput = document.querySelector(
                    '.ant-pagination-options-quick-jumper input, ' +
                    '.pagination-jump input, [class*="jump"] input'
                );
                if(jumpInput){
                    const nativeSet = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    nativeSet.call(jumpInput, String(pageNum));
                    jumpInput.dispatchEvent(new Event('input', {bubbles:true}));
                    jumpInput.dispatchEvent(new Event('change', {bubbles:true}));

                    // 按回车确认
                    jumpInput.dispatchEvent(new KeyboardEvent('keydown', {
                        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
                    }));
                    return 'jumped: input: '+pageNum;
                }

                return 'goto_failed: '+pageNum;
            })()
        """
        result = await _cdp_eval(ws, js_code)
        await asyncio.sleep(2)
        await _wait_loading(ws, PAGE_TIMEOUT)
        return result
    except Exception as e:
        return f"goto_page_error: {e}"


async def extract_current_page(ws):
    """
    提取当前页面的细分市场榜单数据
    参照 cdp_sellersprite_full.py 的成熟实现：
    - 精确选择器 #table-condition-search tbody tr
    - 有效行过滤：第一格纯数字 + >= 13 列
    - 按固定 cell index 映射字段（不依赖表头名）
    - cells[1] 用 split('\\n')[0] 截断（去除 Top10 链接文字干扰）
    - cells[10] 用正则分别提取商品/品牌/卖家集中度
    - 处理展开行(detail row)获取 A+占比、毛利率、退货率等更多字段

    Returns:
        list[dict]: 标准化后的细分市场记录列表
    """
    import asyncio
    # 稍等确保 DOM 渲染完成
    await asyncio.sleep(1)

    raw = await _cdp_eval(ws, r"""
        (function(){
            // ── 1. 精确选择器：只取 SellerSprite 市场研究主表格 ──
            var rows = document.querySelectorAll('#table-condition-search tbody tr');
            if (rows.length === 0) {
                // 备用选择器：某些版本可能 id 不同
                rows = document.querySelectorAll(
                    '.market-research-table tbody tr, ' +
                    '[class*="condition-search"] tbody tr, ' +
                    'table.ant-table-bordered tbody tr'
                );
            }
            if (rows.length === 0) {
                return JSON.stringify({error: 'no_rows_found', row_count: 0});
            }

            var markets = [];

            for (var i = 0; i < rows.length; i++) {
                var row = rows[i];
                var cells = row.querySelectorAll('td');

                // ── 2. 关键过滤：第一格必须是纯数字，且列数 >= 13 ──
                // 这能排除展开行、分组标题行、广告行等非数据行
                var isFirstCellNumber = cells[0] && /^\d+$/.test(cells[0].textContent.trim());
                if (!(isFirstCellNumber && cells.length >= 13)) {
                    continue;  // 跳过无效行
                }

                var m = {};

                // ── 3. 按固定 cell index 映射字段 ──
                m['rank'] = cells[0].textContent.trim();

                // 细分市场名：只取第一行文本（去除混入的 Top10 产品链接 "第1名 第2名..."）
                m['sub_market'] = cells[1]
                    ? cells[1].textContent.trim().split('\n')[0].trim()
                    : '';

                m['sample_count'] = cells[2]
                    ? cells[2].textContent.trim().replace(/\s+/g, ' ')
                    : '';

                // cells[3]: 月总销量
                m['monthly_sales'] = cells[3] ? cells[3].textContent.trim() : '';

                // cells[4]: 月均消费额（可能含头部商品月均消费额，取第一个值）
                m['avg_monthly_spend'] = cells[4]
                    ? cells[4].textContent.trim().split(/\s+/)[0]
                    : '';
                m['top_avg_monthly_spend'] = cells[4]
                    ? cells[4].textContent.trim().split(/\s+/).slice(1).join(' ')
                    : '';

                // cells[5]: 月均价消费额 / 平均价格
                m['avg_price'] = cells[5]
                    ? cells[5].textContent.trim().split(/\s+/)[0]
                    : '';
                m['top_avg_monthly_spend_price'] = cells[5]
                    ? cells[5].textContent.trim().split(/\s+/).slice(1).join(' ')
                    : '';

                // cells[6]: 平均评分数 + 平均星级（两个值在一个格内）
                m['avg_review_count'] = cells[6]
                    ? cells[6].textContent.trim().split(/\s+/)[0]
                    : '';
                m['avg_rating'] = cells[6]
                    ? cells[6].textContent.trim().split(/\s+/)[1] || ''
                    : '';

                // cells[7]: 平均 BSR + 头部商品平均 BSR
                m['avg_bsr'] = cells[7]
                    ? cells[7].textContent.trim().split(/\s+/)[0]
                    : '';
                m['top_avg_bsr'] = cells[7]
                    ? cells[7].textContent.trim().split(/\s+/).slice(1).join(' ')
                    : '';

                // cells[8]: 平均卖家数 + 平均价格
                m['avg_seller_count'] = cells[8]
                    ? cells[8].textContent.trim().split(/\s+/)[0]
                    : '';
                m['avg_seller_price'] = cells[8]
                    ? cells[8].textContent.trim().split(/\s+/).pop()
                    : '';

                // cells[9]: 卖家类型
                m['seller_type'] = cells[9]
                    ? cells[9].textContent.trim().replace(/\s+/g, ' ').substring(0, 60)
                    : '';

                // ── 4. cells[10]: 三个集中度指标在一个单元格内，正则拆分 ──
                if (cells[10]) {
                    var ct = cells[10].textContent;
                    var gm = ct.match(/商品[：:]\s*([\d.]+%)/);
                    var bm = ct.match(/品牌[：:]\s*([\d.]+%)/);
                    var sm = ct.match(/卖家[：:]\s*([\d.]+)/);
                    m['product_concentration'] = gm ? gm[1] : '';
                    m['brand_concentration'] = bm ? bm[1] : '';
                    m['seller_concentration'] = sm ? sm[1] : '';
                } else {
                    m['product_concentration'] = '';
                    m['brand_concentration'] = '';
                    m['seller_concentration'] = '';
                }

                // ── 5. cells[11]: 新品数量 + 新品占比 ──
                m['new_product_count'] = cells[11]
                    ? cells[11].textContent.trim().split(/\s+/)[0]
                    : '';
                m['new_product_ratio'] = cells[11]
                    ? cells[11].textContent.trim().split(/\s+/)[1] || ''
                    : '';

                // cells[12]: 商品总数
                m['total_products'] = cells[12]
                    ? cells[12].textContent.trim()
                    : '';

                // cells[13]: 退货率
                m['return_rate'] = cells[13]
                    ? cells[13].textContent.trim().split(/\s+/)[0]
                    : '';

                // ── 6. 展开行(detail row)处理 ──
                // 下一行 sibling 包含 A+ 占比、新品详情、毛利率、重量、体积等
                var nextRow = rows[i + 1];
                if (nextRow) {
                    var detailText = nextRow.textContent;

                    // 按"同类平均值"分为市场部分和同类均值部分
                    var parts = detailText.split('同类平均值');
                    var marketPart = parts[0] || '';
                    var avgPart = parts.length > 1 ? parts.slice(1).join('同类平均值') : '';

                    var pm;

                    // 市场级指标
                    pm = marketPart.match(/A\+数量占比[：:\s]*([\d.]+%)/);
                    m['a_plus_ratio'] = pm ? pm[1] : '';

                    pm = marketPart.match(/新品平均评分数[：:\s]*(N\/A|[\d,]+)/);
                    m['new_avg_reviews'] = pm ? pm[1] : '';

                    pm = marketPart.match(/新品平均价格[：:\s]*(N\/A|\$?[\d,.]+)/);
                    m['new_avg_price'] = pm ? pm[1] : '';

                    pm = marketPart.match(/新品平均星级[：:\s]*(N\/A|[\d.]+)/);
                    m['new_avg_rating'] = pm ? pm[1] : '';

                    pm = marketPart.match(/新品月总销量[：:\s]*(N\/A|[\d,]+)/);
                    m['new_monthly_sales'] = pm ? pm[1] : '';

                    pm = marketPart.match(/新品月均价消费额[：:\s]*(N\/A|\$?[\d,.]+)/);
                    m['new_avg_spend'] = pm ? pm[1] : '';

                    pm = marketPart.match(/平均重量[：:\s]*([\d.]+\s*pounds?\s*\([^)]+\))/);
                    m['avg_weight'] = pm ? pm[1] : '';

                    pm = marketPart.match(/平均体积[：:\s]*([\d.]+\s*in³?\s*\([^)]+\))/);
                    m['avg_volume'] = pm ? pm[1] : '';

                    pm = marketPart.match(/平均毛利率[：:\s]*([\d.]+%)/);
                    m['margin_rate'] = pm ? pm[1] : '';

                    pm = marketPart.match(/卖家所在地[：:\s]*([^\s|]+\|[^\s]+)/);
                    m['seller_location'] = pm ? pm[1] : '';

                    pm = marketPart.match(/搜索购买比[：:\s]*([\d.]+[%‰])/);
                    m['search_buy_ratio'] = pm ? pm[1] : '';

                    // 同类平均值指标（带前缀区分）
                    pm = avgPart.match(/A\+数量占比[：:\s]*([\d.]+%)/);
                    m['cat_avg_a_plus_ratio'] = pm ? pm[1] : '';

                    pm = avgPart.match(/新品平均评分数[：:\s]*(N\/A|[\d,]+)/);
                    m['cat_avg_new_reviews'] = pm ? pm[1] : '';

                    pm = avgPart.match(/新品平均价格[：:\s]*(N\/A|\$?[\d,.]+)/);
                    m['cat_avg_new_price'] = pm ? pm[1] : '';

                    pm = avgPart.match(/新品平均星级[：:\s]*(N\/A|[\d.]+)/);
                    m['cat_avg_new_rating'] = pm ? pm[1] : '';

                    pm = avgPart.match(/新品月总销量[：:\s]*(N\/A|[\d,]+)/);
                    m['cat_avg_new_sales'] = pm ? pm[1] : '';

                    pm = avgPart.match(/新品月均价消费额[：:\s]*(N\/A|\$?[\d,.]+)/);
                    m['cat_avg_new_spend'] = pm ? pm[1] : '';

                    pm = avgPart.match(/平均重量[：:\s]*([\d.]+\s*pounds?\s*\([^)]+\))/);
                    m['cat_avg_weight'] = pm ? pm[1] : '';

                    pm = avgPart.match(/平均体积[：:\s]*([\d.]+\s*in³?\s*\([^)]+\))/);
                    m['cat_avg_volume'] = pm ? pm[1] : '';

                    pm = avgPart.match(/平均毛利率[：:\s]*([\d.]+%)/);
                    m['cat_avg_margin_rate'] = pm ? pm[1] : '';

                    pm = avgPart.match(/卖家所在地[：:\s]*([^\s|]+\|[^\s]+)/);
                    m['cat_avg_seller_location'] = pm ? pm[1] : '';

                    pm = avgPart.match(/搜索购买比[：:\s]*([\d.]+[%‰])/);
                    m['cat_avg_search_buy_ratio'] = pm ? pm[1] : '';
                }

                markets.push(m);
            }

            return JSON.stringify(markets);
        })()
    """)

    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("error"):
                return []
            # 已经是标准化的 dict 列表，直接返回
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    return []


# ── 字段标准化 ─────────────────────────────────────────────
# extract_current_page() 已在 JS 层直接输出标准英文字段名
# 此映射表仅作为 normalize_record 的兼容层（处理可能的旧格式数据）

# SellerSprite 市场研究字段：JS 提取层 → 最终标准名（大部分已一致）
_FIELD_MAP = {
    # 基础信息（JS 层已直接输出正确 key，以下为兼容旧格式）
    "序号": "rank",
    "细分市场": "sub_market",
    "样本数量": "sample_count",

    # 销量相关
    "月总销量": "monthly_sales",
    "月均消费额": "avg_monthly_spend",
    "头部商品月均消费量": "top_avg_monthly_spend",

    # 价格相关
    "平均价格": "avg_price",
    "头部商品月均价消费额": "top_avg_monthly_spend_price",

    # 评价相关
    "平均评分数": "avg_review_count",
    "平均星级": "avg_rating",

    # BSR
    "平均bsr": "avg_bsr",
    "头部商品平均bsr": "top_avg_bsr",

    # 卖家信息
    "平均卖家数": "avg_seller_count",
    "卖家类型": "seller_type",

    # 集中度（关键竞争指标）— JS 层已用正则拆分
    "商品集中度": "product_concentration",
    "品牌集中度": "brand_concentration",
    "卖家集中度": "seller_concentration",

    # 新品机会 — JS 层已按空格拆分
    "新品数量": "new_product_count",
    "新品占比": "new_product_ratio",
    "商品总数": "total_products",

    # 风险指标
    "退货率": "return_rate",
    "a+数量占比": "a_plus_ratio",

    # 新品详情（来自展开行 detail row）
    "新品平均评分数": "new_avg_reviews",
    "新品平均价格": "new_avg_price",
    "新品平均星级": "new_avg_rating",
    "新品月销量": "new_monthly_sales",
    "新品月均价消费额": "new_avg_spend",

    # 物理属性（来自展开行）
    "平均重量": "avg_weight",
    "平均体积": "avg_volume",

    # 盈利能力（来自展开行）
    "利润率/毛利率": "margin_rate",
    "搜索购买比": "search_buy_ratio",
    "卖家所在地": "seller_location",

    # 同类平均值前缀（来自展开行）
}


def normalize_record(raw_record):
    """
    将原始提取的记录字段标准化。
    新版 extract_current_page() 已在 JS 层输出标准英文字段名，
    此函数主要做：1) 兼容旧格式中文 key  2) 确保必要默认值
    """
    normalized = {}

    for key, value in raw_record.items():
        # 跳过空 key
        if not key:
            continue

        # 如果 key 已经是标准英文名（新版 JS 输出），直接保留
        if key in (
            'rank', 'sub_market', 'sample_count',
            'monthly_sales', 'avg_monthly_spend', 'top_avg_monthly_spend',
            'avg_price', 'top_avg_monthly_spend_price',
            'avg_review_count', 'avg_rating',
            'avg_bsr', 'top_avg_bsr',
            'avg_seller_count', 'avg_seller_price', 'seller_type',
            'product_concentration', 'brand_concentration', 'seller_concentration',
            'new_product_count', 'new_product_ratio', 'total_products',
            'return_rate', 'a_plus_ratio',
            'new_avg_reviews', 'new_avg_price', 'new_avg_rating',
            'new_monthly_sales', 'new_avg_spend',
            'avg_weight', 'avg_volume',
            'margin_rate', 'search_buy_ratio', 'seller_location',
            # 同类平均值字段
            'cat_avg_a_plus_ratio', 'cat_avg_new_reviews', 'cat_avg_new_price',
            'cat_avg_new_rating', 'cat_avg_new_sales', 'cat_avg_new_spend',
            'cat_avg_weight', 'cat_avg_volume', 'cat_avg_margin_rate',
            'cat_avg_seller_location', 'cat_avg_search_buy_ratio',
        ):
            normalized[key] = value
        else:
            # 尝试映射旧格式中文 key
            mapped_key = _FIELD_MAP.get(key, key)
            normalized[mapped_key] = value

    # 确保 rank 字段存在
    if "rank" not in normalized or not normalized.get("rank"):
        normalized["rank"] = raw_record.get("index", raw_record.get("序号", 0))

    return normalized


# ── 主扫描入口 ─────────────────────────────────────────────

async def scan_rank(
    market=DEFAULT_MARKET,
    category=None,
    pages=1,
    cdp_port=None,
    progress_callback=None,
    page_callback=None,
    mode='nav',
    keyword=None,
):
    """
    执行完整的榜单扫描流程

    Args:
        market: 市场站点代码 (US/UK/DE/...)
        category: 类目名称 (如 "Automotive")，导航模式时使用
        pages: 要扫描的最大页数
        cdp_port: CDP 端口号
        progress_callback(pct, msg): 进度回调
        page_callback(page_num, records): 每页数据回调
        mode: 'nav'(类目导航) 或 'search'(关键词搜索)
        keyword: 搜索关键词，搜索模式时使用

    Returns:
        dict: {success, market, category, pages_scanned, total_records, records}
    """
    import asyncio

    def report_progress(pct, msg):
        if progress_callback:
            progress_callback(pct, msg)

    try:
        # Step 1: 连接 CDP
        report_progress(5, "正在连接 Chrome DevPorts...")
        ws = await connect(cdp_port)
        report_progress(10, "CDP 已连接")

        # Step 2: 进入市场研究页面
        report_progress(15, f"正在进入 {market} 市场研究页面...")
        await ensure_market_research_page(ws, market)
        report_progress(20, "已进入市场研究页面")

        # Step 3: 设置参数
        report_progress(25, "正在设置扫描参数...")
        await set_params(ws, market, None, DEFAULT_NEW_MONTHS)
        report_progress(30, "参数已设置")

        # Step 4: 根据模式走不同路径
        if mode == 'search' and keyword:
            # ── 搜索模式：输入关键词 ──
            report_progress(35, f"正在切换到搜索模式，输入关键词: {keyword}...")
            # 确保当前是搜索模式（SellerSprite 默认就是搜索模式）
            search_result = await input_search_keyword(ws, keyword)
            report_progress(50, f"关键词已输入: {search_result}")
        else:
            # ── 导航模式：类目选择流程 ──
            report_progress(35, "正在打开类目选择...")
            await switch_nav_mode(ws)
            dialog_info = await open_nav_dialog(ws)
            report_progress(40, f"类目选择器已打开: {dialog_info}")

            # Step 5: 选择类目（关键步骤）
            if category:
                report_progress(45, f'正在选择类目: {category}...')
                result = await select_category(ws, category)

                # 解析选择结果，检测是否真正匹配成功
                select_success = True
                if isinstance(result, str):
                    # 尝试解析 JSON 结果
                    try:
                        import json as _json
                        parsed = _json.loads(result)
                        if isinstance(parsed, dict):
                            select_success = parsed.get('success', False)
                            if not select_success:
                                # 收集诊断信息：所有可用类目 + snapshot DOM 详情
                                all_items = parsed.get("allItems", [])
                                snapshot = parsed.get("snapshot", "")
                                diag_msg = f'⚠️ 类目 "{category}" 未找到! 可用类目数: {len(all_items)}'
                                if all_items:
                                    # 列出前5个类目的 label 和 text
                                    item_summaries = []
                                    for it in all_items[:5]:
                                        item_summaries.append(f"[label={it.get('label','?')}, text={it.get('text','?')[:40]}]")
                                    diag_msg += f" | 前5项: {'; '.join(item_summaries)}"
                                if snapshot:
                                    try:
                                        snap_data = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
                                        diag_msg += f" | snapshot count: {snap_data.get('count', '?')}"
                                    except Exception:
                                        pass
                                report_progress(-1, diag_msg)
                    except Exception:
                        pass
                report_progress(50, f'类目选择结果: {result}')

                if not select_success:
                    # 类目选择失败，关闭弹窗并报错
                    await _cdp_eval(ws, """
                        (function(){
                            // 尝试关闭弹窗（点击遮罩或 ESC）
                            var closeBtn = document.querySelector('#modal-breadcrumb-node-dialog .modal-close, #modal-breadcrumb-node-dialog [class*="close"]');
                            if (closeBtn) closeBtn.click();
                            return 'attempted_close';
                        })()
                    """)
                    raise ValueError(
                        f'类目 "{category}" 在 SellerSprite 中未找到匹配项。'
                        f'请确认类目名称正确。调试信息: {result}'
                    )

                # Step 6: 确认类目
                report_progress(53, "正在确认类目选择...")
                confirm_result = await confirm_category(ws)
                report_progress(56, f"类目已确认: {confirm_result}")
            else:
                report_progress(56, "使用默认类目（未指定）")

        # Step 7: 提交搜索
        report_progress(60, "正在提交扫描请求...")
        submit_result = await submit_search(ws)
        report_progress(65, f"搜索已提交: {submit_result}")

        # Step 8: 等待结果加载
        await _wait_loading(ws, 20)
        report_progress(70, "数据加载完成，开始提取...")

        # Step 9: 分页提取数据
        all_records = []
        actual_pages = min(pages, 10)  # 最多扫10页

        for page in range(1, actual_pages + 1):
            if page > 1:
                report_progress(
                    70 + (page - 1) * (25 / actual_pages),
                    f"正在跳转到第 {page}/{actual_pages} 页..."
                )
                await goto_page(ws, page)
                await asyncio.sleep(1.5)

            report_progress(
                70 + page * (25 / actual_pages) - 2,
                f"正在提取第 {page} 页数据..."
            )

            page_records = await extract_current_page(ws)
            normalized = [normalize_record(r) for r in page_records]
            all_records.extend(normalized)

            if page_callback:
                page_callback(page, normalized)

        # 关闭连接
        try:
            await ws.close()
        except Exception:
            pass

        report_progress(100, f"扫描完成! 共 {len(all_records)} 条细分市场数据")

        return {
            "success": True,
            "market": market,
            "category": category,
            "pages_scanned": actual_pages,
            "total_records": len(all_records),
            "records": all_records,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        report_progress(-1, f"扫描失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "market": market,
            "category": category,
            "records": [],
        }


# ── 快捷测试接口 ───────────────────────────────────────────

def test_cdp_connection(cdp_port=None):
    """测试 CDP 连接是否可用"""
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws = loop.run_until_complete(connect(cdp_port))
        result = loop.run_until_complete(_cdp_eval(ws, "navigator.userAgent"))
        loop.run_until_complete(ws.close())
        loop.close()
        return {"success": True, "user_agent": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
