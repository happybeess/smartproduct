# -*- coding: utf-8 -*-
"""
榜单筛选器
基于 filter_products.py (市场级筛选) 和 filter_growth.py (产品级增长筛选) 改造
适配 CDP 扫榜返回的细分市场数据格式
"""

import re
from typing import List, Dict, Tuple, Any, Optional

# ── 市场级筛选规则 ──────────────────────────────────────────
# 9项硬性规则，用于评估每个细分市场的整体质量

FILTER_RULES = [
    # (字段名, 操作符, 阈值, 分类, 描述)
    ('monthly_sales', '>=', 100000,   '市场规模', '月总销量>10万'),
    ('product_concentration', '<', 50,      '竞争环境', '商品集中度<50%'),
    ('brand_concentration', '<', 70,        '竞争环境', '品牌集中度<70%'),
    ('seller_concentration', '<', 70,       '竞争环境', '卖家集中度<70%'),
    ('new_product_ratio', '>=', 5,          '新品机会', '新品占比>=5%'),
    ('margin_rate', '>=', 60,               '盈利空间', '平均毛利率>=60%'),
    ('avg_price', '>=', 15,                 '盈利空间', '平均价格>=$15'),
    ('return_rate', '<', 8,                 '风险指标', '退货率<8%'),
    ('fba_ratio', '>=', 60,                 '卖家友好度', 'FBA占比>=60%'),
]


def parse_num(value) -> Optional[float]:
    """解析各种格式的数字（含百分号、货币符号、逗号分隔等）"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or s == '-' or s == 'N/A' or s.lower() == 'null':
        return None
    # 去除常见符号
    s = re.sub(r'[,$¥€£%\s]', '', s)
    # 处理 K/M/B 后缀
    m = re.match(r'^([\d.]+)\s*([KMBkmb])$', s, re.I)
    if m:
        num = float(m.group(1))
        suffix = m.group(2).upper()
        multiplier = {'K': 1e3, 'M': 1e6, 'B': 1e9}[suffix]
        return num * multiplier
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def extract_fba_ratio(record: dict) -> Optional[float]:
    """
    从记录中提取 FBA 占比
    可能藏在 seller_type 字段中，如 "FBA 72%"
    """
    seller_type = str(record.get('seller_type') or '')
    fba_val = record.get('fba_ratio')
    if fba_val is not None:
        return parse_num(fba_val)
    # 尝试从 seller_type 文本中提取百分比
    m = re.search(r'FBA\s*:?\s*(\d+(?:\.\d+)?)\s*%', seller_type, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r'(\d+(?:\.\d+)?)\s*%\s*FBA', seller_type, re.I)
    if m:
        return float(m.group(1))
    return None


class MarketFilterResult:
    """单个市场的筛选结果"""

    def __init__(self, record: Dict[str, Any]):
        self.record = record
        self.sub_market = record.get('sub_market', 'Unknown')
        self.checks: List[Dict] = []  # 每条规则的检查结果
        self.passed_count = 0
        self.failed_count = 0
        self.verdict: str = ''  # 通过/观察/淘汰
        self.score: float = 0.0

    def to_dict(self) -> dict:
        return {
            'sub_market': self.sub_market,
            'verdict': self.verdict,
            'score': round(self.score, 1),
            'passed': self.passed_count,
            'failed': self.failed_count,
            'total': len(self.checks),
            'checks': self.checks,
            **{k: v for k, v in self.record.items()
               if k not in ('index',)},
        }


def apply_filter(record: Dict[str, Any]) -> MarketFilterResult:
    """
    对一条细分市场记录应用全部9条筛选规则

    Returns:
        MarketFilterResult 包含详细检查结果和判定
    """
    result = MarketFilterResult(record)

    for field_name, operator, threshold, category, desc in FILTER_RULES:
        # 获取字段值（特殊处理 fba_ratio）
        if field_name == 'fba_ratio':
            value = extract_fba_ratio(record)
        else:
            value = parse_num(record.get(field_name))

        # 判断是否通过
        passed = False
        if value is not None:
            if operator == '>=':
                passed = value >= threshold
            elif operator == '<':
                passed = value < threshold
            elif operator == '>':
                passed = value > threshold
            elif operator == '<=':
                passed = value <= threshold
            elif operator == '==':
                passed = value == threshold

        check = {
            'field': field_name,
            'category': category,
            'description': desc,
            'operator': operator,
            'threshold': threshold,
            'value': value,
            'passed': passed,
        }
        result.checks.append(check)

        if passed:
            result.passed_count += 1
        else:
            result.failed_count += 1

    # 综合判定
    total = len(result.checks)
    if result.failed_count == 0:
        result.verdict = "pass"          # 全部通过 → 通过 ✅
        result.score = 100.0
    elif result.failed_count <= 2:
        result.verdict = "observe"       # 差1-2项 → 观察名单 ⚠️
        result.score = max(40.0, round((result.passed_count / total) * 100 - 10, 1))
    else:
        result.verdict = "eliminate"     # 差3项+ → 淘汰 ❌
        result.score = max(0.0, round((result.passed_count / total) * 80 - 20, 1))

    return result


def batch_filter(records: List[Dict]) -> Dict[str, Any]:
    """
    批量筛选多条记录，返回汇总统计

    Returns:
        {
            total: 总数,
            pass_count: 通过数,
            observe_count: 观察数,
            eliminate_count: 淘汰数,
            results: [MarketFilterResult.to_dict(), ...],
        }
    """
    results = []
    verdicts = {"pass": 0, "observe": 0, "eliminate": 0}

    for rec in records:
        r = apply_filter(rec)
        results.append(r.to_dict())
        verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1

    return {
        'total': len(results),
        'pass_count': verdicts.get('pass', 0),
        'observe_count': verdicts.get('observe', 0),
        'eliminate_count': verdicts.get('eliminate', 0),
        'results': results,
    }


# ── 产品级增长评分 ──────────────────────────────────────────
# 基于各维度指标打分（共10分）

GROWTH_CRITERIA = [
    # (字段名, 解析函数, 条件, 分值, 标签)
    ('growth_rate', lambda v: parse_num(v), lambda v: v and v >= 10, 3, '高增长'),
    ('monthly_sales', lambda v: parse_num(v), lambda v: v and v >= 1000, 2, '高销量'),
    ('listing_months', lambda v: parse_num(v), lambda v: v is not None and v <= 12, 2, '新品上架'),
    ('new_reviews', lambda v: parse_num(v), lambda v: v and v > 0, 1, '新增评价'),
    ('margin_rate', lambda v: parse_num(v), lambda v: v and v >= 30, 1, '高毛利'),
    ('avg_price', lambda v: parse_num(v), lambda v: v and v >= 15, 1, '合理定价'),
]


def score_growth(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    对单条记录进行增长潜力评分

    Returns:
        {
            score: int (0-10),
            level: str (强增长/稳步/微弱),
            tags: [str],  # 达成的标签列表
            details: [dict],  # 各项得分详情
            core_passed: bool,  # 是否通过核心门槛(增长率>0)
        }
    """
    details = []
    tags = []
    total_score = 0

    # 核心门槛：销量增长率 > 0%
    growth_rate = parse_num(record.get('growth_rate')) or \
                  parse_num(record.get('soldCntGrowthRate') or record.get('sold_cnt_growth'))
    core_passed = (growth_rate is not None and growth_rate > 0)

    if not core_passed:
        return {
            'score': 0,
            'level': '微弱',
            'tags': ['负增长'],
            'details': [{'criteria': '核心门槛', 'passed': False, 'reason': f'增长率={growth_rate}'}],
            'core_passed': False,
        }

    # 加分项评分
    for field_name, parser, condition, points, label in GROWTH_CRITERIA:
        raw_value = record.get(field_name)
        parsed_value = parser(raw_value) if raw_value else None
        passed = condition(parsed_value)

        detail = {
            'field': field_name,
            'label': label,
            'points': points,
            'value': parsed_value,
            'passed': passed,
        }
        details.append(detail)

        if passed:
            total_score += points
            tags.append(label)

    # 分级
    if total_score >= 6:
        level = "强增长"
    elif total_score >= 3:
        level = "稳步"
    else:
        level = "微弱"

    return {
        'score': total_score,
        'level': level,
        'tags': tags,
        'details': details,
        'core_passed': True,
    }


# ── 综合排序 ───────────────────────────────────────────────

def rank_records(records: List[Dict], weight_filter=0.6, weight_growth=0.4) -> List[Dict]:
    """
    对记录进行综合加权排序

    Args:
        records: 已经过滤的记录列表（需包含 _filter_result 和 _growth_score 字段）
        weight_filter: 市场级筛选权重
        weight_growth: 增长评分权重
    """
    scored = []
    for rec in records:
        filter_score = rec.get('_filter_result', {}).get('score', 0)
        growth_score = rec.get('_growth_score', {}).get('score', 0)
        # 归一化: filter 0-100, growth 0-10 → 统一到 0-100
        growth_normalized = min(growth_score * 10, 100)
        combined = filter_score * weight_filter + growth_normalized * weight_growth
        scored.append((combined, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [rec for _, rec in scored]
