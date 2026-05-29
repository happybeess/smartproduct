## 一、项目架构总览

```
┌─────────────────────────────────────────────────────┐
│                    用户浏览器                         │
│    ┌─────────────────────────────────────────────┐   │
│    │  selection_system.html (SPA, 8858行, 原生JS)  │   │
│    │  · 12个功能面板 (Tab 切换)                     │   │
│    │  · SSE EventSource 实时流式接收               │   │
│    │  · SheetJS Excel 导出                        │   │
│    └──────────────┬──────────────────────────────┘   │
└────────────────  ─┼───────────────────────────────────┘
                    │ HTTP / SSE (EventSource)
┌────────────────  ─▼───────────────────────────────────┐
│               Flask Web Server (app.py)               │
│          · 3812行主入口 · 32+ API 端点                 │
│      · JWT-like Token 认证 + RBAC 权限控制            │
│           · SSE 流式响应 (Queue + Thread)            │
│            · SQLite 四库分离缓存层                   │
├──────────┬──────────┬──────────┬──────────┬─────────┤
│ ranking/ │ amazon/  │  ebay/   │ hot_     │  DB/    │
│ 榜单扫描  │ Amazon模块│ eBay模块  │interest/ │ 缓存库  │
│          │          │          │ 趋势模块  │         │
└────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬────┘
     │          │          │         │          │
 ┌───▼──┐ ┌────▼───┐ ┌────▼───┐ ┌──▼──┐ ┌────▼────┐
 │CDP WS│ │Playwrt │ │eBay API│ │SerpAPI│ │SQLite ×4│
 │Chrome│ │Stealth │ │Browse  │ │GraphQL│ │4库分离  │
 └──────┘ │        │ │Finding │ │       │ │TTL分级  │
 ┌──────┐ └────────┘ │Research│ │Product │ └────────┘
 │Alpha-│             │Reviews │ │Hunt   │
 │Shop  │             └────────┘ └───┬───┘
 │代理   │                          │
 └──────┘                   ┌──────▼────────┐
                            │  AI 分析引擎   │
                            │ DeepSeek(主)   │
                            │ 豆包ARK(备)    │
                            │ OpenAI(兜底)   │
                            └───────────────┘
```

---

## 二、后端技术栈

### 2.1 核心框架与运行时

| 技术 | 版本 | 用途 |
|------|------|------|
| **Python** | 3.14.3 | 主要开发语言 |
| **Flask** | >=2.0.0 | Web 框架，路由/Middleware/模板 |
| **python-dotenv** | >=1.0.0 | `.env` 环境变量管理 |
| **SQLite3** | 内置标准库 | 数据持久化 / 缓存存储 |
| **threading** | 标准库 | SSE 流式推送子线程 |

### 2.2 数据采集引擎

| 技术 | 用途 | 应用场景 |
|------|------|---------|
| **CDP WebSocket** (`websockets`) | 直连 Chrome DevTools Protocol，复用已登录会话 | 榜单扫描、Amazon 评论抓取、卖家精灵数据提取 |
| **Playwright** + **playwright-stealth** | 无头浏览器自动化，注入反检测脚本 | Amazon/eBay 商品搜索爬虫、竞品详情抓取 |
| **Requests** | HTTP 客户端 | eBay API 调用、第三方服务对接 |
| **BeautifulSoup4** | HTML 解析 | Amazon/eBay 评论页面解析 |

### 2.3 第三方 API 集成

#### 电商平台 API

| API | 类型 | 用途 | 认证方式 |
|-----|------|------|---------|
| **Amazon SP-API v0** | REST (AWS SigV4) | Catalog Items 目录查询 | LWA OAuth + AWS 签名 |
| **eBay Browse API** | REST (OAuth2) | 商品搜索 / 详情查询 | Client Credentials Grant |
| **eBay Finding API** | REST | findItemsAdvanced 关键词搜索 | App ID |
| **eBay Research API** | REST | 已售/在售数据分析 | CDP Cookie 提取 |
| **AlphaShop Ranking** | REST (内网代理) | 排行榜产品数据（热销/新品/飙升/创新） | 无需认证 |

#### 数据与分析 API

| API | 用途 |
|-----|------|
| **Product Hunt GraphQL** | 全球热门产品趋势发现 |
| **Google Trends (pytrends/SerpAPI)** | 关键词热度趋势 / 兴趣度时间序列 |
| **DeepSeek API (v4-flash)** | 评论 AI 情感分析（首选） |
| **火山方舟 豆包 (doubao-pro-32k)** | 评论 AI 分析（降级备选） |
| **OpenAI API (gpt-4o-mini)** | 评论 AI 分析（兜底） |
| **运德物流 WeDoExpress API** | 实时国际运费查询 |
| **百度翻译 API** | 多语言翻译 |
| **open.er-api.com** | USD/CNY 实时汇率 |

### 2.4 数据库设计 — SQLite 四库分离

| 库文件 | 职责 | 核心表 | 缓存 TTL |
|--------|------|--------|----------|
| `users.db` | 用户认证 + 产品收藏库 | users, user_tokens, user_products | 永久 |
| `ebay_cache.db` | eBay 全量数据缓存 | ebay_search_cache, sellersprite_cache, reviews_cache, review_analysis_cache | 1~30天 |
| `trends.db` | Google Trends 日级缓存 | daily_data | 按日去重 |
| `products.db` | Product Hunt 缓存 | daily_data | 按日去重 |

**缓存策略亮点**：
- eBay 搜索 → **30 天**
- SellerSprite/OE → **1 天**
- 商品详情 → **7 天**
- Amazon/eBay 评论 → **3 天**
- AI 分析结果 → **7 天**

---

## 三、前端技术栈

### 3.1 技术选型

| 维度 | 选择 | 说明 |
|------|------|------|
| **架构模式** | 单页应用 (SPA) | Tab 面板切换，无页面跳转 |
| **UI 框架** | 原生 JavaScript (ES6+) | 无 React/Vue/Angular |
| **CSS 方案** | 手写 CSS-in-HTML | Tailwind 风格工具类，内联 `<style>` 块 |
| **构建工具** | 零构建依赖 | 直接浏览器打开运行 |
| **外部 JS 库** | SheetJS (xlsx@0.18.5) | Excel 导出（唯一外部依赖） |
| **代码规模** | 8858 行 HTML（其中 CSS ~1366 行, JS ~5915 行, 4 个 script 块）|

### 3.2 前后端通信协议

| 协议 | 使用场景 | 说明 |
|------|---------|------|
| **REST API** (`fetch`) | 常规 CRUD 操作 | 32+ 个端点，JSON 请求/响应 |
| **SSE** (`EventSource`) | 耗时爬虫操作实时反馈 | page(逐页)、progress(进度)、done(完成)、error(错误) 四种事件 |
| **统一响应格式** | `{ success: bool, data/error, cached: bool }` | 所有 API 遵循此规范 |

### 3.3 功能模块清单（12 个面板）

| 面板 ID | 功能名称 | 核心能力 |
|----------|---------|---------|
| `loginPage` | 登录认证 | Token 登录 + 角色判断 |
| `page-dashboard` | 仪表盘 | Google Trends 热点 + Product Hunt 每日精选 |
| `page-priceCheck` | 利润计算器 | 运费查询 + 多仓对比 + 汇率换算 + 利润率计算 |
| `page-rankScan` | 选品扫描 | SellerSprite CDP 榜单扫描 + 类目树导航 + 关键词搜索 + 18列数据表格 |
| `page-insight` | 竞品洞察 | Amazon ASIN 解析 + eBay ItemID 解析 + 竞品详情展示 |
| `page-kwCloud` | 关键词云图 | 多关键词趋势对比可视化 |
| `page-kwAmazon` | Amazon 关键词搜索 | Playwright 搜索 + CDP 自动补全 + SSE 流式结果 |
| `page-kwEbay` | eBay 关键词搜索 | Browse/Finding API 搜索 + SSE 流式结果 |
| `page-productLibrary` | 产品收藏库 | CRUD 管理 + 分类标签 |
| `page-reviewAnalysis` | 评论 AI 分析 | Amazon/eBay 评论抓取 + DeepSeek 智能分析 + 星评筛选 |
| `page-userManagement` | 用户管理 | CRUD + 角色分配（管理员专属） |
| `page-inspiration` | TikTok 灵感推荐 | TikTok 爆款趋势追踪 |

---

## 四、核心技术难点与解决方案

### 4.1 CDP 浏览器自动化 — 反爬对抗的核心方案

**问题**：Amazon/eBay 有严格的反爬机制（EdgeX 验证、WAF 拦截、Cookie 过期），传统 requests/scrapy 无法应对。

**方案**：双引擎架构

```
引擎一：CDP WebSocket 直连（轻量级）
┌──────────┐    WebSocket    ┌──────────────────────┐
│ Python   │ ◄───────────── │ Chrome (已登录状态)    │
│ websockets│   CDP Commands │ Port 9222 Debug Mode   │
│          │                │ 复用登录态/Cookie      │
└──────────┘                └──────────────────────┘
适用场景：榜单扫描、评论抓取、卖家精灵数据提取

引擎二：Playwright + Stealth（完整模拟）
┌──────────────────────┐
│ Chromium (Headless)   │
│ ├─ navigator.webdriver = undefined
│ ├─ User-Agent 随机化
│ └─ WebGL/Canvas 指纹伪装
└──────────────────────┘
适用场景：商品搜索、竞品详情、购买历史
```

### 4.2 SSE 流式推送 — 解决长时间爬虫的用户体验

**问题**：一次榜单扫描可能需要 5~10 分钟，同步等待用户体验极差。

**方案**：Flask Generator + Queue 多线程模型

```python
# 伪代码示意
_sse_queues = {}           # task_id → threading.Queue

def stream_scan(task_id):
    # 主线程：Flask generator 产出生成器
    def generate():
        while True:
            event = _sse_queues[task_id].get()  # 阻塞等待
            yield f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"
            if event.type == 'done': break
    return Response(generate(), mimetype='text/event-stream')

# 子线程：执行耗时爬虫
def scan_worker(task_id):
    for page_data in crawler.scan():
        _sse_push(task_id, 'page', page_data)    # 通过 Queue 回传
    _sse_push(task_id, 'done', final_result)

Thread(target=scan_worker, args=(task_id,), daemon=True).start()
```

### 4.3 多级 AI 降级 — 保证服务可用性

```
DeepSeek (deepseek-v4-flash)
    ↓ 失败/超时 (5s timeout)
豆包 ARK (doubao-pro-32k)
    ↓ 失败/超时
OpenAI (gpt-4o-mini)
    ↓ 失败
返回兜底文本 "AI 服务暂不可用"
```

每级独立超时控制 + 异常捕获，确保单点故障不影响整体流程。

### 4.4 榜单数据双维度评估算法

**市场级筛选（9 项硬指标）**：
| 维度 | 淘汰阈值 | 含义 |
|------|---------|------|
| 月销量下限 | < 500 | 市场太小不值得进入 |
| 商品集中度 | > 60% | 头部垄断，新卖家无机会 |
| 品牌集中度 | > 70% | 品牌壁垒过高 |
| 卖家集中度 | > 70% | 卖家垄断 |
| A+ 占比 | < 30% | 竞争对手 FBA 优势不明显 |
| 新品占比 | < 2% | 新品入场窗口关闭 |
| 平均评分 | < 3.5 | 品质风险高 |
| 退货率 | > 10% | 售后成本过高 |
| 毛利率 | < 25% | 利润空间不足 |

**产品级增长评分（6 维加权）**：
销量趋势、价格竞争力、评分趋势、竞争密度、新品机会、利润空间 → 加权求和得到综合得分

**最终判定**：pass（通过）/ observe（观察）/ eliminate（淘汰）

---

## 五、系统部署架构

### 5.1 本地 Windows 单机部署

```
启动流程 (start.bat):

[1] Chrome --remote-debugging-port=9222
    └── 提供调试端口供 Python WebSocket 连接

[2] python -X utf8 app.py (port 8006)
    ├── 智能代理检测 (_check_proxy_alive)
    ├── 加载 .env 配置 (44项环境变量)
    ├── 注册 ranking/amazon/ebay 模块路由
    └── Flask threaded=True 多线程启动

[3] 打开浏览器 http://localhost:8006
```

### 5.2 关键环境变量（.env）

| 分类 | 示例变量 | 数量 |
|------|---------|------|
| API 密钥 | DEEPSEEK_API_KEY, SERPAPI_KEY, EBAY_* 等 | ~15 |
| 代理配置 | HTTPS_PROXY, ALL_PROXY | ~3 |
| 数据库路径 | DB_PATH, USERS_DB | ~3 |
| 运德物流 | WEDO_APP_ID, WEDO_SECRET_KEY | ~6 |
| AI 配置 | AI_PROVIDER, AI_MODEL, AI_BASE_URL | ~5 |
| 其他 | PORT, DEBUG_MODE, ADMIN_USERNAME 等 | ~12 |

---

## 六、技术亮点（面试重点）

### 可以深入聊的方向：

1. **CDP vs Playwright 的选型依据**
   - 何时选择 CDP 直连？何时选择 Playwright？
   - 各自的优劣势和适用边界是什么？
   - 如何处理 Chrome 版本升级导致的 CDP 协议变更？

2. **SSE 流式架构的设计考量**
   - 为什么不选 WebSocket？（单向推送足够，SSE 更简单）
   - Queue 线程安全如何保证？
   - 如何处理客户端断连后的资源清理？

3. **反爬对抗经验**
   - EdgeX 验证码的绕过策略
   - playwright-stealth 的检测点列表
   - Cookie 有效期管理和自动续期
   - IP 代理的故障转移机制

4. **缓存分层设计思路**
   - 为什么选择 4 个 SQLite 而不是 1 个？
   - 不同业务数据的 TTL 如何确定？
   - 缓存穿透/击穿/雪崩的预防？

5. **多 AI 提供商降级链**
   - 各模型的调用延迟和成本对比
   - Prompt 统一层如何屏蔽底层差异？
   - 降级的熔断策略（连续失败 N 次后暂停？）

6. **前端零构建方案的取舍**
   - 为什么不用 Vue/React？（快速迭代优先、团队规模小）
   - 8858 行单文件的维护挑战和应对
   - 组件化改造的渐进路径

---

## 七、可改进方向（展现思考深度）

| 方向 | 当前状态 | 改进建议 |
|------|---------|---------|
| **ORM** | 原生 sqlite3 SQL 字符串拼接 | 引入 SQLAlchemy 或 Peewee，防 SQL 注入 |
| **密码安全** | SHA-256 单次哈希 | 升级为 bcrypt/argon2，加盐迭代 |
| **容器化** | 仅支持 Windows 本地部署 | Docker Compose 一键部署（Flask + Chrome + Redis） |
| **任务队列** | threading.Thread 内存队列 | 升级 Celery + Redis，支持任务持久化和重试 |
| **前端工程化** | 8858 行单 HTML 文件 | 拆分为 ES Module 或迁移到 Vite + Vue3 |
| **API 文档** | 无 Swagger/OpenAPI | 引入 Flask-RESTX 或 Redoc 自动生成 |
| **单元测试** | 几乎为零 | pytest + 覆盖核心爬虫逻辑和筛选算法 |
| **监控告警** | 仅 print 日志 | 引入结构化日志 (structlog) + Prometheus metrics |
| **requirements.txt** | 仅声明 8 项依赖 | 补齐全部隐式依赖，加 pip-tools lock |
| **生产服务器** | Flask 内置 dev server | Gunicorn + gevent worker + Nginx 反代 |

---

## 八、项目统计一览

| 指标 | 数值 |
|------|------|
| **Python 总行数** | ~19,571 行（10 个核心 .py 文件） |
| **前端总行数** | 8,858 行 HTML（CSS 1,366 + JS 5,915） |
| **API 端点数** | 32+ 个 REST + SSE 接口 |
| **数据库数量** | 4 个 SQLite 库（按职责分离） |
| **第三方 API** | 14 个（电商 5 + 数据 3 + AI 3 + 工具 3） |
| **功能模块** | 12 个前端面板 + 3 大后端业务模块 |
| **并发模型** | threading.Thread + ThreadPoolExecutor + asyncio（混合使用） |
| **缓存策略** | 4 级过期（1天/3天/7天/30天） |
| **AI 降级链** | 3 级（DeepSeek → 豆包 → OpenAI） |
| **浏览器自动化** | 2 种引擎（CDP WebSocket + Playwright Stealth） |
