"""
卖家精灵 v3 产品研究 CDP 抓取模块
================================
通过 CDP WebSocket 连接 Chrome 浏览器，
直接从 URL 参数触发搜索，提取产品信息全字段。
"""

import json, re, time, os, asyncio, urllib.request, urllib.parse
from dataclasses import dataclass, asdict, field
from typing import List, Any, Callable

# 延迟导入 websockets，避免与 websocket-client 包冲突
_websockets = None
def _get_websockets():
    global _websockets
    if _websockets is None:
        import websockets as _ws
        _websockets = _ws
    return _websockets

CDP_PORT = 9222
_msg_id = 0


# ============================================================
# 产品数据类（产品研究字段）
# ============================================================

@dataclass
class ProductInfo:
    """产品研究页面字段"""
    rank: int = 0
    asin: str = ""
    title: str = ""
    brand: str = ""
    category_bsr: str = ""
    subcategory_bsr: str = ""
    sales_trend_parent: str = ""
    sales_trend_child: str = ""
    sales_parent: int = 0
    growth_rate: float = 0.0
    sales_amount: float = 0.0
    child_sales: int = 0
    child_sales_amount: float = 0.0
    variant_count: int = 0
    price: float = 0.0
    qa: int = 0
    review_count: int = 0
    monthly_new: int = 0
    rating: float = 0.0
    review_rate: float = 0.0
    fba_margin: float = 0.0
    listing_date: str = ""
    seller_count: int = 0
    delivery: str = ""
    buyer_shipping: float = 0.0
    buybox_seller: str = ""
    lqs: int = 0
    product_weight: str = ""
    product_size: str = ""
    package_weight: str = ""
    package_size: str = ""
    img: str = ""
    product_tags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 数值解析工具
# ============================================================

def _num(s: str) -> float:
    if not s:
        return 0.0
    s = str(s).strip().replace(",", "").replace("%", "").replace("$", "")
    m = re.search(r"[\d.]+", s)
    return float(m.group()) if m else 0.0

def _int(s: str) -> int:
    return int(_num(s))


# ============================================================
# 列头 → 字段名 映射
# ============================================================

_HEADER_MAP = [
    ("大类BSR",              "category_bsr"),
    ("销量趋势(父)",         "sales_trend_parent"),
    ("销量趋势(子)",         "sales_trend_child"),
    ("销量(父)",             "sales_parent"),
    ("增长率",               "growth_rate"),
    ("销售额",               "sales_amount"),
    ("子体销量",             "child_sales"),
    ("子体销售额",           "child_sales_amount"),
    ("变体数",               "variant_count"),
    ("价格",                 "price"),
    ("Q&A",                  "qa"),
    ("评分数",               "review_count"),
    ("月新增",               "monthly_new"),
    ("评分",                 "rating"),
    ("留评率",               "review_rate"),
    ("FBA",                  "fba_margin"),
    ("毛利率",               "fba_margin"),
    ("上架时间",             "listing_date"),
    ("卖家数",               "seller_count"),
    ("配送",                 "delivery"),
    ("买家运费",             "buyer_shipping"),
    ("BuyBox",               "buybox_seller"),
    ("LQS",                  "lqs"),
    ("商品重量",             "product_weight"),
    ("商品尺寸",             "product_size"),
    ("包装重量",             "package_weight"),
    ("包装尺寸",             "package_size"),
]

def _match_field(hdr: str) -> str:
    h = hdr.strip()
    if not h or h in ("#", "操作", "expander", "产品信息"):
        return None
    for kw, field in _HEADER_MAP:
        if kw in h:
            return field
    return None


# ============================================================
# CDP 异步工具
# ============================================================

async def _cdp(ws, method, params=None):
    global _msg_id
    _msg_id += 1
    msg = {"id": _msg_id, "method": method}
    if params:
        msg["params"] = params
    await ws.send(json.dumps(msg))
    while True:
        r = json.loads(await ws.recv())
        if r.get("id") == _msg_id:
            return r


async def _wait_load(ws, timeout=15):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await _cdp(ws, "Runtime.evaluate", {
            "expression": "document.readyState",
            "returnByValue": True
        })
        if r.get("result", {}).get("result", {}).get("value") == "complete":
            return True
        await asyncio.sleep(0.5)
    return False


async def _wait_table(ws, timeout=40):
    """等待数据渲染完成（支持表格视图和卡片视图）"""
    deadline = asyncio.get_event_loop().time() + timeout
    last_row_count = 0
    stable_ticks = 0
    while asyncio.get_event_loop().time() < deadline:
        r = await _cdp(ws, "Runtime.evaluate", {
            "expression": """
            (function(){
                var tableRows = document.querySelectorAll('tr.el-table__row');
                var cards = document.querySelectorAll('div.relation-card');
                var count = Math.max(tableRows.length, cards.length);
                var nodata = document.querySelector('.el-table__empty-block');
                var loading = document.querySelector('.el-loading-mask');
                var lv = loading ? window.getComputedStyle(loading).display !== 'none' : false;
                var loadingHidden = loading ? (window.getComputedStyle(loading).opacity === '0' || window.getComputedStyle(loading).zIndex === '-1') : false;
                return JSON.stringify({rows: count, nodata: !!nodata, loading: lv && !loadingHidden});
            })()
            """,
            "returnByValue": True
        })
        val = r.get("result", {}).get("result", {}).get("value", "{}")
        try:
            info = json.loads(val)
        except:
            info = {}
        row_count = info.get("rows", 0)
        is_loading = info.get("loading", False)

        # 数据行已就绪
        if not is_loading and row_count > 0:
            return row_count
        # 空数据
        if info.get("nodata"):
            return 0
        # loading 遮罩可能卡住，但如果行数据稳定存在 3 次检测，也认为就绪
        if row_count > 0 and row_count == last_row_count:
            stable_ticks += 1
            if stable_ticks >= 3:
                return row_count
        else:
            stable_ticks = 0
        last_row_count = row_count
        await asyncio.sleep(0.8)
    # 超时但有行数据，返回已有的
    return last_row_count


async def _get_headers(ws) -> List[str]:
    r = await _cdp(ws, "Runtime.evaluate", {
        "expression": """
        (function(){
            var tr = document.querySelector('thead tr');
            if(!tr) return '[]';
            var ths = tr.querySelectorAll('th');
            var out = [];
            ths.forEach(function(th){
                var div = th.querySelector('div.cell');
                out.push(div ? div.textContent.trim() : th.textContent.trim());
            });
            return JSON.stringify(out);
        })()
        """,
        "returnByValue": True
    })
    val = r.get("result", {}).get("result", {}).get("value", "[]")
    try:
        return json.loads(val)
    except:
        return []


async def _get_page_items(ws):
    """提取当前页所有产品行（支持卡片视图和表格视图）"""
    r = await _cdp(ws, "Runtime.evaluate", {
        "expression": r"""
        (function(){
            var rows = document.querySelectorAll('tr.el-table__row');
            var cards = document.querySelectorAll('div.relation-card');
            var out = [];

                if(cards.length > 0){
                // ===== 卡片视图 =====
                cards.forEach(function(card){
                    // img: 从 back_img > div.img 提取，并将小图替换为大图
                    var img = '';
                    var imgDiv = card.querySelector('div.back_img > div.img');
                    if(imgDiv){
                        var st = imgDiv.getAttribute('style') || '';
                        var m = st.match(/url\(["']?([^"')]+)/);
                        if(m){
                            img = m[1];
                        }
                    }
                    // asin
                    var asin = '';
                    var asinSpan = card.querySelector('span.asin span.text-black');
                    if(asinSpan) asin = asinSpan.textContent.trim();
                    // title
                    var titleEl = card.querySelector('p.over-ellipsis.text-black');
                    var title = titleEl ? (titleEl.getAttribute('title') || titleEl.textContent.trim()).substring(0, 200) : '';
                    // seller
                    var seller = '';
                    var sellerSpan = card.querySelectorAll('p.flex-center span.text-black.sub-title');
                    if(sellerSpan.length > 0) seller = sellerSpan[0].textContent.trim();
                    // brand
                    var brand = '';
                    if(sellerSpan.length > 1) brand = sellerSpan[1].textContent.trim();
                    // 构造兼容表格视图的 cells 数组 (索引对齐 _parse_row)
                    // cells[0]=ASIN  cells[1]=品牌标签  cells[2]=标题  cells[3]=(空)
                    // cells[4]=BSR  cells[5]=销量趋势  cells[6]=销量(父)  cells[7]=销售额
                    // cells[8]=子体销量  cells[9]=变体数  cells[10]=价格  cells[11]=评分数
                    // cells[12]=评分  cells[13]=FBA  cells[14]=上架时间  cells[15]=配送
                    var flexBetweens = card.querySelectorAll('p.flex-between');
                    // BSR: p.product-rank 里所有 badge，分开存储
                    var bsrMain = '';
                    var bsrSub = '';
                    var rankEls = card.querySelectorAll('p.product-rank .badge');
                    if(rankEls.length > 0){
                        var bsrParts = [];
                        rankEls.forEach(function(badge, idx){
                            var bsrText = badge.textContent.trim().replace('#','');
                            var inSpan = badge.parentElement.querySelector('span:not(.badge)');
                            if(inSpan) bsrParts.push(bsrText + ' in ' + inSpan.textContent.trim());
                            else bsrParts.push(bsrText);
                        });
                        bsrMain = bsrParts[0] || '';
                        bsrSub = bsrParts[1] || '';
                        bsr = bsrParts.join(' / ');
                    }
                    // 销量(父): 第一个 flex-between 的 span.text-main
                    var salesParent = '';
                    var salesChild = '';
                    var salesAmount = '';
                    var variants = '';
                    var price = '';
                    var reviewCount = '';
                    var ratingText = '';
                    var listingDate = '';

                    flexBetweens.forEach(function(fb){
                        var text = fb.textContent.trim();
                        if(text.indexOf('销量(父)') > -1){
                            var sp1 = fb.querySelector('span.text-main.border-bottom');
                            if(sp1) salesParent = sp1.textContent.trim();
                            // 子体销量：同行的第二个 text-main（不是 border-bottom）
                            var allTextMain = fb.querySelectorAll('span.text-main');
                            allTextMain.forEach(function(tm){
                                if(!tm.classList.contains('border-bottom')){
                                    var v = tm.textContent.trim();
                                    if(v && v !== '--' && v !== '-' && !salesChild) salesChild = v;
                                }
                            });
                        } else if(text.indexOf('子体销量') > -1 && text.indexOf('销量(父)') === -1){
                            // 子体销量独立一行
                            var cSpans = fb.querySelectorAll('span.text-main');
                            if(cSpans.length > 0){
                                var cv = cSpans[0].textContent.trim();
                                if(cv && cv !== '--' && cv !== '-') salesChild = cv;
                            }
                        } else if(text.indexOf('销售额') > -1){
                            var sp3 = fb.querySelector('span.text-black.border-bottom');
                            if(sp3) salesAmount = sp3.textContent.trim();
                            var sp4 = fb.querySelector('span.text-black:not(.border-bottom)');
                            if(sp4) variants = sp4.textContent.trim();
                        } else if(text.indexOf('价格') > -1){
                            var priceSpan = fb.querySelector('span span');
                            if(priceSpan) price = priceSpan.textContent.trim();
                            // 评分/评分数
                            var ratingSpans = fb.querySelectorAll('span.el-tooltip');
                            if(ratingSpans.length >= 1) ratingText = ratingSpans[0].textContent.trim();
                            if(ratingSpans.length >= 2) reviewCount = ratingSpans[1].textContent.trim();
                        }
                    });
                    // 上架时间
                    var dateSpans = card.querySelectorAll('span.text-black');
                    dateSpans.forEach(function(ds){
                        var t = ds.textContent.trim();
                        if(/^\d{4}-\d{2}-\d{2}$/.test(t)) listingDate = t;
                    });

                    // 卖家信息：FBA卖家: 1 / AMZ卖家: 16 / NA卖家: 1
                    var sellerCount = 0;
                    var deliveryType = '';
                    var allPSpans = card.querySelectorAll('p.flex-center span, p.flex-center div');
                    allPSpans.forEach(function(sp){
                        var txt = sp.textContent.trim();
                        var m = txt.match(/^(FBA|AMZ|NA)\s*卖家\s*[:：]?\s*(\d+)/);
                        if(m){
                            sellerCount = parseInt(m[2]);
                            deliveryType = m[1];
                        }
                    });

                    // 标签：A+ / V / AC / NR 等
                    var tags = [];
                    var tagEls = card.querySelectorAll('div.tags span.tag, span.badge, span.el-tag');
                    tagEls.forEach(function(te){
                        var tt = te.textContent.trim();
                        if(tt && /^(A\+|V|AC|NR|VC)$/i.test(tt)){
                            tags.push(tt.toUpperCase());
                        }
                    });

                    var cells = [asin, brand, title, '',
                        bsr, '', salesParent, salesAmount,
                        salesChild, variants, price, reviewCount,
                        ratingText, '', listingDate, deliveryType || '', ''];
                    out.push({asin: asin, title: title, img: img, brand: brand, seller: seller,
                              sellerCount: sellerCount, delivery: deliveryType, tags: tags,
                              bsrMain: bsrMain, bsrSub: bsrSub, cells: cells});
                });
            } else {
                // ===== 表格视图（原逻辑）=====
                rows.forEach(function(row){
                    if(row.classList.contains('expanded-row') || row.classList.contains('el-table__expanded-row')) return;
                    var tds = row.querySelectorAll('td');
                    var cells = [];
                    tds.forEach(function(td){
                        var div = td.querySelector('div.cell');
                        cells.push(div ? div.textContent.trim() : td.textContent.trim());
                    });
                    var asin = '';
                    var link = row.querySelector('a[href*="asin"]');
                    if(link){
                        var m = link.getAttribute('href').match(/([A-Z0-9]{10})/);
                        if(m) asin = m[1];
                    }
                    if(!asin){
                        var m2 = row.textContent.match(/\b([A-Z0-9]{10})\b/);
                        if(m2) asin = m2[1];
                    }
                    var title = cells.length > 3 ? cells[3].substring(0, 200) : '';
                    out.push({asin: asin, title: title, img: '', cells: cells});
                });
            }
            return JSON.stringify({count: out.length, items: out});
        })()
        """,
        "returnByValue": True
    })
    val = r.get("result", {}).get("result", {}).get("value", "{}")
    try:
        return json.loads(val)
    except:
        return {"count": 0, "items": []}


async def _get_pager(ws):
    r = await _cdp(ws, "Runtime.evaluate", {
        "expression": r"""
        (function(){
            var p = document.querySelector('.el-pagination');
            if(!p) return '{}';
            var t = p.querySelector('.el-pagination__total');
            var btns = p.querySelectorAll('li.number');
            var maxBtn = 0;
            btns.forEach(function(b){
                var n = parseInt(b.textContent);
                if(n > maxBtn) maxBtn = n;
            });
            return JSON.stringify({total: t ? parseInt(t.textContent.replace(/\D/g,'')) : 0, maxBtn: maxBtn});
        })()
        """,
        "returnByValue": True
    })
    val = r.get("result", {}).get("result", {}).get("value", "{}")
    try:
        return json.loads(val)
    except:
        return {"total": 0, "maxBtn": 0}


# ============================================================
# 数据解析
# ============================================================

def _set_field(p: ProductInfo, field: str, text: str):
    """向 ProductInfo 字段赋值"""
    if not text or text == "--" or text == "-":
        return
    v = text.strip()

    if field == "category_bsr":
        p.category_bsr = v
    elif field == "sales_trend_parent":
        p.sales_trend_parent = v
    elif field == "sales_trend_child":
        p.sales_trend_child = v
    elif field == "sales_parent":
        p.sales_parent = _int(v)
    elif field == "growth_rate":
        m = re.search(r"([+-]?[\d.]+)%", v)
        p.growth_rate = float(m.group(1)) if m else _num(v)
    elif field == "sales_amount":
        m = re.search(r"\$?([\d,]+\.?\d*)", v)
        p.sales_amount = float(m.group(1).replace(",","")) if m else _num(v)
    elif field == "child_sales":
        p.child_sales = _int(v)
    elif field == "child_sales_amount":
        m = re.search(r"\$?([\d,]+\.?\d*)", v)
        p.child_sales_amount = float(m.group(1).replace(",","")) if m else _num(v)
    elif field == "variant_count":
        p.variant_count = _int(v)
    elif field == "price":
        m = re.search(r"\$?([\d,]+\.?\d*)", v)
        p.price = float(m.group(1).replace(",","")) if m else _num(v)
    elif field == "qa":
        p.qa = _int(v)
    elif field == "review_count":
        p.review_count = _int(v)
    elif field == "monthly_new":
        p.monthly_new = _int(v)
    elif field == "rating":
        m = re.search(r"([\d.]+)", v)
        p.rating = float(m.group(1)) if m else _num(v)
    elif field == "review_rate":
        m = re.search(r"([\d.]+)%", v)
        p.review_rate = float(m.group(1)) if m else _num(v)
    elif field == "fba_margin":
        m = re.search(r"([\d.]+)%", v)
        p.fba_margin = float(m.group(1)) if m else _num(v)
    elif field == "listing_date":
        p.listing_date = v
    elif field == "seller_count":
        p.seller_count = _int(v)
    elif field == "delivery":
        p.delivery = v
    elif field == "buyer_shipping":
        m = re.search(r"([\d,]+\.?\d*)", v)
        p.buyer_shipping = float(m.group(1).replace(",","")) if m else _num(v)
    elif field == "buybox_seller":
        p.buybox_seller = v
    elif field == "lqs":
        p.lqs = _int(v)
    elif field == "product_weight":
        p.product_weight = v
    elif field == "product_size":
        p.product_size = v
    elif field == "package_weight":
        p.package_weight = v
    elif field == "package_size":
        p.package_size = v


def _parse_row(idx: int, asin: str, title: str,
               img: str = "", headers: List[str] = None, cells: List = None) -> ProductInfo:
    if headers is None:
        headers = []
    if cells is None:
        cells = []
    """
    解析一行数据到 ProductInfo。
    基于实际 DOM 结构（17个td）：

    td[0] = expander
    td[1] = 序号
    td[2] = 品牌标签
    td[3] = 完整标题
    td[4] = "18,523  3,230  15%"  → 大类BSR数 + 增长率
    td[5] = 销量趋势(子)
    td[6] = "504 -4.79%"          → 销量(父) + 增长率
    td[7] = "$24,187"             → 销售额
    td[8] = 子体销量/子体销售额
    td[9] = "4"                   → 变体数
    td[10] = "$47.99 -"           → 价格 / Q&A
    td[11] = "917  28"            → 评分数 / 月新增
    td[12] = "4.4  5.56%"        → 评分 / 留评率
    td[13] = "$12.64  59%"       → FBA费用 / 毛利率
    td[14] = "2025-12-02 5个月"  → 上架时间
    td[15] = "FBA -"              → 配送 / 买家运费
    td[16] = 操作列
    """
    p = ProductInfo()
    p.rank = idx + 1
    p.asin = asin
    p.title = title
    p.img = img

    # 固定位置解析辅助
    def _get(ci: int) -> str:
        if ci < len(cells):
            return cells[ci]
        return ""

    # td[1]: 品牌
    brand_cell = _get(1) if len(cells) > 1 else ""
    if brand_cell and brand_cell not in ("--", "-"):
        p.brand = brand_cell

    # 辅助：把含多个值的格子拆分
    def _split_cell(text: str) -> List[str]:
        """按非断行空格(\xa0)或Tab分割，支持双空格"""
        if not text:
            return []
        # 先处理双空格/双\xa0，再处理单\xa0或Tab
        parts = re.split(r'(?:\xa0|\t){2,}', text)
        result = []
        for p in parts:
            # 每个片段内部再按单个 \xa0 或 Tab 拆分
            sub = re.split(r'(?:\xa0|\t)', p)
            result.extend([x.strip() for x in sub if x.strip() and x.strip() != '--'])
        return result

    # 固定位置解析（根据诊断结果）
    # td[4]: 大类BSR + 增长率
    cell4 = _get(4)
    parts4 = _split_cell(cell4)
    if parts4:
        p.category_bsr = parts4[0]
        # 如果最后一个值含%，是增长率
        for part in reversed(parts4):
            m = re.search(r"([+-]?[\d.]+)%", part)
            if m:
                p.growth_rate = float(m.group(1))
                break

    # td[5]: 销量趋势(子)
    cell5 = _get(5)
    if cell5 and cell5 not in ("--", "-"):
        p.sales_trend_child = cell5

    # td[6]: 销量(父) + 增长率
    cell6 = _get(6)
    parts6 = _split_cell(cell6)
    if parts6:
        p.sales_parent = _int(parts6[0])
        for part in parts6:
            m = re.search(r"([+-]?[\d.]+)%", part)
            if m:
                p.growth_rate = float(m.group(1))
                break

    # td[7]: 销售额
    cell7 = _get(7)
    if cell7 and cell7 not in ("--", "-"):
        p.sales_amount = _num(cell7)

    # td[8]: 子体销量 / 子体销售额
    cell8 = _get(8)
    parts8 = _split_cell(cell8)
    if len(parts8) >= 1:
        p.child_sales = _int(parts8[0])
    if len(parts8) >= 2:
        p.child_sales_amount = _num(parts8[1])

    # td[9]: 变体数
    cell9 = _get(9)
    if cell9 and cell9 not in ("--", "-"):
        p.variant_count = _int(cell9)

    # td[10]: 价格 / Q&A
    cell10 = _get(10)
    parts10 = _split_cell(cell10)
    if parts10:
        for part in parts10:
            if "$" in part or re.search(r"[\d.]+$", part):
                if p.price == 0:
                    p.price = _num(part)
            elif re.search(r"^\d+$", part.strip()):
                if p.qa == 0:
                    p.qa = _int(part)

    # td[11]: 评分数 / 月新增
    cell11 = _get(11)
    parts11 = _split_cell(cell11)
    if parts11:
        nums11 = [_int(x) for x in parts11]
        if len(nums11) >= 1:
            p.review_count = nums11[0]
        if len(nums11) >= 2:
            p.monthly_new = nums11[1]

    # td[12]: 评分 / 留评率
    cell12 = _get(12)
    parts12 = _split_cell(cell12)
    for part in parts12:
        if "%" in part:
            m = re.search(r"([\d.]+)%", part)
            if m:
                p.review_rate = float(m.group(1))
        elif re.search(r"[\d.]+", part) and p.rating == 0.0:
            p.rating = _num(part)

    # td[13]: FBA费用 / 毛利率
    cell13 = _get(13)
    parts13 = _split_cell(cell13)
    for part in parts13:
        if "%" in part:
            m = re.search(r"([\d.]+)%", part)
            if m:
                p.fba_margin = float(m.group(1))
        elif "$" in part and p.fba_margin == 0.0:
            p.fba_margin = _num(part)

    # td[14]: 上架时间
    cell14 = _get(14)
    if cell14 and cell14 not in ("--", "-"):
        p.listing_date = cell14

    # td[15]: 配送 / 买家运费
    cell15 = _get(15)
    parts15 = _split_cell(cell15)
    if parts15:
        p.delivery = parts15[0]
        for part in parts15[1:]:
            if part and part not in ("-", "--"):
                p.buyer_shipping = _num(part)
                break

    return p


# ============================================================
# URL 构建
# ============================================================

def build_url(keyword, market="US", page=1, size=60):
    params = {
        "market": market,
        "page": str(page),
        "size": str(size),
        "symbolFlag": "true",
        "monthName": "bsr_sales_nearly",
        "selectType": "4",
        "filterSub": "false",
        "weightUnit": "g",
        "order[field]": "amz_unit",
        "order[desc]": "true",
        "productTags": "[]",
        "sellerTypes": "[]",
        "eligibility": "[]",
        "pkgDimensionTypeList": "[]",
        "sellerNationList": "[]",
        "keywords": keyword,
        "lowPrice": "N",
        "video": "",
        "nodeIdPaths": "[]",
    }
    base = "https://www.sellersprite.com/v3/product-research"
    return f"{base}?{urllib.parse.urlencode(params)}"


# ============================================================
# 异步主流程
# ============================================================

async def _scrape_impl(keyword, market, pages, page_callback, progress_callback):
    def report(pct, msg):
        if progress_callback:
            progress_callback(pct, msg)

    report(5, "连接CDP...")
    try:
        tabs = json.loads(urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=5).read())
    except Exception as e:
        raise RuntimeError(f"无法连接 CDP ({CDP_PORT}): {e}")

    ws_url = None
    for t in tabs:
        url = t.get("url", "")
        ws_url = t.get("webSocketDebuggerUrl")
        if ws_url and ("sellersprite" in url or "product-research" in url):
            break
    if not ws_url:
        ws_url = tabs[0]["webSocketDebuggerUrl"]

    all_rows = []
    all_headers = []
    seen_asins = set()  # 用于去重

    async with _get_websockets().connect(ws_url, max_size=20*1024*1024) as ws:
        await _cdp(ws, "Page.enable", {})

        for page_num in range(1, pages + 1):
            url = build_url(keyword, market, page_num)
            print(f"  [卖家精灵] 开始抓取第 {page_num}/{pages} 页...")
            report(15 + (page_num - 1) * 70 // max(pages, 1), f"打开第 {page_num} 页...")

            await _cdp(ws, "Page.navigate", {"url": url})
            await asyncio.sleep(3)
            loaded = await _wait_load(ws, timeout=20)
            # SPA 页面 readyState=complete 不代表数据渲染完成，额外等待
            await asyncio.sleep(3)
            row_count = await _wait_table(ws, timeout=40)

            if row_count == 0:
                print(f"  [卖家精灵] 第 {page_num} 页无数据，停止抓取")
                break

            headers = await _get_headers(ws)
            data = await _get_page_items(ws)
            pager = await _get_pager(ws)

            if not all_headers and headers:
                all_headers = headers

            items = data.get("items", [])
            # 抓完一页立即打印该页每条数据
            for seq, item in enumerate(items, 1):
                asin = item.get("asin", "")
                title = item.get("title", "")[:55]
                cells = item.get("cells", [])
                # 从 cells 提取关键字段打印
                price = ""
                bsr = ""
                sales = ""
                rating = ""
                reviews = ""
                if len(cells) > 10: price = cells[10]
                if len(cells) > 4: bsr = cells[4]
                if len(cells) > 6: sales = cells[6]
                if len(cells) > 12: rating = cells[12]
                if len(cells) > 11: reviews = cells[11]
                print(f"  [p{page_num}|{seq:2d}] ASIN={asin} BSR={bsr[:20]} Sales={sales[:15]} Price={price} Rating={rating} Reviews={reviews}")
                print(f"         {title}")

            # 检查去重
            page_asins = set()
            new_items = []
            for item in items:
                asin = item.get("asin", "")
                if asin and asin not in seen_asins and asin not in page_asins:
                    page_asins.add(asin)
                    seen_asins.add(asin)
                    new_items.append(item)

            print(f"  ✅ 第 {page_num}/{pages} 页完成，提取 {len(items)} 条数据，累计 {len(all_rows) + len(new_items)} 条")
            report(50 + page_num * 40 // max(pages, 1), f"第 {page_num} 页提取到 {len(items)} 条")

            # 如果本页无新数据，说明到头了，停止翻页
            if len(new_items) == 0:
                print(f"  [卖家精灵] 第 {page_num} 页无新数据，停止抓取")
                break

            # 如果本页新数据很少（< 10%），说明数据重复了，停止翻页
            if page_num > 1 and len(new_items) < len(items) * 0.1 and len(items) > 0:
                print(f"  [卖家精灵] 本页 {len(items)} 条中仅 {len(new_items)} 条新数据（重复率 > 90%），提前停止翻页")
                break

            for item in new_items:
                all_rows.append({
                    "page":  page_num,
                    "asin":  item.get("asin",  ""),
                    "title": item.get("title", ""),
                    "img":   item.get("img",   ""),
                    "brand": item.get("brand", ""),
                    "seller": item.get("seller", ""),
                    "sellerCount": item.get("sellerCount", 0),
                    "delivery": item.get("delivery", ""),
                    "tags":  item.get("tags",  []),
                    "cells": item.get("cells", []),
                })

            # 实时推送该页数据（去重后仅新数据）
            if page_callback:
                page_callback(page_num, new_items)

            if pager.get("total"):
                report(60 + page_num * 40 // max(pages, 1),
                       f"  总计: {pager['total']} 条，最大页: {pager.get('maxBtn')}")

            if len(new_items) == 0:
                break

            # 智能停止：如果本页数据量很少（<10条），说明可能已到最后一页，提前停止
            if len(new_items) < 10 and page_num > 1:
                print(f"  [卖家精灵] 本页仅 {len(new_items)} 条（< 10），提前停止翻页")
                break

            if page_num < pages:
                await asyncio.sleep(1.5)

    return {"rows": all_rows, "headers": all_headers}


# ============================================================
# 同步入口
# ============================================================

def scrape_sellersprite_product(
    keyword: str = "",
    market: str = "US",
    pages: int = 4,
    progress_callback: Callable = None,
    page_callback: Callable = None,
) -> dict:
    """
    抓取卖家精灵 v3 产品研究数据
    page_callback(page_num, page_items): 每抓完一页调用，用于实时推送
    返回字段: 大类BSR / 销量趋势(父/子) / 销量(父) / 增长率 / 销售额 /
              子体销量 / 子体销售额 / 变体数 / 价格 / Q&A / 评分数 / 月新增 /
              评分 / 留评率 / FBA毛利率 / 上架时间 / 卖家数 / 配送 /
              买家运费 / BuyBox卖家 / LQS / 商品重量 / 商品尺寸 / 包装重量 / 包装尺寸
    """
    result = {
        "success": False,
        "products": [],
        "total": 0,
        "error": None,
        "pages_scraped": 0,
        "headers_raw": [],
        "filepath": None,
    }

    try:
        data = asyncio.run(_scrape_impl(keyword, market, pages, page_callback, progress_callback))
        rows = data.get("rows", [])
        all_headers = data.get("headers", [])

        if not rows:
            result["error"] = "未获取到任何数据，请检查：1) Chrome 已开启调试模式 2) 已登录卖家精灵 3) 关键词有对应产品"
            return result

        # 解析每行
        products = []
        for idx, row in enumerate(rows):
            p = _parse_row(
                idx,
                row.get("asin", ""),
                row.get("title", ""),
                row.get("img", ""),
                all_headers,
                row.get("cells", [])
            )
            # 补充卡片视图特有字段
            if row.get("brand") and not p.brand:
                p.brand = row["brand"]
            if row.get("seller"):
                p.buybox_seller = row["seller"]
            if row.get("sellerCount"):
                p.seller_count = row["sellerCount"]
            if row.get("delivery"):
                p.delivery = row["delivery"]
            if row.get("tags"):
                p.product_tags = row["tags"]
                if row.get("bsrMain"): p.category_bsr = row["bsrMain"]
                if row.get("bsrSub"): p.subcategory_bsr = row["bsrSub"]
                products.append(p)

        result["products"] = products
        result["total"] = len(products)
        result["headers_raw"] = all_headers
        result["pages_scraped"] = pages
        result["success"] = True

        if progress_callback:
            progress_callback(100, f"完成！共获取 {len(products)} 条")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        result["error"] = f"{type(e).__name__}: {e}\n\n完整堆栈:\n{tb}"
        result["_traceback"] = tb  # 完整堆栈单独存

    return result


# 向后兼容
def scrape_sellersprite_keywords(keyword="", market="US", pages=4,
                                  node_ids=None, progress_callback=None,
                                  page_callback=None):
    return scrape_sellersprite_product(
        keyword=keyword, market=market, pages=pages,
        progress_callback=progress_callback,
        page_callback=page_callback,
    )


def test_cdp_connection() -> dict:
    try:
        tabs = json.loads(urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=3).read())
        return {"success": True, "tabs": len(tabs), "ws": tabs[0].get("webSocketDebuggerUrl","")}
    except Exception as e:
        return {"success": False, "error": str(e)}


SELLERSPRITE_URL = "https://www.sellersprite.com/v3/product-research?market=US"

SITE_MAP = {
    "US": "美国", "UK": "英国", "DE": "德国", "FR": "法国",
    "IT": "意大利", "ES": "西班牙", "JP": "日本", "CA": "加拿大",
    "IN": "印度", "MX": "墨西哥", "BR": "巴西", "AU": "澳洲",
    "AE": "阿联酋", "SA": "沙特",
}
