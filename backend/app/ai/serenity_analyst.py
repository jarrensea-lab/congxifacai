"""
Serenity 产业链分析引擎 — 供应链瓶颈分析 + 7 维评分 + 红旗信号检测

灵感来自 Serenity / @aleabitoreddit 的供应链瓶颈研究方法论。
将市场故事拆解为系统变化 → 产业链层级 → 稀缺层 → 候选公司 → 证据 → 风险。

设计原则:
- 先排产业链层级，再排公司
- 证据为导向，社交媒体内容仅作线索
- 输出研究优先级排序，不做买入/卖出建议
"""

from typing import Dict, List, Optional, Any

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
    "score_company",
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
    "get_chokepoint_prompt",
]
