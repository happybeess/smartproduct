# -*- coding: utf-8 -*-
"""
榜单模块 - Flask 路由
提供扫描榜单的 API 接口，支持同步和 SSE 流式两种模式
"""

import json
import uuid
import queue
import threading
from flask import jsonify, Response, request

# 导入本模块核心组件
from ranking.cdp_rank_scanner import (
    scan_rank, test_cdp_connection,
    CATEGORIES, MARKETS,
    fetch_keyword_suggestions, select_keyword_suggestion,
)
from ranking.rank_filters import batch_filter, score_growth, rank_records


# ── 路由: 获取可用类目列表 ──────────────────────────────────

def get_categories():
    """返回支持的类目列表"""
    return jsonify({
        'success': True,
        'categories': CATEGORIES,
        'markets': MARKETS,
    })


# ── 路由: 测试 CDP 连接 ─────────────────────────────────────

def test_connection():
    """测试 CDP 连接状态"""
    cdp_port = int(request.args.get('cdp_port', 9222))
    result = test_cdp_connection(cdp_port)
    return jsonify(result)


# ── 路由: 同步扫描（简单场景用） ────────────────────────────

def scan_sync():
    """
    同步扫描接口（超时风险较高，仅用于快速测试）
    生产环境推荐使用 /stream 接口
    """
    import asyncio

    market = request.args.get('market', 'US').upper()
    category = request.args.get('category', '').strip() or None
    pages = int(request.args.get('pages', 1))
    cdp_port = int(request.args.get('cdp_port', 9222))

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            scan_rank(market=market, category=category, pages=pages, cdp_port=cdp_port)
        )
        loop.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ── 路由: SSE 流式扫描 ⭐ ───────────────────────────────────

def stream_scan(sse_push_fn, sse_queues, sse_lock):
    """
    SSE 流式扫描接口工厂函数
    接收外部传入的 sse_push 函数和队列/锁对象（从 app.py 主模块共享）

    Args:
        sse_push_fn: _sse_push(task_id, event_type, data) 函数引用
        sse_queues: 全局 _sse_queues 字典引用
        sse_lock: 全局 _sse_lock 引用
    """
    market = request.args.get('market', 'US').upper()
    category = request.args.get('category', '').strip() or None
    pages = min(int(request.args.get('pages', 3)), 10)  # 最多扫10页
    cdp_port = int(request.args.get('cdp_port', 9222))
    auto_filter = request.args.get('filter', '1') == '1'  # 是否自动筛选

    if not category:
        return Response(
            f"event: error\ndata: {json.dumps({'error': '请选择一个类目'}, ensure_ascii=False)}\n\n",
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )

    # 生成任务 ID + 创建队列
    task_id = str(uuid.uuid4())[:8]
    q = queue.Queue(maxsize=200)
    with sse_lock:
        sse_queues[task_id] = q

    def _run_in_thread():
        """子线程：执行扫描 + 筛选"""
        try:
            import asyncio

            def progress_cb(pct, msg):
                sse_push_fn(task_id, 'progress', {'type': 'progress', 'pct': pct, 'msg': msg})

            def page_cb(page_num, page_records):
                sse_push_fn(task_id, 'page', {
                    'type': 'page',
                    'page': page_num,
                    'records': page_records,
                    'count': len(page_records),
                })

            # 执行 CDP 扫描
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                scan_rank(
                    market=market,
                    category=category,
                    pages=pages,
                    cdp_port=cdp_port,
                    progress_callback=progress_cb,
                    page_callback=page_cb,
                )
            )
            loop.close()

            if not result.get('success'):
                sse_push_fn(task_id, 'error', {'error': result.get('error', '未知错误')})
                return

            records = result.get('records', [])

            # 可选：自动执行市场级筛选 + 增长评分
            filter_summary = None
            if auto_filter and records:
                # 市场级筛选
                filter_result = batch_filter(records)

                # 对每条记录追加增长评分
                for r in filter_result['results']:
                    growth = score_growth(r)
                    r['_growth_score'] = growth

                # 综合排序
                ranked = rank_records(filter_result['results'])

                filter_summary = {
                    **filter_result,
                    'results': ranked,
                }

            resp_data = {
                'success': True,
                'market': market,
                'category': category,
                'total_records': len(records),
                'pages_scanned': result.get('pages_scanned', pages),
                'filter': filter_summary,
                'cached': False,
            }
            sse_push_fn(task_id, 'done', resp_data)

        except Exception as e:
            import traceback
            traceback.print_exc()
            sse_push_fn(task_id, 'error', {'error': str(e)[:300]})
        finally:
            try:
                q.put_nowait((None, None))
            except Exception:
                pass
            with sse_lock:
                sse_queues.pop(task_id, None)

    # 启动子线程
    t = threading.Thread(target=_run_in_thread, daemon=True)
    t.start()

    def gen():
        while True:
            try:
                event_type, data = q.get(timeout=180)  # 榜单扫描给3分钟超时
                if event_type is None:
                    break
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            except queue.Empty:
                yield f"event: error\ndata: {json.dumps({'error': '扫描超时（3分钟无响应）'}, ensure_ascii=False)}\n\n"
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


# ── 路由: 仅筛选（对已有数据做筛选） ────────────────────────

def run_filter():
    """
    对提交的数据执行市场级筛选和增长评分
    Body: JSON array of records
    """
    data = request.get_json(force=True, silent=True)
    if not data or not isinstance(data, list):
        return jsonify({'success': False, 'error': '需要 POST JSON 数组'})

    # 市场级筛选
    filter_result = batch_filter(data)

    # 追加增长评分并排序
    for r in filter_result['results']:
        growth = score_growth(r)
        r['_growth_score'] = growth

    ranked = rank_records(filter_result['results'])

    return jsonify({
        'success': True,
        **filter_result,
        'results': ranked,
    })


# ── 路由注册函数（被 app.py 调用） ───────────────────────────

def register_routes(app, _sse_push=None, _queues=None, _lock=None):
    """
    将所有榜单路由注册到 Flask app

    Args:
        app: Flask 实例
        _sse_push: _sse_push 函数引用（用于 SSE）
        _queues: _sse_queues 字典引用
        _lock: _sse_lock 引用
    """

    @app.route('/api/ranking/categories')
    def _api_categories():
        return get_categories()

    @app.route('/api/ranking/test-connection')
    def _api_test_conn():
        return test_connection()

    @app.route('/api/ranking/scan')
    def _api_scan_sync():
        return scan_sync()

    # ── 路由: 关键词自动补全建议 ──────────────────────────────
    @app.route('/api/ranking/keyword-suggest')
    def _api_keyword_suggest():
        """通过 CDP 在 SellerSprite 搜索框输入关键词，获取自动补全下拉列表"""
        import asyncio

        keyword = request.args.get('keyword', '').strip()
        if not keyword:
            return jsonify({'success': False, 'error': '请输入关键词'})

        cdp_port = int(request.args.get('cdp_port', 9222))

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # 注意: 参数顺序 (keyword, cdp_port) 必须与函数签名一致
            result = loop.run_until_complete(
                fetch_keyword_suggestions(keyword=keyword, cdp_port=cdp_port)
            )
            loop.close()

            # 解析结果并返回
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    return jsonify(parsed)
                except Exception:
                    return jsonify({'success': False, 'raw': result[:500]})
            return jsonify(result)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)[:300]})

    @app.route('/api/ranking/filter', methods=['POST'])
    def _api_filter():
        return run_filter()

    @app.route('/api/ranking/stream')
    def _api_stream_scan():
        if _sse_push is None or _queues is None or _lock is None:
            return jsonify({
                'success': False,
                'error': 'SSE 基础设施未初始化'
            }), 500
        # 使用闭包捕获 request 和共享对象
        nonlocal_sse_push = _sse_push
        nonlocal_queues = _queues
        nonlocal_lock = _lock
        # 重新构建 stream_scan 的请求处理逻辑（避免循环依赖）
        return _make_stream_response(
            nonlocal_sse_push, nonlocal_queues, nonlocal_lock
        )


def _make_stream_response(sse_push_fn, sse_queues, sse_lock):
    """构建流式扫描响应（内联避免闭包问题）"""
    market = request.args.get('market', 'US').upper()
    mode = request.args.get('mode', 'nav').strip().lower()  # 'nav' 或 'search'
    category = request.args.get('category', '').strip() or None
    keyword = request.args.get('keyword', '').strip() or None
    pages = min(int(request.args.get('pages', 3)), 10)
    cdp_port = int(request.args.get('cdp_port', 9222))
    auto_filter = request.args.get('filter', '1') == '1'

    # 参数校验
    if mode == 'nav' and not category:
        return Response(
            f"event: error\ndata: {json.dumps({'error': '请选择一个类目'}, ensure_ascii=False)}\n\n",
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )
    if mode == 'search' and not keyword:
        return Response(
            f"event: error\ndata: {json.dumps({'error': '请输入搜索关键词'}, ensure_ascii=False)}\n\n",
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )

    task_id = str(uuid.uuid4())[:8]
    q = queue.Queue(maxsize=200)
    with sse_lock:
        sse_queues[task_id] = q

    def _run_in_thread():
        try:
            import asyncio

            def progress_cb(pct, msg):
                sse_push_fn(task_id, 'progress', {'type': 'progress', 'pct': pct, 'msg': msg})

            def page_cb(page_num, page_records):
                sse_push_fn(task_id, 'page', {
                    'type': 'page',
                    'page': page_num,
                    'records': page_records,
                    'count': len(page_records),
                })

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                scan_rank(
                    market=market, category=category, pages=pages,
                    cdp_port=cdp_port, progress_callback=progress_cb,
                    page_callback=page_cb,
                    mode=mode, keyword=keyword,
                )
            )
            loop.close()

            if not result.get('success'):
                sse_push_fn(task_id, 'error', {'error': result.get('error', '未知错误')})
                return

            records = result.get('records', [])
            filter_summary = None
            if auto_filter and records:
                fr = batch_filter(records)
                for r in fr['results']:
                    r['_growth_score'] = score_growth(r)
                ranked = rank_records(fr['results'])
                filter_summary = {**fr, 'results': ranked}

            resp_data = {
                'success': True, 'market': market, 'mode': mode,
                'category': category, 'keyword': keyword,
                'total_records': len(records),
                'pages_scanned': result.get('pages_scanned', pages),
                'filter': filter_summary, 'cached': False,
            }
            sse_push_fn(task_id, 'done', resp_data)
        except Exception as e:
            import traceback
            traceback.print_exc()
            sse_push_fn(task_id, 'error', {'error': str(e)[:300]})
        finally:
            try:
                q.put_nowait((None, None))
            except Exception:
                pass
            with sse_lock:
                sse_queues.pop(task_id, None)

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
                yield f"event: error\ndata: {json.dumps({'error': '扫描超时'}, ensure_ascii=False)}\n\n"
                break

    return Response(gen(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    })
