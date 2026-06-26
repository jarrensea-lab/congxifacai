"""
Serenity 产业链分析引擎 — 供应链瓶颈分析 + 7 维评分 + 红旗信号检测

灵感来自 Serenity / @aleabitoreddit 的供应链瓶颈研究方法论。
将市场故事拆解为系统变化 → 产业链层级 → 稀缺层 → 候选公司 → 证据 → 风险。

设计原则:
- 先排产业链层级，再排公司
- 证据为导向，社交媒体内容仅作线索
- 输出研究优先级排序，不做买入/卖出建议
"""

import copy
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any

from app.ai.serenity_evidence import build_quote_evidence, build_verification_tasks

# ============================================================
# 1. 产业链层级定义（8 层价值链地图）
# ============================================================

VALUE_CHAIN_LAYERS = [
    ("终端客户与资本开支源", "End customers and capex source"),
    ("系统集成商与 OEM", "System integrators and OEMs"),
    ("模组与子系统", "Modules and subsystems"),
    ("芯片、器件与关键组件", "Chips, devices, and critical components"),
    ("工艺、组装、封装与测试", "Process, assembly, packaging, and testing"),
    ("设备与计量", "Equipment and metrology"),
    ("材料、耗材与特种输入", "Materials, consumables, and specialty inputs"),
    ("基础设施（电力/散热/网络）", "Physical infrastructure (power/cooling/network)"),
]

# 常见产业链主题的典型卡点映射
THEME_CHOKEPOINTS: Dict[str, List[str]] = {
    "AI半导体": ["内存互连(HBM/DDR5)", "先进封装(CoWoS)", "CMP/减薄", "高深宽比刻蚀",
                  "CMP抛光液/电镀耗材", "光刻胶", "高纯靶材", "EDA/IP"],
    "CPO光通信": ["磷化铟(InP)衬底", "硅光芯片", "激光器(EML/VCSEL)", "CW光源",
                   "FA光纤阵列", "MPO连接器", "DSP芯片", "薄膜铌酸锂(TFLN)"],
    "机器人": ["精密减速器(RV/谐波)", "空心杯电机", "力矩传感器", "编码器",
                "精密轴承", "丝杠/导轨", "驱动器"],
    "AI基建/电力": ["变压器", "HVDC换流阀", "高压开关", "液冷(CDU/冷板)",
                    "UPS/HVDC电源", "IGBT/SiC功率器件"],
    "新能源" : ["光伏硅料", "锂矿/碳酸锂", "正极材料(高镍)", "隔膜",
                "电解液(六氟磷酸锂)", "钠离子电池"],
    "创新药": ["CXO(CDMO)", "ADC药物", "GLP-1多肽",
               "基因治疗(AAV载体)", "mRNA疫苗"],
}

# ============================================================
# 2. 7 维评分卡
# ============================================================

# 每个维度的评分标准 (1-10)
SCORING_DIMENSIONS = {
    "需求确定性": {
        "description": "需求是否已经发生，还是只存在于想象中？",
        "weight": 0.20,
        "levels": {
            1:  "纯概念/想象，无可观察需求信号",
            3:  "有行业讨论但缺乏具体采购/使用数据",
            5:  "有部分企业采购或收入确认，但规模有限",
            7:  "可观察到稳定的企业采购/用户采用，且增速明显",
            10: "需求已经爆发，供应商出货/涨价/扩产，客户在抢产能",
        },
    },
    "传导清晰度": {
        "description": "需求能否清晰传导到具体公司的财务报表收入项？",
        "weight": 0.20,
        "levels": {
            1:  "需求模糊，无法判断哪些公司受益",
            3:  "只能判断链条方向，无法精确定位",
            5:  "能定位到产业链层级，但具体公司不清晰",
            7:  "能清晰定位到具体公司的具体业务线",
            10: "需求传导路径极其清晰，直接对应某公司核心产品的量价齐升",
        },
    },
    "业务纯度": {
        "description": "公司收入多大比例直接受益于该需求？",
        "weight": 0.15,
        "levels": {
            1:  "该业务占比 <5%，基本不相关",
            3:  "有相关业务但占比 <10%",
            5:  "相关业务占比约 10-30%",
            7:  "核心业务占比 30-60%，弹性较大",
            10: "公司几乎纯正该赛道，占比 >60%",
        },
    },
    "市值弹性": {
        "description": "增量需求相对公司当前规模有多大？",
        "weight": 0.15,
        "levels": {
            1:  "超级大盘股，增量需求对公司影响微乎其微",
            3:  "大市值公司，增量需求贡献 <5% 收入增长",
            5:  "中等市值，增量需求能贡献 5-15% 增长",
            7:  "小市值，增量需求能贡献 15-50% 增长",
            10: "微盘/迷你市值，增量需求可带来 >50% 业绩弹性",
        },
    },
    "市场忽视度": {
        "description": "市场是否在用旧标签给公司定价？",
        "weight": 0.10,
        "levels": {
            1:  "市场已充分认知，估值已反映新叙事",
            3:  "大多数投资者已关注到这个变化",
            5:  "少数投资者意识到，但市场整体还没反应",
            7:  "市场普遍用旧业务标签，新赛道几乎未定价",
            10: "完全被忽视，零分析师覆盖，市场标签与业务实质完全错位",
        },
    },
    "验证速度": {
        "description": "1-4 个季度内能否通过财报/公告验证论据？",
        "weight": 0.10,
        "levels": {
            1:  "需要 >2 年验证，或无法验证",
            3:  "需要 1-2 年才有可见变化",
            5:  "约 2-4 个季度可见财报信号",
            7:  "1-2 个季度可见收入/订单/毛利率变化",
            10: "下个季度财报即可验证（已有订单/出货/涨价）",
        },
    },
    "下行风险": {
        "description": "如果判断错了，最坏情况是什么？（反向评分，越高越安全）",
        "weight": 0.10,
        "levels": {
            10: "几乎无下行风险（估值底+现金流充足+替代方案少）",
            7:  "下行风险有限，有安全边际",
            5:  "有一定下行空间，但有限（适度估值）",
            3:  "估值偏高，判断错误回撤较大",
            1:  "极高风险（微盘/高估值/低流动性/无盈利）",
        },
    },
}


def score_company(
    name: str,
    code: str,
    demand_certainty: int,
    transmission_clarity: int,
    business_purity: int,
    market_cap_elasticity: int,
    market_neglect: int,
    verification_speed: int,
    downside_risk: int,
    custom_notes: str = "",
) -> Dict[str, Any]:
    """对一家公司/标的进行 7 维评分

    Args:
        name: 公司名称
        code: 股票代码
        demand_certainty: 需求确定性 (1-10)
        transmission_clarity: 传导清晰度 (1-10)
        business_purity: 业务纯度 (1-10)
        market_cap_elasticity: 市值弹性 (1-10)
        market_neglect: 市场忽视度 (1-10)
        verification_speed: 验证速度 (1-10)
        downside_risk: 下行风险/安全性 (1-10, 越高越安全)
        custom_notes: 自定义备注

    Returns:
        评分结果 dict
    """
    scores = {
        "需求确定性": demand_certainty,
        "传导清晰度": transmission_clarity,
        "业务纯度": business_purity,
        "市值弹性": market_cap_elasticity,
        "市场忽视度": market_neglect,
        "验证速度": verification_speed,
        "下行风险": downside_risk,
    }

    weighted_sum = sum(
        scores[dim] * SCORING_DIMENSIONS[dim]["weight"]
        for dim in scores
    )
    # 标准化到 0-100
    total_score = round(weighted_sum * 10, 1)

    return {
        "name": name,
        "code": code,
        "score": total_score,
        "scores": scores,
        "breakdown": {
            dim: {
                "value": scores[dim],
                "weight": SCORING_DIMENSIONS[dim]["weight"],
                "weighted": round(scores[dim] * SCORING_DIMENSIONS[dim]["weight"], 2),
                "description": _describe_level(dim, scores[dim]),
            }
            for dim in scores
        },
        "notes": custom_notes,
    }


def _describe_level(dimension: str, level: int) -> str:
    """获取评分等级的文本描述"""
    dim = SCORING_DIMENSIONS.get(dimension, {})
    levels = dim.get("levels", {})
    # 找最接近的等级
    closest = min(levels.keys(), key=lambda k: abs(k - level))
    return levels.get(closest, "")


def score_summary_table(results: List[Dict[str, Any]]) -> str:
    """将多个标的评分结果格式化为摘要表格

    Returns:
        Markdown 格式的表格
    """
    if not results:
        return "无评分结果"
    lines = [
        "| 标的 | 总分 | 需求确定性 | 传导清晰度 | 业务纯度 | 市值弹性 | 市场忽视度 | 验证速度 | 下行安.",
        "|------|:---:|:----------:|:----------:|:--------:|:--------:|:----------:|:--------:|:--------:|"
    ]
    for r in sorted(results, key=lambda x: x["score"], reverse=True):
        s = r["scores"]
        lines.append(
            f"| {r['name']}({r['code']}) | **{r['score']}** "
            f"| {s['需求确定性']} | {s['传导清晰度']} | {s['业务纯度']} "
            f"| {s['市值弹性']} | {s['市场忽视度']} | {s['验证速度']} | {s['下行风险']} |"
        )
    return "\n".join(lines)


# ============================================================
# 3. 红旗信号检测（风控增强）
# ============================================================

RED_FLAGS = [
    {
        "id": "single_customer_rumor",
        "label": "依赖单一客户传闻",
        "check": "论据是否主要依赖一个未具名客户的传闻？",
        "severity": "high",
    },
    {
        "id": "social_media_driven",
        "label": "社交媒体炒作驱动",
        "check": "股价上涨是否主要由社交媒体/大V喊单驱动？",
        "severity": "high",
    },
    {
        "id": "needs_financing",
        "label": "需融资才能兑现机遇",
        "check": "公司是否需要在机遇兑现前先融资（定增/可转债/配股）？",
        "severity": "high",
    },
    {
        "id": "vague_customer_revenue",
        "label": "客户匿名/收入模糊",
        "check": "客户是否匿名？收入影响是否模糊？",
        "severity": "medium",
    },
    {
        "id": "inventory_receivable_growth",
        "label": "存货/应收增长快于收入",
        "check": "存货和应收账款增速是否显著快于收入增速？",
        "severity": "medium",
    },
    {
        "id": "margin_not_improving",
        "label": "声称稀缺但毛利率无改善",
        "check": "公司声称供不应求，但毛利率没有改善甚至下降？",
        "severity": "high",
    },
    {
        "id": "management_theme_talk",
        "label": "管理层讲题材但数据不兑现",
        "check": "管理层反复讲热点概念，但分部业务数据没有变化？",
        "severity": "medium",
    },
    {
        "id": "insider_selling",
        "label": "内幕或大股东减持",
        "check": "大股东/高管是否在近期（3个月内）持续减持？",
        "severity": "medium",
    },
    {
        "id": "micro_cap_liquidity",
        "label": "微盘/低流动性",
        "check": "市值是否 < 50亿且日均成交额 < 5000万？",
        "severity": "high",
    },
    {
        "id": "valuation_assumes_perfection",
        "label": "估值假设完美执行",
        "check": "当前估值是否已假设未来3年业绩翻倍以上？",
        "severity": "medium",
    },
    {
        "id": "price_limit_social_backflow",
        "label": "涨停后社媒倒灌",
        "check": "研究线索是否来自股价异动后的社媒解释，而不是事前基本面证据？",
        "severity": "high",
    },
    {
        "id": "anonymous_customer",
        "label": "客户匿名",
        "check": "关键客户是否无法通过公告、财报或公开资料交叉验证？",
        "severity": "medium",
    },
    {
        "id": "no_financial_transmission",
        "label": "财务传导缺失",
        "check": "产业链逻辑是否无法落到收入、毛利率、订单或产能利用率？",
        "severity": "high",
    },
    {
        "id": "financing_pressure",
        "label": "融资压力",
        "check": "公司是否需要持续融资才能兑现产能、订单或研发承诺？",
        "severity": "medium",
    },
    {
        "id": "weak_evidence",
        "label": "证据强度不足",
        "check": "论据是否主要来自社媒、截图、传闻或缺乏出处的二手整理？",
        "severity": "medium",
    },
]


def check_red_flags(
    signals: Dict[str, bool],
    custom_flags: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """检查红旗信号

    Args:
        signals: {flag_id: True/False} 表示是否触发
        custom_flags: 自定义红旗信息

    Returns:
        触发的红旗列表
    """
    triggered = []
    for flag in RED_FLAGS:
        if signals.get(flag["id"], False):
            triggered.append({
                "id": flag["id"],
                "label": flag["label"],
                "check": flag["check"],
                "severity": flag["severity"],
            })
    if custom_flags:
        for cf in custom_flags:
            triggered.append({
                "id": "custom",
                "label": cf,
                "check": cf,
                "severity": "medium",
            })
    return triggered


def summarize_red_flags(flags: List[Dict[str, Any]]) -> str:
    """将红旗信号格式化为文本"""
    if not flags:
        return "✅ 未检测到明显红旗信号"
    high = [f for f in flags if f["severity"] == "high"]
    medium = [f for f in flags if f["severity"] == "medium"]
    lines = []
    if high:
        lines.append("🔴 **高风险信号：**")
        for f in high:
            lines.append(f"  - {f['label']}")
    if medium:
        lines.append("🟡 **中等风险信号：**")
        for f in medium:
            lines.append(f"  - {f['label']}")
    return "\n".join(lines)


# ============================================================
# 3b. Serenity 独立瓶颈选股流水线（V2 MVP）
# ============================================================

SERENITY_SCORING_V2 = {
    "需求确定性": {
        "weight": 0.15,
        "description": "需求是否由真实资本开支、订单、产能或财报信号支撑。",
    },
    "瓶颈强度": {
        "weight": 0.20,
        "description": "该环节是否真的卡住供给，且短期难以被替代或快速扩产。",
    },
    "传导清晰度": {
        "weight": 0.15,
        "description": "需求能否从系统变化清晰传导到公司业务线与财务科目。",
    },
    "业务纯度": {
        "weight": 0.10,
        "description": "相关业务对公司收入、利润或估值弹性的贡献占比。",
    },
    "证据强度": {
        "weight": 0.15,
        "description": "是否有公告、财报、客户、产品、行业数据等可核验证据。",
    },
    "市场忽视度": {
        "weight": 0.10,
        "description": "市场是否仍用旧标签定价，未充分反映新瓶颈价值。",
    },
    "验证速度": {
        "weight": 0.05,
        "description": "未来 1-4 个季度能否通过订单、收入、毛利率等指标验证。",
    },
    "下行安全": {
        "weight": 0.10,
        "description": "若判断错误，估值、流动性、现金流和基本面提供多少缓冲。",
    },
}

THEME_ALIASES = {
    "电网设备": "AI基建/电力",
    "AI电力": "AI基建/电力",
    "算力电力": "AI基建/电力",
    "AI基础设施": "AI基建/电力",
    "光模块": "CPO光通信",
    "光通信": "CPO光通信",
    "CPO": "CPO光通信",
    "半导体": "AI半导体",
    "AI芯片": "AI半导体",
    "面板": "面板产业链",
    "显示面板": "面板产业链",
    "人形机器人": "机器人",
}

THEME_CANDIDATES: Dict[str, List[Dict[str, Any]]] = {
    "AI基建/电力": [
        {
            "name": "许继电气",
            "code": "000400",
            "chokepoint": "电网二次设备/换流阀",
            "chain_position": "设备与基础设施",
            "scores": {"需求确定性": 7, "瓶颈强度": 7, "传导清晰度": 7, "业务纯度": 7,
                       "证据强度": 5, "市场忽视度": 5, "验证速度": 6, "下行安全": 5},
            "evidence_items": [
                {"fact": "AI 数据中心用电增长逻辑需要继续落到电网投资、订单和毛利率验证", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验直流输电/配网自动化订单、招标中标、毛利率变化。",
        },
        {
            "name": "平高电气",
            "code": "600312",
            "chokepoint": "高压开关/GIS",
            "chain_position": "高压输变电设备",
            "scores": {"需求确定性": 7, "瓶颈强度": 7, "传导清晰度": 7, "业务纯度": 7,
                       "证据强度": 5, "市场忽视度": 5, "验证速度": 6, "下行安全": 5},
            "evidence_items": [
                {"fact": "高压设备是电网扩容链条的关键环节，需核验电网招标与交付节奏", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验国网招标份额、交付周期、原材料价格影响。",
        },
        {
            "name": "思源电气",
            "code": "002028",
            "chokepoint": "输配电一次/二次设备",
            "chain_position": "电力设备平台",
            "scores": {"需求确定性": 7, "瓶颈强度": 6, "传导清晰度": 7, "业务纯度": 6,
                       "证据强度": 5, "市场忽视度": 5, "验证速度": 6, "下行安全": 5},
            "evidence_items": [
                {"fact": "平台型电力设备公司可能受益于电网建设，但需拆分业务线验证", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验产品结构、海外收入、订单与现金流。",
        },
        {
            "name": "特变电工",
            "code": "600089",
            "chokepoint": "变压器/输变电",
            "chain_position": "电力设备与能源材料",
            "scores": {"需求确定性": 7, "瓶颈强度": 6, "传导清晰度": 6, "业务纯度": 5,
                       "证据强度": 5, "市场忽视度": 4, "验证速度": 5, "下行安全": 5},
            "evidence_items": [
                {"fact": "变压器是电力扩容核心设备，但多业务结构会稀释单一瓶颈弹性", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验变压器板块收入占比、订单、海外需求与能源业务波动。",
        },
        {
            "name": "英维克",
            "code": "002837",
            "chokepoint": "液冷/CDU/温控",
            "chain_position": "数据中心散热基础设施",
            "scores": {"需求确定性": 7, "瓶颈强度": 7, "传导清晰度": 7, "业务纯度": 6,
                       "证据强度": 5, "市场忽视度": 4, "验证速度": 6, "下行安全": 4},
            "evidence_items": [
                {"fact": "高功率机柜推高液冷需求，需核验客户、出货和毛利率是否兑现", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验液冷收入占比、头部客户、价格竞争和交付能力。",
        },
    ],
    "AI半导体": [
        {
            "name": "澜起科技",
            "code": "688008",
            "chokepoint": "内存互连/服务器内存接口",
            "chain_position": "芯片与关键组件",
            "scores": {"需求确定性": 7, "瓶颈强度": 7, "传导清晰度": 7, "业务纯度": 7,
                       "证据强度": 5, "市场忽视度": 5, "验证速度": 6, "下行安全": 5},
            "evidence_items": [
                {"fact": "AI 服务器内存带宽与容量升级是可跟踪方向，需用财报分部验证", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验 DDR5/MRCD/MDB 等产品收入、客户导入和毛利率。",
        },
        {
            "name": "北方华创",
            "code": "002371",
            "chokepoint": "半导体设备",
            "chain_position": "设备与计量",
            "scores": {"需求确定性": 7, "瓶颈强度": 8, "传导清晰度": 7, "业务纯度": 7,
                       "证据强度": 5, "市场忽视度": 3, "验证速度": 5, "下行安全": 4},
            "evidence_items": [
                {"fact": "设备国产化逻辑强，但市场关注度较高，估值与订单需要同步核验", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验新增订单、存货应收、先进制程设备进展。",
        },
        {
            "name": "中微公司",
            "code": "688012",
            "chokepoint": "刻蚀/MOCVD设备",
            "chain_position": "设备与计量",
            "scores": {"需求确定性": 7, "瓶颈强度": 8, "传导清晰度": 7, "业务纯度": 7,
                       "证据强度": 5, "市场忽视度": 3, "验证速度": 5, "下行安全": 4},
            "evidence_items": [
                {"fact": "高端刻蚀设备是先进制造约束之一，需核验产品结构和客户验证", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验刻蚀设备收入、客户验证进度、研发投入效率。",
        },
        {
            "name": "沪硅产业",
            "code": "688126",
            "chokepoint": "大硅片",
            "chain_position": "材料与耗材",
            "scores": {"需求确定性": 6, "瓶颈强度": 6, "传导清晰度": 6, "业务纯度": 6,
                       "证据强度": 4, "市场忽视度": 5, "验证速度": 4, "下行安全": 4},
            "evidence_items": [
                {"fact": "材料国产化具备方向性，但供需与盈利弹性需谨慎核验", "strength": "weak", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验产能利用率、价格、客户认证和亏损改善节奏。",
            "red_flag_signals": {"weak_evidence": True},
        },
        {
            "name": "华海清科",
            "code": "688120",
            "chokepoint": "CMP设备",
            "chain_position": "设备与工艺",
            "scores": {"需求确定性": 7, "瓶颈强度": 7, "传导清晰度": 7, "业务纯度": 7,
                       "证据强度": 5, "市场忽视度": 4, "验证速度": 5, "下行安全": 4},
            "evidence_items": [
                {"fact": "CMP 是先进制程和平坦化关键工艺，需核验订单和国产替代节奏", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验 CMP 设备订单、耗材延展、客户集中度。",
        },
    ],
    "CPO光通信": [
        {
            "name": "中际旭创",
            "code": "300308",
            "chokepoint": "高速光模块",
            "chain_position": "模组与子系统",
            "scores": {"需求确定性": 8, "瓶颈强度": 7, "传导清晰度": 8, "业务纯度": 8,
                       "证据强度": 5, "市场忽视度": 2, "验证速度": 7, "下行安全": 4},
            "evidence_items": [
                {"fact": "高速光模块受 AI 互连需求牵引，但市场已高度关注", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验 800G/1.6T 出货、客户结构、毛利率和估值消化。",
        },
        {
            "name": "新易盛",
            "code": "300502",
            "chokepoint": "高速光模块",
            "chain_position": "模组与子系统",
            "scores": {"需求确定性": 8, "瓶颈强度": 7, "传导清晰度": 8, "业务纯度": 8,
                       "证据强度": 5, "市场忽视度": 2, "验证速度": 7, "下行安全": 4},
            "evidence_items": [
                {"fact": "光模块需求传导清晰，但需防止高预期下的估值透支", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验客户份额、订单能见度、价格与良率。",
        },
        {
            "name": "天孚通信",
            "code": "300394",
            "chokepoint": "光器件/FA等精密组件",
            "chain_position": "关键组件",
            "scores": {"需求确定性": 8, "瓶颈强度": 7, "传导清晰度": 7, "业务纯度": 8,
                       "证据强度": 5, "市场忽视度": 3, "验证速度": 7, "下行安全": 4},
            "evidence_items": [
                {"fact": "精密光器件可能是良率与扩产瓶颈，需核验产品占比和客户导入", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验具体器件收入、客户认证、产能利用率。",
        },
        {
            "name": "源杰科技",
            "code": "688498",
            "chokepoint": "激光器芯片",
            "chain_position": "芯片与关键组件",
            "scores": {"需求确定性": 7, "瓶颈强度": 8, "传导清晰度": 6, "业务纯度": 7,
                       "证据强度": 4, "市场忽视度": 5, "验证速度": 5, "下行安全": 3},
            "evidence_items": [
                {"fact": "激光器芯片是上游卡点候选，但需要严格核验高端产品进度", "strength": "weak", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验高速激光器产品、客户验证、良率与收入确认。",
            "red_flag_signals": {"weak_evidence": True},
        },
        {
            "name": "光迅科技",
            "code": "002281",
            "chokepoint": "光器件/模块平台",
            "chain_position": "光通信组件与模块",
            "scores": {"需求确定性": 7, "瓶颈强度": 6, "传导清晰度": 6, "业务纯度": 6,
                       "证据强度": 5, "市场忽视度": 4, "验证速度": 5, "下行安全": 5},
            "evidence_items": [
                {"fact": "平台型光通信公司需拆分高速产品占比后判断弹性", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验高速产品占比、海外客户、盈利质量。",
        },
    ],
    "机器人": [
        {
            "name": "绿的谐波",
            "code": "688017",
            "chokepoint": "谐波减速器",
            "chain_position": "精密传动组件",
            "scores": {"需求确定性": 6, "瓶颈强度": 8, "传导清晰度": 6, "业务纯度": 8,
                       "证据强度": 4, "市场忽视度": 5, "验证速度": 4, "下行安全": 3},
            "evidence_items": [
                {"fact": "减速器是人形机器人关键部件，但量产节奏、客户和价格需核验", "strength": "weak", "source": "Serenity 线索待本地财报核验"},
            ],
            "verify_next": "核验客户、量产节奏、单机价值量、价格压力。",
            "red_flag_signals": {"weak_evidence": True},
        },
        {
            "name": "汇川技术",
            "code": "300124",
            "chokepoint": "伺服/控制系统",
            "chain_position": "驱动与控制",
            "scores": {"需求确定性": 6, "瓶颈强度": 6, "传导清晰度": 6, "业务纯度": 5,
                       "证据强度": 5, "市场忽视度": 3, "验证速度": 5, "下行安全": 5},
            "evidence_items": [
                {"fact": "工业自动化平台受机器人方向牵引，但业务体量较大，弹性需拆分", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验机器人相关业务占比、伺服订单和竞争格局。",
        },
        {
            "name": "鸣志电器",
            "code": "603728",
            "chokepoint": "空心杯/步进电机",
            "chain_position": "电机与执行器",
            "scores": {"需求确定性": 6, "瓶颈强度": 7, "传导清晰度": 6, "业务纯度": 6,
                       "证据强度": 4, "市场忽视度": 5, "验证速度": 4, "下行安全": 3},
            "evidence_items": [
                {"fact": "执行器方向有弹性，但客户与收入确认仍需核验", "strength": "weak", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验空心杯电机进展、客户认证和盈利能力。",
            "red_flag_signals": {"weak_evidence": True},
        },
        {
            "name": "埃斯顿",
            "code": "002747",
            "chokepoint": "工业机器人本体/伺服",
            "chain_position": "系统集成与核心部件",
            "scores": {"需求确定性": 6, "瓶颈强度": 5, "传导清晰度": 6, "业务纯度": 6,
                       "证据强度": 5, "市场忽视度": 4, "验证速度": 5, "下行安全": 4},
            "evidence_items": [
                {"fact": "机器人业务相关度较高，但需判断是否为真正瓶颈环节", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验订单、利润率、海外业务和本体竞争强度。",
        },
        {
            "name": "柯力传感",
            "code": "603662",
            "chokepoint": "力传感器",
            "chain_position": "传感器",
            "scores": {"需求确定性": 6, "瓶颈强度": 7, "传导清晰度": 5, "业务纯度": 5,
                       "证据强度": 4, "市场忽视度": 6, "验证速度": 4, "下行安全": 4},
            "evidence_items": [
                {"fact": "传感器是人形机器人感知链候选瓶颈，需确认产品适配和收入传导", "strength": "weak", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验六维力传感器产品、客户、收入占比和量产节奏。",
            "red_flag_signals": {"weak_evidence": True},
        },
    ],
    "面板产业链": [
        {
            "name": "TCL科技",
            "code": "000100",
            "chokepoint": "大尺寸面板产能/周期",
            "chain_position": "面板制造",
            "scores": {"需求确定性": 5, "瓶颈强度": 5, "传导清晰度": 6, "业务纯度": 7,
                       "证据强度": 5, "市场忽视度": 5, "验证速度": 5, "下行安全": 5},
            "evidence_items": [
                {"fact": "面板更多是周期供需与价格验证，不一定符合高强度瓶颈逻辑", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验面板价格、稼动率、库存和资本开支纪律。",
        },
        {
            "name": "京东方A",
            "code": "000725",
            "chokepoint": "面板龙头/规模供给",
            "chain_position": "面板制造",
            "scores": {"需求确定性": 5, "瓶颈强度": 4, "传导清晰度": 6, "业务纯度": 7,
                       "证据强度": 5, "市场忽视度": 4, "验证速度": 5, "下行安全": 5},
            "evidence_items": [
                {"fact": "大市值面板龙头弹性更多来自周期改善，非典型稀缺卡点", "strength": "medium", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验价格周期、库存天数、折旧压力与产品结构。",
        },
        {
            "name": "彩虹股份",
            "code": "600707",
            "chokepoint": "玻璃基板/面板周期",
            "chain_position": "材料与面板",
            "scores": {"需求确定性": 5, "瓶颈强度": 5, "传导清晰度": 5, "业务纯度": 6,
                       "证据强度": 4, "市场忽视度": 6, "验证速度": 4, "下行安全": 4},
            "evidence_items": [
                {"fact": "需区分周期反弹与真正不可替代的材料瓶颈", "strength": "weak", "source": "内部方法论待核验"},
            ],
            "verify_next": "核验玻璃基板供需、利润弹性、资产负债表压力。",
            "red_flag_signals": {"weak_evidence": True},
        },
    ],
}

DEFAULT_THEME_ALIASES = copy.deepcopy(THEME_ALIASES)
DEFAULT_THEME_CANDIDATES = copy.deepcopy(THEME_CANDIDATES)
DEFAULT_CANDIDATE_POOL_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "serenity" / "theme_candidates.json"
)

REQUIRED_CANDIDATE_FIELDS = {"name", "code", "chokepoint", "chain_position", "scores"}
REQUIRED_EVIDENCE_FIELDS = {"fact", "strength", "source"}
VALID_EVIDENCE_STRENGTHS = {"strong", "medium", "weak"}


def validate_theme_candidate_pool(raw: Dict[str, Any]) -> List[str]:
    """Return schema validation errors for a Serenity candidate pool JSON object."""
    errors: List[str] = []
    if not isinstance(raw, dict):
        return ["候选池根节点必须是 JSON object"]

    aliases = raw.get("aliases", {})
    if not isinstance(aliases, dict):
        errors.append("aliases 必须是 object")
    else:
        for alias, theme in aliases.items():
            if not isinstance(alias, str) or not isinstance(theme, str):
                errors.append(f"aliases.{alias} 必须映射到字符串主题名")

    candidates = raw.get("candidates")
    if not isinstance(candidates, dict):
        errors.append("candidates 必须是 object")
        return errors

    for theme, theme_candidates in candidates.items():
        if not isinstance(theme, str) or not theme:
            errors.append("candidates 的主题名必须是非空字符串")
            continue
        if not isinstance(theme_candidates, list):
            errors.append(f"candidates.{theme} 必须是数组")
            continue
        for index, candidate in enumerate(theme_candidates):
            prefix = f"candidates.{theme}[{index}]"
            if not isinstance(candidate, dict):
                errors.append(f"{prefix} 必须是 object")
                continue
            missing = sorted(REQUIRED_CANDIDATE_FIELDS - set(candidate))
            for field in missing:
                errors.append(f"{prefix}.{field} 缺少必填字段")
            for field in REQUIRED_CANDIDATE_FIELDS - {"scores"}:
                if field in candidate and not isinstance(candidate[field], str):
                    errors.append(f"{prefix}.{field} 必须是字符串")
            code = candidate.get("code")
            if isinstance(code, str) and (len(code) != 6 or not code.isdigit()):
                errors.append(f"{prefix}.code 必须是 6 位 A 股代码")

            scores = candidate.get("scores")
            if not isinstance(scores, dict):
                errors.append(f"{prefix}.scores 必须是 object")
            else:
                for dimension in SERENITY_SCORING_V2:
                    if dimension not in scores:
                        errors.append(f"{prefix}.scores.{dimension} 缺少必填评分维度")
                        continue
                    value = scores[dimension]
                    if not isinstance(value, int) or not 1 <= value <= 10:
                        errors.append(f"{prefix}.scores.{dimension} 必须是 1-10 的整数")
                for dimension in scores:
                    if dimension not in SERENITY_SCORING_V2:
                        errors.append(f"{prefix}.scores.{dimension} 不是合法评分维度")

            evidence_items = candidate.get("evidence_items", [])
            if not isinstance(evidence_items, list):
                errors.append(f"{prefix}.evidence_items 必须是数组")
            else:
                for evidence_index, item in enumerate(evidence_items):
                    evidence_prefix = f"{prefix}.evidence_items[{evidence_index}]"
                    if not isinstance(item, dict):
                        errors.append(f"{evidence_prefix} 必须是 object")
                        continue
                    for field in sorted(REQUIRED_EVIDENCE_FIELDS - set(item)):
                        errors.append(f"{evidence_prefix}.{field} 缺少必填字段")
                    strength = item.get("strength")
                    if strength is not None and strength not in VALID_EVIDENCE_STRENGTHS:
                        errors.append(
                            f"{evidence_prefix}.strength 必须是 strong/medium/weak"
                        )

            red_flags = candidate.get("red_flag_signals", {})
            if red_flags and not isinstance(red_flags, dict):
                errors.append(f"{prefix}.red_flag_signals 必须是 object")

    return errors


def load_theme_candidate_pool(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Load Serenity theme aliases and seed candidates from JSON with safe fallback."""
    pool_path = path or os.getenv("SERENITY_THEME_CANDIDATES_PATH")
    if pool_path is None:
        pool_path = str(DEFAULT_CANDIDATE_POOL_PATH)

    aliases = copy.deepcopy(DEFAULT_THEME_ALIASES)
    candidates = copy.deepcopy(DEFAULT_THEME_CANDIDATES)
    try:
        raw = json.loads(Path(pool_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"aliases": aliases, "candidates": candidates}
    if validate_theme_candidate_pool(raw):
        return {"aliases": aliases, "candidates": candidates}

    loaded_aliases = raw.get("aliases", {})
    loaded_candidates = raw.get("candidates", {})
    if isinstance(loaded_aliases, dict):
        aliases.update(loaded_aliases)
    if isinstance(loaded_candidates, dict):
        for theme, theme_candidates in loaded_candidates.items():
            if isinstance(theme_candidates, list):
                candidates[theme] = theme_candidates

    return {"aliases": aliases, "candidates": candidates}


_THEME_CANDIDATE_POOL = load_theme_candidate_pool()
THEME_ALIASES = _THEME_CANDIDATE_POOL["aliases"]
THEME_CANDIDATES = _THEME_CANDIDATE_POOL["candidates"]


def _normalize_theme(theme: str) -> str:
    """Normalize common user wording to the internal theme key."""
    cleaned = (theme or "").strip()
    return THEME_ALIASES.get(cleaned, cleaned)


def _clamp_score(value: Any) -> int:
    """Keep V2 score dimensions in a predictable 1-10 range."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(10, number))


def _research_tier(score: float, red_flags: List[Dict[str, Any]]) -> str:
    high_risk = any(flag["severity"] == "high" for flag in red_flags)
    if high_risk or score < 50:
        return "弱线索"
    if score >= 75:
        return "高优先级研究"
    if score >= 65:
        return "强观察"
    return "初步线索"


def score_company_v2(
    name: str,
    code: str,
    chokepoint: str,
    chain_position: str,
    scores: Dict[str, Any],
    evidence_items: Optional[List[Dict[str, str]]] = None,
    red_flag_signals: Optional[Dict[str, bool]] = None,
    notes: str = "",
    verify_next: str = "",
) -> Dict[str, Any]:
    """Score one A-share candidate with the Serenity 8D bottleneck framework."""
    normalized_scores = {
        dimension: _clamp_score(scores.get(dimension, 1))
        for dimension in SERENITY_SCORING_V2
    }
    weighted_sum = sum(
        normalized_scores[dimension] * config["weight"]
        for dimension, config in SERENITY_SCORING_V2.items()
    )
    total_score = round(weighted_sum * 10, 1)

    flags = check_red_flags(red_flag_signals or {})
    tier = _research_tier(total_score, flags)
    high_flag_count = sum(1 for flag in flags if flag["severity"] == "high")
    actionability = "research_watchlist"
    if high_flag_count or normalized_scores["证据强度"] <= 3 or normalized_scores["下行安全"] <= 2:
        actionability = "reject_for_now"

    return {
        "name": name,
        "code": code,
        "score": total_score,
        "scores": normalized_scores,
        "breakdown": {
            dimension: {
                "value": normalized_scores[dimension],
                "weight": config["weight"],
                "weighted": round(normalized_scores[dimension] * config["weight"], 2),
                "description": config["description"],
            }
            for dimension, config in SERENITY_SCORING_V2.items()
        },
        "chokepoint": chokepoint,
        "chain_position": chain_position,
        "evidence_items": evidence_items or [],
        "red_flags": flags,
        "research_tier": tier,
        "actionability": actionability,
        "notes": notes,
        "verify_next": verify_next,
    }


def adjust_scores_with_quote_evidence(
    scores: Dict[str, Any],
    quote_evidence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Adjust Serenity scores using quote-derived evidence without trading output."""
    adjusted_scores = {
        dimension: _clamp_score(scores.get(dimension, 1))
        for dimension in SERENITY_SCORING_V2
    }
    reasons: List[Dict[str, Any]] = []
    red_flag_signals: Dict[str, bool] = {}
    if not quote_evidence:
        return {"scores": adjusted_scores, "reasons": reasons, "red_flag_signals": red_flag_signals}

    metrics = quote_evidence.get("metrics") or {}
    lot_value = float(metrics.get("lot_value") or 0)
    cash_coverage_ratio = float(metrics.get("cash_coverage_ratio") or 0)
    amount_wan = float(metrics.get("amount_wan") or 0)
    pe_ttm = float(metrics.get("pe_ttm") or 0)
    pb = float(metrics.get("pb") or 0)

    if lot_value > 0 and cash_coverage_ratio < 1:
        before = adjusted_scores["下行安全"]
        adjusted_scores["下行安全"] = _clamp_score(before - 2)
        reasons.append({
            "dimension": "下行安全",
            "before": before,
            "after": adjusted_scores["下行安全"],
            "reason": f"一手金额 {lot_value:.2f} 元超过可用现金覆盖能力，小账户观察安全边际降低。",
        })

    if pe_ttm >= 80 or pb >= 10:
        before = adjusted_scores["下行安全"]
        adjusted_scores["下行安全"] = _clamp_score(before - 2)
        red_flag_signals["valuation_assumes_perfection"] = True
        reasons.append({
            "dimension": "下行安全",
            "before": before,
            "after": adjusted_scores["下行安全"],
            "reason": f"PE(TTM) {pe_ttm:.2f} / PB {pb:.2f} 处于极端估值区间，触发估值红旗。",
        })

    if amount_wan <= 0:
        before = adjusted_scores["证据强度"]
        adjusted_scores["证据强度"] = _clamp_score(before - 1)
        reasons.append({
            "dimension": "证据强度",
            "before": before,
            "after": adjusted_scores["证据强度"],
            "reason": "行情快照成交额缺失或为 0，流动性证据需要降权。",
        })

    if quote_evidence.get("strength") == "weak":
        before = adjusted_scores["证据强度"]
        adjusted_scores["证据强度"] = _clamp_score(before - 1)
        reasons.append({
            "dimension": "证据强度",
            "before": before,
            "after": adjusted_scores["证据强度"],
            "reason": "行情证据本身为弱证据，研究结论需要更多公告和财报交叉验证。",
        })

    return {"scores": adjusted_scores, "reasons": reasons, "red_flag_signals": red_flag_signals}


def get_theme_candidates(theme: str) -> List[Dict[str, Any]]:
    """Return deterministic A-share seed candidates for a Serenity theme."""
    normalized_theme = _normalize_theme(theme)
    return [candidate.copy() for candidate in THEME_CANDIDATES.get(normalized_theme, [])]


def _account_constraint(available_cash: float, total_assets: float) -> str:
    if available_cash <= 0 and total_assets <= 0:
        return "未提供账户规模；本报告只做研究排序与观察清单，不生成交易动作。"
    base = f"账户现金约 {available_cash:.2f} 元"
    if total_assets:
        base += f"，总资产约 {total_assets:.2f} 元"
    return f"{base}；小账户约束下，本报告仅观察与学习，不生成交易动作。"


def _run_quote_fetcher(
    quote_fetcher: Callable[[List[str]], Any],
    codes: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Run an async or sync quote fetcher from the synchronous Serenity pipeline."""
    result = quote_fetcher(codes)
    if asyncio.iscoroutine(result):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)
        raise RuntimeError("quote_fetcher requires async caller")
    return result or {}


def run_serenity_pipeline(
    theme: str,
    available_cash: float = 0,
    total_assets: float = 0,
    report_date: Optional[str] = None,
    context: str = "",
    quote_fetcher: Optional[Callable[[List[str]], Any]] = None,
) -> Dict[str, Any]:
    """Run a reusable Serenity bottleneck research pipeline for one theme."""
    normalized_theme = _normalize_theme(theme)
    candidates = get_theme_candidates(normalized_theme)
    quotes: Dict[str, Dict[str, Any]] = {}
    quote_status = {"status": "skipped", "error": ""}
    if quote_fetcher and candidates:
        codes = [candidate.get("code", "") for candidate in candidates if candidate.get("code")]
        try:
            quotes = _run_quote_fetcher(quote_fetcher, codes)
            quote_status = {"status": "success", "error": ""}
        except Exception as exc:
            quote_status = {"status": "failed", "error": str(exc)}

    scored = []
    verification_tasks = []
    for candidate in candidates:
        candidate_tasks = build_verification_tasks(normalized_theme, candidate)
        evidence_items = list(candidate.get("evidence_items", []))
        quote = quotes.get(candidate.get("code", ""))
        quote_evidence = None
        if quote:
            quote_evidence = build_quote_evidence(candidate, quote, available_cash=available_cash)
            evidence_items.append(quote_evidence)
        score_adjustment = adjust_scores_with_quote_evidence(
            candidate.get("scores", {}),
            quote_evidence,
        )
        red_flag_signals = dict(candidate.get("red_flag_signals", {}))
        red_flag_signals.update(score_adjustment["red_flag_signals"])
        scored_candidate = score_company_v2(
            name=candidate["name"],
            code=candidate["code"],
            chokepoint=candidate.get("chokepoint", ""),
            chain_position=candidate.get("chain_position", ""),
            scores=score_adjustment["scores"],
            evidence_items=evidence_items,
            red_flag_signals=red_flag_signals,
            notes=candidate.get("notes", ""),
            verify_next=candidate.get("verify_next", ""),
        )
        scored_candidate["verification_tasks"] = candidate_tasks
        scored_candidate["score_adjustments"] = score_adjustment["reasons"]
        if quote_evidence:
            scored_candidate["quote_evidence"] = quote_evidence
        scored.append(scored_candidate)
        verification_tasks.extend(candidate_tasks)
    scored.sort(key=lambda item: item["score"], reverse=True)
    verification_tasks.sort(
        key=lambda item: {"high": 0, "medium": 1, "low": 2}.get(item["priority"], 3)
    )

    return {
        "theme": theme,
        "normalized_theme": normalized_theme,
        "report_date": report_date or datetime.now().strftime("%Y-%m-%d"),
        "context": context,
        "chokepoints": get_theme_chokepoints(normalized_theme),
        "candidates": scored,
        "top_candidates": scored[:5],
        "verification_tasks": verification_tasks,
        "quote_status": quote_status,
        "available_cash": round(float(available_cash or 0), 2),
        "total_assets": round(float(total_assets or 0), 2),
        "account_constraint": _account_constraint(
            float(available_cash or 0),
            float(total_assets or 0),
        ),
        "method": "产业链 -> 瓶颈 -> A股候选 -> 8维评分 -> 红旗过滤 -> 研究报告",
        "disclaimer": "仅用于研究学习，不构成投资建议。",
    }


def _format_evidence_short(items: List[Dict[str, str]]) -> str:
    if not items:
        return "待补证据"
    return "；".join(
        f"{item.get('strength', 'unknown')}:{item.get('fact', '')}"
        for item in items[:2]
    )


def build_serenity_research_report(pipeline: Dict[str, Any]) -> str:
    """Build a standalone markdown report for Obsidian/Siku archiving."""
    candidates = pipeline.get("candidates", [])
    chokepoints = pipeline.get("chokepoints", [])
    title_theme = pipeline.get("theme") or pipeline.get("normalized_theme", "未命名主题")
    lines = [
        f"# Serenity瓶颈选股报告：{title_theme}",
        "",
        f"- 日期: {pipeline.get('report_date', '')}",
        f"- 标准化主题: {pipeline.get('normalized_theme', title_theme)}",
        f"- 方法: {pipeline.get('method', '')}",
        f"- 账户约束: {pipeline.get('account_constraint', '')}",
        "",
        "## 一句话结论",
        "",
    ]

    if candidates:
        top = candidates[0]
        lines.append(
            f"当前内置候选池中，研究优先级最高的是 {top['name']}({top['code']})，"
            f"核心瓶颈是 {top['chokepoint']}，总分 {top['score']}。"
        )
    else:
        lines.append("暂无内置候选池，需要先补产业链卡点和 A 股映射。")

    lines.extend([
        "",
        "## 产业链瓶颈清单",
        "",
        "| 序号 | 瓶颈环节 | 需要核验的数据 |",
        "|:---:|---|---|",
    ])
    if chokepoints:
        for idx, chokepoint in enumerate(chokepoints, start=1):
            lines.append(f"| {idx} | {chokepoint} | 订单、产能、价格、毛利率、客户结构 |")
    else:
        lines.append("| - | 暂无内置卡点 | 需要人工补充行业资料 |")

    lines.extend([
        "",
        "## 候选公司 8 维评分",
        "",
        "| 标的 | 代码 | 研究优先级 | 总分 | 需求 | 瓶颈 | 传导 | 纯度 | 证据 | 忽视 | 验证 | 安全 |",
        "|---|:---:|:---:|---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ])
    if candidates:
        for item in candidates:
            s = item["scores"]
            lines.append(
                f"| {item['name']} | {item['code']} | {item['research_tier']} | {item['score']} "
                f"| {s['需求确定性']} | {s['瓶颈强度']} | {s['传导清晰度']} | {s['业务纯度']} "
                f"| {s['证据强度']} | {s['市场忽视度']} | {s['验证速度']} | {s['下行安全']} |"
            )
    else:
        lines.append("| 暂无 | - | 弱线索 | 0 | - | - | - | - | - | - | - | - |")

    score_adjustment_rows = []
    for item in candidates:
        for adjustment in item.get("score_adjustments", []):
            score_adjustment_rows.append((item, adjustment))
    if score_adjustment_rows:
        lines.extend([
            "",
            "## 评分调整原因",
            "",
            "| 标的 | 维度 | 调整 | 原因 |",
            "|---|---|:---:|---|",
        ])
        for item, adjustment in score_adjustment_rows:
            lines.append(
                f"| {item['name']}({item['code']}) | {adjustment.get('dimension', '')} "
                f"| {adjustment.get('before', '')} -> {adjustment.get('after', '')} "
                f"| {adjustment.get('reason', '')} |"
            )

    lines.extend([
        "",
        "## 标的拆解与红旗",
        "",
    ])
    if candidates:
        for item in candidates:
            flag_text = "无明显红旗"
            if item["red_flags"]:
                flag_text = "；".join(f"{flag['label']}({flag['severity']})" for flag in item["red_flags"])
            lines.extend([
                f"### {item['name']}({item['code']})",
                "",
                f"- 产业链位置: {item['chain_position']}",
                f"- 关键瓶颈: {item['chokepoint']}",
                f"- 证据摘要: {_format_evidence_short(item['evidence_items'])}",
                f"- 红旗: {flag_text}",
                f"- 下一步核验: {item.get('verify_next') or '补充公告、财报、订单和客户证据。'}",
                f"- 处理方式: {'暂不纳入研究清单' if item['actionability'] == 'reject_for_now' else '纳入观察清单'}",
                "",
            ])
    else:
        lines.append("暂无可拆解标的。")

    lines.extend([
        "",
        "## 待核验任务清单",
        "",
        "| 标的 | 优先级 | 核验任务 | 数据源 |",
        "|---|:---:|---|---|",
    ])
    verification_tasks = pipeline.get("verification_tasks", [])
    if verification_tasks:
        for task in verification_tasks[:15]:
            lines.append(
                f"| {task.get('candidate_name', '')}({task.get('candidate_code', '')}) "
                f"| {task.get('priority', '')} | {task.get('task', '')} "
                f"| {task.get('source_label', task.get('source_type', ''))} |"
            )
    else:
        lines.append("| 暂无 | - | 需要先补候选池与证据源 | - |")

    lines.extend([
        "",
        "## 使用边界",
        "",
        "- 本报告是 Serenity 式产业链瓶颈研究笔记，服务知识库学习和后续人工核验。",
        "- 内置候选池是 MVP 种子库，不代表覆盖完整行业，也不替代实时行情、财报、公告和估值校验。",
        f"- {pipeline.get('disclaimer', '仅用于研究学习，不构成投资建议。')}",
    ])
    return "\n".join(lines)


# ============================================================
# 4. 产业链拆解 Prompt 生成
# ============================================================

INDUSTRY_CHAIN_PROMPT_TEMPLATE = """你是「产业链研究员」— Serenity 式供应链瓶颈分析专家。
你在分析当前市场的产业链机会。你的任务是：先排产业链层级，再找供给卡点，最后排序候选标的。

【当前热点与上下文】
{context}

【操作系统变化】
请思考：什么技术和经济变化在驱动需求？旧的系统架构哪里开始不够用了？
最关键的物理/工艺约束是什么（带宽/功率/良率/纯度/散热/产能/认证）？

【产业链地图（8层级）】
1. 终端客户与资本开支源
2. 系统集成商与 OEM
3. 模组与子系统
4. 芯片、器件与关键组件
5. 工艺、组装、封装与测试
6. 设备与计量
7. 材料、耗材与特种输入
8. 基础设施（电力/散热/网络）

【请按以下结构输出分析，JSON格式，不要code fence】
{{
    "perspective": "产业链瓶颈分析",
    "market_story": "当前市场的核心叙事一句话",
    "system_change": "系统正在发生的变化和物理约束",
    "layer_ranking": ["按优先级排序列出的产业链层级"],
    "chokepoints": [
        {{
            "layer": "卡点所在层级",
            "bottleneck": "具体瓶颈描述",
            "difficulty": "扩产难度 (高/中/低)",
            "low_supplier_count": true/false
        }}
    ],
    "recommended_research": [
        {{
            "industry": "方向/环节",
            "reason": "为什么值得优先研究",
            "companies_hint": "可能相关的公司类型或方向",
            "verification": "需要核验什么"
        }}
    ],
    "what_market_misses": "市场可能没看清的地方",
    "false_positive_risk": "什么情况说明这个判断是错的",
    "analysis": "你的分析结论（面向新手，通俗语言）",
    "knowledge_tips": [
        {{"term": "产业链术语", "explanation": "通俗解释"}}
    ]
}}
"""


def build_industry_chain_prompt(context: str) -> str:
    """构建产业链分析 Prompt"""
    return INDUSTRY_CHAIN_PROMPT_TEMPLATE.format(context=context)


# ============================================================
# 5. 证据等级评定
# ============================================================

EVIDENCE_STRENGTH = {
    "strong": {
        "label": "强证据",
        "emoji": "🟢",
        "sources": [
            "交易所文件/公告", "财报/年报/半年报/季报",
            "电话会/IR演示材料", "官方客户合同/订单公告",
            "监管文件/项目备案/环评/能评", "专利/标准/技术文献",
        ],
    },
    "medium": {
        "label": "中等证据",
        "emoji": "🟡",
        "sources": [
            "可信财经媒体", "行业期刊/协会数据",
            "公司官网/产品页面", "卖方/专业研究（假设可见）",
            "供应商/客户交叉公开验证",
        ],
    },
    "weak": {
        "label": "弱证据",
        "emoji": "🔴",
        "sources": [
            "KOL/社交媒体帖子", "论坛讨论",
            "来源不明的截图", "无基本面支撑的价格异动",
        ],
    },
}


def evidence_summary(company: str, evidence_items: List[Dict[str, str]]) -> str:
    """为一家公司生成证据摘要

    Args:
        company: 公司名称
        evidence_items: [{"fact": "...", "strength": "strong|medium|weak", "source": "..."}, ...]

    Returns:
        Markdown 格式的证据摘要
    """
    if not evidence_items:
        return f"{company}: 暂无明确证据"
    lines = [f"**{company} 证据摘要**"]
    for item in evidence_items:
        strength = EVIDENCE_STRENGTH.get(item["strength"], {})
        emoji = strength.get("emoji", "⚪")
        label = strength.get("label", "未知")
        lines.append(f"- {emoji} **{label}**: {item['fact']} — 来源: {item.get('source', '?')}")
    return "\n".join(lines)


# ============================================================
# 6. 完整产业链研究员 Prompt（待 workshop 中注入）
# ============================================================

RESEARCHER_DEBATE_PROMPT = """你是「产业链研究员」— Serenity 式供应链瓶颈分析专家。
你的任务是：先拆产业链层级，找供给卡点，再排序值得研究的标的。
你的分析会和其他三位角色（猎手/账房/守夜人）一起被裁判综合。

⚠️ 重要：受众是股票交易新手。请用通俗语言。
⚠️ 铁律：不要做买入/卖出建议，只做产业链层面对投资研究优先级的排序。
⚠️ 铁律：必须从物理/工艺/产能约束出发，而不是讲故事。
⚠️ 铁律：股票代码必须是6位真实A股代码（沪市60xxxx/688开头，深市00xxxx/30xxxx），严禁编造。

【今日要闻】
{news_context}

【市场数据】
{market_data}

【持仓情况】
{holdings_data}

请给出「产业链研究员」视角的分析 (JSON格式，不要code fence):
{{
    "perspective": "产业链瓶颈分析",
    "analysis": "你的分析（面向新手，通俗语言）",
    "system_change": "系统正在发生的变化和关键物理/工艺约束",
    "layer_ranking": ["按优先级排序列出的产业链层级及理由"],
    "chokepoints": [
        {{
            "layer": "卡点所在层级",
            "bottleneck": "具体瓶颈",
            "difficulty": "扩产难度(高/中/低)",
            "why_matters": "为什么这对投资很重要"
        }}
    ],
    "chokepoint_candidates": [
        {{
            "code": "股票代码",
            "name": "公司/标的名称",
            "constrains": "它卡住的环节",
            "chain_position": "产业链位置",
            "reason": "为什么值得研究",
            "evidence": "现有证据",
            "risk": "主要风险",
            "priority": "高/中/低"
        }}
    ],
    "downgraded_areas": [
        {{
            "area": "被降级的热门方向",
            "reason": "为什么现在优先级不高"
        }}
    ],
    "what_market_misses": "市场可能没看清的地方",
    "danger_signals": ["需要警惕的红旗信号"],
    "knowledge_tips": [
        {{"term": "产业链术语", "explanation": "通俗解释"}}
    ]
}}
"""

# ============================================================
# 7. A 股产业链卡点预检（用于盘前快速扫描）
# ============================================================

def get_theme_chokepoints(theme: str) -> List[str]:
    """获取常见产业链主题的卡点环节"""
    return THEME_CHOKEPOINTS.get(theme, [])


def get_chokepoint_prompt(theme: str = "", news_summary: str = "") -> str:
    """生成产业链卡点预检 Prompt，用于盘前快速扫描"""
    chokepoints_hint = ""
    if theme and theme in THEME_CHOKEPOINTS:
        chokepoints = THEME_CHOKEPOINTS[theme]
        chokepoints_hint = "\n该主题的典型卡点环节：\n" + "\n".join(f"  - {cp}" for cp in chokepoints)

    return f"""你是一位产业链卡点预检分析师。请在盘前对当前市场热点进行快速产业链扫描。

【市场热点】
{news_summary}

【提示卡点】
{chokepoints_hint}

请快速分析：
1. 今天市场上最热的产业链主题是什么？
2. 哪些产业链层级今天最值得关注？
3. 有没有新的供应链信号/事件值得跟踪？

输出JSON格式（简洁版）：
{{{{
    "hot_theme": "最热的产业链主题",
    "chokepoint_layer": "最紧的卡点层级",
    "focus_reason": "为什么这个层级值得关注",
    "trigger_event": "今天的触发信号（如有）",
    "watch_items": ["需要跟踪的方向"],
    "priority": "高/中/低"
}}}}
"""


# ============================================================
# 导出列表
# ============================================================

__all__ = [
    "VALUE_CHAIN_LAYERS",
    "THEME_CHOKEPOINTS",
    "SCORING_DIMENSIONS",
    "SERENITY_SCORING_V2",
    "THEME_CANDIDATES",
    "score_company",
    "score_company_v2",
    "adjust_scores_with_quote_evidence",
    "score_summary_table",
    "RED_FLAGS",
    "check_red_flags",
    "summarize_red_flags",
    "build_industry_chain_prompt",
    "INDUSTRY_CHAIN_PROMPT_TEMPLATE",
    "RESEARCHER_DEBATE_PROMPT",
    "EVIDENCE_STRENGTH",
    "evidence_summary",
    "get_theme_chokepoints",
    "get_theme_candidates",
    "get_chokepoint_prompt",
    "validate_theme_candidate_pool",
    "run_serenity_pipeline",
    "build_serenity_research_report",
]
