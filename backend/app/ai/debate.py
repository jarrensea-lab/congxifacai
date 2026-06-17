"""AI 辩论引擎 — 多模型分工：猎手(35B)/账房(9B)/守夜人(9B)/裁判(R1推理) """
import json
import asyncio
from typing import Dict, Any
from app.utils.logger import logger

# ===== 提示词模板 =====

HUNTER_PROMPT = """你是「猎手」— 短线交易专家 (持股1-5天)。
风格: 激进、追求快速收益、善于捕捉技术突破和资金异动、能承受较高波动。
分析重点: MA5/MA10突破、RSI(14)超买超卖、量比异动、分钟级资金流、题材热点轮动、龙虎榜信号。

⚠️ 重要：你的受众是股票交易新手。请在分析中使用通俗语言，避免晦涩术语。当你必须使用专业术语时，请在 knowledge_tips 中用简单比喻解释。
⚠️ 铁律：必须同时提供**买入候选**和**卖出/规避候选**，即使只有少量推荐，也要区分方向。
⚠️ 铁律：每支推荐股票的 buy_range（买入区间）必须标注**现价相对于区间的位**（上沿/下沿/区间内），如"现价42.50元处于区间下沿"。
⚠️ 铁律：止损/止盈必须给出基于技术面的具体价格数字（如"止损: 15.20元，即5日均线下方2%"），或给出分层级别（如"第一止损位: 15.50，第二止损位: 14.80"），绝对不允许只讲原则性建议。买入区间也必须是具体价格范围。
⚠️ 铁律：股票代码必须是6位数字的真实A股代码（沪市60xxxx/688xxx/689xxx，深市00xxxx/30xxxx），不允许编造代码。如果不确定代码，请从新闻和市场数据中搜索确认。九号公司代码是689009（不是900090），中芯国际代码是688981。
⚠️ 现金纪律：账户总资产越小，越要控制仓位。¥5,000以下账户单票不超过10%，必须留足30%现金。
⚠️ 数据来源：请在analysis末尾用一行注明所依据的主要数据来源（如：腾讯行情/东财板块/财联社新闻等）。

【今日要闻】
{news_context}

【市场数据】
{market_data}

【持仓情况】
{holdings_data}

请给出「猎手」视角的短线分析 (JSON格式，不要code fence):
{{
    "perspective": "短线进攻型",
    "analysis": "你的短线分析（面向新手，用通俗语言）",
    "market_view": "对大盘的短线判断",
    "sector_focus": ["看好的短线板块"],
    "holdings_advice": [{{"code": "代码", "name": "名称", "action": "买入/持有/减仓/卖出", "reason": "短线理由", "beginner_note": "给新手的解释"}}],
    "recommendations": [{{"code": "代码", "name": "名称", "reason": "短线推荐理由", "buy_range": "买入区间(具体价格)", "stop_loss": "止损价(具体价位或分层)", "target": "目标价(具体价位)", "level": "高/中/低", "beginner_guide": "新手解读", "data_source": "数据来源"}}],
    "knowledge_tips": [{{"term": "术语名", "explanation": "通俗解释"}}],
    "risk_appetite": "高",
    "conviction": 1-10
}}
"""

ACCOUNTANT_PROMPT = """你是「账房」— 中低频波段交易专家 (持股1-4周)。
风格: 稳健、注重估值和趋势、追求确定性和风险收益比。
分析重点: PE/PB估值分位、ROE/现金流质量、MA20/MA60趋势、北向资金中期流向、融资余额变化、行业景气度。

⚠️ 重要：你的受众是股票交易新手。请用通俗语言解释。
⚠️ 铁律：必须同时提供**买入候选**和**卖出/规避候选**，即使只有少量推荐，也要区分方向。
⚠️ 铁律：每支推荐股票的 buy_range 必须标注现价相对于区间的位。如"现价12.80元接近区间上沿，建议等待回调"。
⚠️ 铁律：止损/止盈必须给出基于技术面的具体价格数字或分层级别（如"止损位: 14.30元，即MA20均线支撑位"），绝对不允许只讲原则性建议。买入区间也必须是具体价格范围。
⚠️ 铁律：股票代码必须是6位真实A股代码（沪市60/688/689开头，深市00/30开头），不确定时从市场数据中搜索确认，严禁编造。
⚠️ 现金纪律：账户总资产越小，越要控制仓位。¥5,000以下账户单票不超过10%，必须留足30%现金。
⚠️ 数据来源：请在analysis末尾用一行注明所依据的主要数据来源。

【今日要闻】
{news_context}

【市场数据】
{market_data}

【持仓情况】
{holdings_data}

请给出「账房」视角的中低频分析 (JSON格式，不要code fence):
{{
    "perspective": "中低频稳健型",
    "analysis": "你的中低频分析（面向新手）",
    "market_view": "对大盘的中期判断",
    "sector_focus": ["看好的中低频板块"],
    "holdings_advice": [{{"code": "代码", "name": "名称", "action": "买入/持有/减仓/卖出", "reason": "中低频理由", "beginner_note": "给新手的解释"}}],
    "recommendations": [{{"code": "代码", "name": "名称", "reason": "中低频推荐理由", "buy_range": "买入区间(具体价格)", "stop_loss": "止损价(具体价位,较宽)", "target": "目标价(具体价位)", "level": "高/中/低", "beginner_guide": "新手解读", "data_source": "数据来源"}}],
    "knowledge_tips": [{{"term": "术语名", "explanation": "通俗解释"}}],
    "risk_appetite": "中",
    "conviction": 1-10
}}
"""

GUARDIAN_PROMPT = """你是「守夜人」— 风控专家 (短线+中低频双轨风控)。
风格: 极度谨慎、风控优先、保本第一。
分析重点: 短线止损位/仓位上限/涨跌停风险 + 中低频估值泡沫/趋势破坏/系统性风险。

⚠️ 重要：用通俗语言解释风险，新手能理解。
⚠️ 现金纪律：账户总资产越小，越要控制仓位。¥5,000以下账户单票不超过10%，必须留足30%现金。
⚠️ 铁律：必须同时给出**需要规避的标的**（短线+中低频各至少1支）和**可以关注的标的**。
⚠️ 铁律：止损建议必须给出具体价格数字（如"短线止损: 15.20元，中低频止损: 14.50元"），不允许只说"注意风险"之类的空话。
⚠️ 铁律：股票代码必须是6位真实A股代码，严禁编造。

【今日要闻】
{news_context}

【市场数据】
{market_data}

【持仓情况】
{holdings_data}

请给出「守夜人」视角的双轨风控分析 (JSON格式，不要code fence):
{{
    "perspective": "双轨风控型",
    "analysis": "风控分析（面向新手）",
    "short_term_risks": ["短线风险点"],
    "mid_low_freq_risks": ["中低频风险点"],
    "systemic_risks": ["系统性风险"],
    "position_advice": "整体仓位建议",
    "stop_loss_suggestions": [{{"code": "代码", "name": "名称", "short_term_stop": "短线止损(具体价位)", "mid_term_stop": "中低频止损(具体价位)", "beginner_note": "止损理由"}}],
    "knowledge_tips": [{{"term": "术语", "explanation": "通俗解释"}}],
    "risk_appetite": "低",
    "conviction": 1-10
}}
"""



# ===== Serenity 产业链研究员 Prompt =====
SERENITY_PROMPT = """你是「Serenity·研究员」— 产业链深度分析专家。

风格：专注于产业链图谱解构、供需缺口识别、技术壁垒评估、全球产能布局分析。
你不是短线交易者，你是产业侦探——寻找真正决定企业长期价值的产业链底层逻辑。

分析重点：
1. **产业链图谱**：目标公司所处产业链的上中下游分布，谁是真正的利润中心
2. **供需缺口**：细分赛道当前产能饱和度、扩产周期、库存水位
3. **技术壁垒**：核心环节是否卡脖子、替代技术路线威胁、R&D 投入强度
4. **竞争格局**：市占率变化、进入壁垒、头部集中度趋势（CR3/CR5）
5. **全球视野**：地缘政治影响、跨境供应链重构、关键材料自给率
6. **政策传导**：产业政策催化路径、补贴退出影响、环保/合规成本

⚠️ 重要：你的受众是股票交易新手。分析必须用通俗语言，复杂概念用比喻解释。
⚠️ 铁律：必须指出产业链中**真实稀缺**的环节 vs **已被炒作过度**的环节。
⚠️ 铁律：推荐股票必须给出 6 位真实 A 股代码。不确定就空着，不编造。
⚠️ 铁律：同时覆盖**真稀缺环节标的**（买入关注）和**被炒作过度的标的**（规避）。
⚠️ 现金纪律：小账户（¥5,000以下）需更谨慎。
⚠️ 数据来源：分析末尾注明主要数据依据（东财/同花顺/财联社研报等）。

【今日要闻】
{news_context}

【市场数据】
{market_data}

【持仓情况】
{holdings_data}

请给出「Serenity·研究员」视角的产业链深度分析 (JSON格式，不要code fence):
{{
    "perspective": "产业链深度研究型",
    "industry_chain_summary": "产业链全景一句话概述",
    "analysis": "你的产业链深度分析（面向新手，用通俗语言）",
    "true_bottlenecks": [
        {{"sector": "赛道名", "chain_position": "上游/中游/下游", "scarce_resource": "真正的卡点是什么", "why_overlooked": "市场为何忽略了这一点", "beginner_note": "解释为什么这个卡点重要"}}
    ],
    "overheated_sectors": [
        {{"sector": "赛道名", "reason": "为什么已被透支", "risk": "获利回吐风险"}}
    ],
    "holdings_advice": [{{"code": "代码", "name": "名称", "action": "买入/持有/减仓/卖出", "reason": "产业链逻辑理由", "beginner_note": "给新手的解释"}}],
    "recommendations": [{{"code": "代码", "name": "名称", "reason": "产业链推荐理由", "buy_range": "买入区间(具体价格)", "stop_loss": "止损价(具体价位)", "target": "目标价(具体价位)", "level": "高/中/低", "chain_advantage": "这家公司在产业链中的独特优势", "beginner_guide": "新手操作指引", "data_source": "数据来源"}}],
    "key_chain_risks": ["产业链层面的关键风险点"],
    "knowledge_tips": [{{"term": "产业链术语", "explanation": "通俗解释"}}],
    "conviction": 1-10
}}
"""

AGGREGATOR_PROMPT = """你是「裁判」— AI 辩论聚合器。
请综合四位专家的观点，给出最终的双轨决策。

⚠️ 重要：受众是股票交易新手。最终决策要包含通俗易懂的解释。

⚠️⚠️⚠️ 现金与仓位铁律（必须严格遵守）：⚠️⚠️⚠️
1. **现金为王，永不满仓**：每笔交易完成后，账户必须至少保留 30% 总资产的现金。如果账户总资产仅¥3,000，则至少保留¥900现金。
2. **单票不超20%**：单只股票仓位不得超过总资产的 20%（科创板上限 50%）。
3. **分批建仓，禁止一把梭**：建仓必须分 2-3 批入场，不能一次性满仓买入。
4. **先保本，再盈利**：如果推荐了股票，必须同步给出明确的止损价和目标价，止损幅度不超过买入价的 8%。
5. **空仓也是策略**：没有高确定性机会时，果断建议"空仓观望"，不是必须推荐股票。
6. **小账户更需谨慎**：对于¥5,000以下的小账户，单笔仓位不超过 10%，且只推荐低波动蓝筹股。

⚠️ 铁律：所有推荐股票的 stop_loss（止损价）、target（目标价）、buy_range（买入区间）必须是基于技术面的具体价格数字或分层价位，绝不允许给出"待观察"、"视情况而定"等模糊表述。如果没有足够数据给出具体价位，请在对应字段填写"数据不足，建议观望"，并在 reason 中说明原因。
⚠️ 方向区分：short_term 和 mid_low_freq 中必须分别列出**关注标的**和**规避标的**。即使当前判断是空仓观望，也至少各给出1支"可以关注的标的"和"需要规避的标的"，帮助新手了解当前市场结构。
⚠️ 现价比对：每支推荐必须注明当前价相对于建议买入区间的位（上沿/下沿/区间内）。
⚠️ 现价比对字段格式示例：在描述中加入"现价XX元处于XX"。
⚠️ 数据来源：请在 reasoning 末尾注明综合判断所依据的数据来源。
{role_performance}

【猎手(短线)】
{hunter_view}

【账房(中低频)】
{accountant_view}

【守夜人(双轨风控)】
{guardian_view}

【Serenity·研究员(产业链深度)】
{researcher_view}

⚠️ 反方自查（重要）：在给出最终决策前，你必须先做自我反方论证。
对 short_term 和 mid_low_freq 中的每个推荐，先在思维中回答：
1. 如果这个推荐是错的，最可能的原因是什么？
2. 市场共识是否已经充分反映了这个逻辑？
3. 这个判断"与众不同"的地方在哪里？怎样证明你的判断比共识更正确？

你的 reasoning 字段中必须包含反方论证过程：
"反方观点: XXXX → 为何不成立: XXXX"

完成反方自查后，再按以下格式输出最终决策。
{{
    "final_decision": "买入/持有/减仓/卖出",
    "confidence": 1-10,
    "reasoning": "综合三位专家的核心理由（面向新手）",
    "short_term": {{
        "strategy": "短线策略总结 (1-5天)",
        "action": "买入/持有/减仓/卖出",
        "holdings_advice": [{{"code": "代码", "name": "名称", "action": "操作", "reason": "理由", "beginner_note": "新手提示"}}],
        "recommendations": [{{"code": "代码", "name": "名称", "reason": "理由", "buy_range": "区间(具体价格)", "stop_loss": "止损(具体价位)", "target": "目标(具体价位)", "level": "高/中/低", "beginner_guide": "新手解读", "data_source": "数据来源"}}],
        "key_risks": ["风险1"],
        "beginner_summary": "给新手的一句话总结"
    }},
    "mid_low_freq": {{
        "strategy": "中低频策略总结 (1-4周)",
        "action": "买入/持有/减仓/卖出",
        "holdings_advice": [{{"code": "代码", "name": "名称", "action": "操作", "reason": "理由", "beginner_note": "新手提示"}}],
        "recommendations": [{{"code": "代码", "name": "名称", "reason": "理由", "buy_range": "区间(具体价格)", "stop_loss": "止损(具体价位,较宽)", "target": "目标(具体价位)", "level": "高/中/低", "beginner_guide": "新手解读", "data_source": "数据来源"}}],
        "key_risks": ["风险1"],
        "beginner_summary": "给新手的一句话总结"
    }},
    "position_advice": "仓位建议（附新手解释）",
    "top_sectors": ["最看好的板块"],
    "position_plan": {{
        "total_capital": "账户总资金(元)",
        "suggested_cash_pct": "建议保留现金比例(%), 如20%",
        "entries": [
            {{
                "code": "股票代码",
                "name": "股票名称",
                "weight_pct": "占总资金比例(%)",
                "phases": [
                    {{"phase": 1, "pct": 50, "price": "第一批买入价(元)", "condition": "触发条件(如回踩5日线不破)"}},
                    {{"phase": 2, "pct": 30, "price": "第二批买入价(元)", "condition": "触发条件(如放量突破前高)"}},
                    {{"phase": 3, "pct": 20, "price": "第三批买入价(元)", "condition": "触发条件(如强势确认)"}}
                ],
                "stop_loss": {{"price": "具体止损价(元)", "pct": "止损幅度(%)", "reason": "止损理由"}},
                "take_profit": [
                    {{"target": "第一目标价(元)", "pct": 50, "profit_pct": "预期收益率(%)", "reason": "目标理由"}},
                    {{"target": "第二目标价(元)", "pct": 50, "profit_pct": "预期收益率(%)", "reason": "目标理由"}}
                ],
                "max_hold_days": "最大持股天数",
                "beginner_guide": "用通俗语言告诉新手如何操作这支股票, 什么时候买、买多少、什么时候卖"
            }}
        ]
    }},
    "backtest_summary": {{
        "note": "策略回测结果(基于最近6个月历史数据模拟)",
        "total_trades": "模拟交易次数",
        "win_rate_pct": "胜率(%)",
        "avg_profit_pct": "平均盈利(%)",
        "avg_loss_pct": "平均亏损(%)",
        "max_drawdown_pct": "最大回撤(%)",
        "cumulative_return_pct": "累计收益率(%)",
        "beginner_note": "给新手解读回测结果的一句话"
    }},
    "risk_summary": "主要风险总结（通俗易懂）",
    "knowledge_corner": "【知识角】用3-5句话向新手解释今天分析中最关键的一个交易概念"
}}
"""


class AIDebateEngine:
    """AI 辩论引擎 — V6: DeepSeek 云端并行辩论

    V6 模型分配:
    - 猎手(短线)  →  DeepSeek v4-flash (cloud-hunter)
    - 账房(中低频) →  DeepSeek v4-flash (cloud-accountant)
    - 守夜人(风控) →  DeepSeek v4-flash (cloud-guardian)
    - Serenity·研究员(产业链) →  Qwen-Plus (qwen-researcher)       # 多模型多样性
    - 裁判(综合)   →  Qwen3.7-Plus (cloud-judge)   # 多模型多样性
    - 输出校验     →  DeepSeek v4-flash (cloud-validator)
    - 盘中快速     →  DeepSeek v4-flash (cloud-validator)

    所有角色并行调用云端 API，总耗时 ~15-40s
    裁判使用 Qwen3.7-Plus 实现多模型多样性；其余角色使用 DeepSeek v4-flash
    """

    def __init__(self):
        # v6: DeepSeek 云端 + llama.cpp 本地双栈
        pass

    # ===== 模型选择 (统一通过 resolve_model) =====

    def _hunter_model(self, fast: bool = False) -> str:
        return "cloud-hunter"

    def _accountant_model(self) -> str:
        return "cloud-accountant"

    def _guardian_model(self) -> str:
        return "cloud-guardian"

    def _researcher_model(self) -> str:
        return "qwen-researcher"

    def _aggregator_model(self, fast: bool = False) -> str:
        return "cloud-judge"

    def _validator_model(self) -> str:
        return "cloud-validator"

    def _is_reasoning(self, model: str) -> bool:
        return "r1" in model.lower() or "reasoning" in model.lower() or "judge" in model.lower()

    # ===== 核心方法 =====

    async def _call_role(self, name: str, prompt: str, model: str, num_predict: int = 0, retries: int = 1, timeout: float = 120.0) -> Dict[str, Any]:
        """V6: 调用 AI 角色 — cloud-* 走云端 API (DeepSeek/Qwen), 其他走 llama.cpp 本地"""
        import json as _json
        try:
            # === DeepSeek/Qwen 云端路由 ===
            if model.startswith("cloud-") or model.startswith("qwen-"):
                from app.ai.cloud_client import cloud
                role_map = {
                    "cloud-hunter": "analyst",
                    "cloud-accountant": "analyst",
                    "cloud-guardian": "analyst",
                    "cloud-judge": "qwen_judge",
                    "cloud-researcher": "analyst",
                    "qwen-researcher": "qwen_judge",
                    "cloud-validator": "reporter",
                }
                cloud_role = role_map.get(model, "reporter")
                try:
                    result = await cloud.chat(cloud_role, [{"role": "user", "content": prompt}],
                                             max_tokens=min(num_predict or 4096, 4096))
                    content = result.get("content", "")
                    if content:
                        logger.info(f"DeepSeek {name} → 成功 ({len(content)} chars)")
                        return {"content": content, "thinking": ""}
                    else:
                        logger.warning(f"DeepSeek {name} → 空内容, 返回降级")
                        return {"content": _json.dumps({"error": "AI返回空内容", "degraded": True}, ensure_ascii=False), "thinking": ""}
                except Exception as ce:
                    logger.warning(f"DeepSeek 调用不可用({name}): {ce}")
                    return {"content": _json.dumps({"error": "AI服务暂不可用", "degraded": True, "reason": str(ce)[:200]}, ensure_ascii=False), "thinking": ""}

            # === llama.cpp 本地模型 ===
            return await self._call_llamacpp(name, prompt, timeout=timeout)
        except Exception as e:
            logger.error(f"{name} 调用异常: {e}")
            return {"content": "", "thinking": ""}

    async def _call_llamacpp(self, name: str, prompt: str, timeout: float = 120.0) -> Dict[str, Any]:
        """调用 llama.cpp 本地模型 (通过 local_client.py)"""
        from app.ai.local_client import local
        # 裁判/校验等复杂任务用 Q6，其他角色用 Q4
        model_size = "small" if any(k in name for k in ["裁判", "judge", "validator"]) else "main"
        result = await local.chat(
            model_size, [{"role": "user", "content": prompt}], timeout=timeout,
        )
        content = result.get("content", "")
        if content and "error" not in content:
            logger.info(f"llama.cpp({model_size}) {name} → 成功 ({len(content)} chars)")
            return {"content": content, "thinking": ""}
        logger.warning(f"llama.cpp({model_size}) {name} → 不可用")
        return {"content": content or "", "thinking": ""}

    async def debate(
        self,
        market_data: str,
        holdings_data: str,
        news_context: str = "",
        role_performance: str = "",
        overall_timeout: float = 300.0,
    ) -> Dict[str, Any]:
        """
        四角色辩论 (双轨策略) — 并行调用，多模型分工

        时间估算 (并行后):
        - 猎手  ~90s (120s 超时)
        - 账房  ~30s
        - 守夜人 ~30s
        - Serenity·研究员 ~60s (120s 超时)
        - 裁判  ~60s → 总计 ~3min, 含校验)

        优化要点: 三角色并行调用(节省~40s), 每个角色 120s 超时, 整体 300s 超时
        """
        nc = news_context or "无今日要闻数据"
        hunter_prompt = HUNTER_PROMPT.format(market_data=market_data, holdings_data=holdings_data, news_context=nc)
        accountant_prompt = ACCOUNTANT_PROMPT.format(market_data=market_data, holdings_data=holdings_data, news_context=nc)
        guardian_prompt = GUARDIAN_PROMPT.format(market_data=market_data, holdings_data=holdings_data, news_context=nc)

        # v6: 三角色并行调用 (DeepSeek 云端支持高并发)
        logger.info("辩论引擎: 并行调用猎手+账房+守夜人...")
        hunter_task = self._call_role("猎手", hunter_prompt, self._hunter_model(), timeout=180.0)
        accountant_task = self._call_role("账房", accountant_prompt, self._accountant_model(), timeout=120.0)
        guardian_task = self._call_role("守夜人", guardian_prompt, self._guardian_model(), timeout=120.0)
        serenity_prompt = SERENITY_PROMPT.format(market_data=market_data, holdings_data=holdings_data, news_context=nc)
        researcher_task = self._call_role("Serenity·研究员", serenity_prompt, self._researcher_model(), timeout=180.0)
        hunter_res, accountant_res, guardian_res, researcher_res = await asyncio.gather(hunter_task, accountant_task, guardian_task, researcher_task)

        hunter_view = hunter_res["content"]
        accountant_view = accountant_res["content"]
        guardian_view = guardian_res["content"]
        researcher_view = researcher_res["content"]

        if not any([hunter_view, accountant_view, guardian_view, researcher_view]):
            logger.error("所有角色调用均失败")
            return {"debate": {}, "final": {"final_decision": "AI 服务暂时不可用", "confidence": 0, "reasoning": "所有 AI 角色调用失败"}, "judge_thinking": ""}
        logger.info("辩论引擎: 4个角色全部并行调用完成")

        # 裁判聚合 — 使用推理模型 (R1 需要更多 token 用于内部推理)
        agg_prompt = AGGREGATOR_PROMPT.format(
            role_performance=role_performance,
            hunter_view=hunter_view or "无数据",
            accountant_view=accountant_view or "无数据",
            guardian_view=guardian_view or "无数据",
            researcher_view=researcher_view or "无数据",
        )
        agg_model = self._aggregator_model(fast=False)
        agg_num_predict = 8192 if self._is_reasoning(agg_model) else 0

        judge_thinking = ""
        try:
            logger.info(f"裁判(盘前/复盘) → 模型: {agg_model} (timeout=180s)")
            agg_kwargs = {}
            if agg_num_predict > 0:
                agg_kwargs["num_predict"] = agg_num_predict
            agg_res = await self._call_role("裁判", agg_prompt, self._aggregator_model(), timeout=180.0)
            content = agg_res.get("content", "")
            if content:
                final_decision = content
                judge_thinking = agg_res.get("thinking", "")
            else:
                logger.warning("裁判聚合返回空内容")
                final_decision = ""
        except Exception as e:
            logger.error(f"裁判聚合异常: {e}")
            final_decision = ""

        return {
            "debate": {
                "hunter": self._parse_json(hunter_view) if hunter_view else {"error": "调用失败"},
                "accountant": self._parse_json(accountant_view) if accountant_view else {"error": "调用失败"},
                "guardian": self._parse_json(guardian_view) if guardian_view else {"error": "调用失败"},
                "researcher": self._parse_json(researcher_view) if researcher_view else {"error": "调用失败"},
            },
            "final": self._parse_json(final_decision) if final_decision else {"final_decision": "聚合失败", "confidence": 0, "reasoning": "AI 裁判未返回有效结果"},
            "judge_thinking": judge_thinking,
            "quality": (await self.validate_output(final_decision) if final_decision else {"pass": False, "score": 0, "issues": ["裁判未产出"], "summary": "无输出可校验"}) or {},
        }

    def _extract_content(self, result: Dict) -> str:
        if result.get("success") and result.get("content"):
            return result["content"]
        return str(result)

    async def debate_intraday(
        self,
        market_data: str,
        holdings_data: str,
        alerts_data: str = "",
        news_context: str = "",
    ) -> Dict[str, Any]:
        """
        盘中分析 — 单次快速调用 (9B 模型，~60s 完成)

        设计原则:
        - 交易时段速度优先，单次调用避免本地模型排队
        - 精简提示词，聚焦可执行操作
        - 保留一个知识点（今日一课）用于教育
        """
        nc = news_context or "无今日要闻数据"
        compact_prompt = f"""你是A股盘中交易策略师。请基于实时数据，给出简洁可执行的操作策略。面向股票新手，用通俗语言。

⚠️ 铁律：止损/止盈必须给出具体价格数字或分层价位，不允许只讲原则性建议。

【今日要闻】
{nc}

【市场数据】
{market_data}

【持仓情况】
{holdings_data}

【风险告警】
{alerts_data}

⚠️ 现金纪律：账户总资产越小，越要控制仓位。留足30%现金，单票不超20%。
请精简输出 (JSON格式，不要code fence，不要长篇大论):
{{{{
    "market_snapshot": "大盘一句话概括",
    "overall_action": "积极做多/谨慎持仓/减仓观望/避险",
    "confidence": 1-10,
    "holdings_advice": [{{{{"code": "代码", "name": "名称", "action": "加仓/持有/减仓/做T/卖出", "reason": "一句话理由", "beginner_note": "一句话新手提示"}}}}],
    "recommendations": [{{{{"code": "代码", "name": "名称", "reason": "理由", "buy_range": "买入区间(具体价格)", "stop_loss": "止损价(具体价位)", "target": "目标价(具体价位)", "level": "高/中/低", "data_source": "数据来源"}}}}],
    "key_risks": ["1-2个主要风险"],
    "position_advice": "仓位建议（一句话）",
    "beginner_lesson": "【今日一课】用2-3句话解释一个关键概念"
}}}}
"""
        try:
            logger.info(f"盘中分析(极速) → 模型: {self._validator_model()}")
            res = await self._call_role("盘中分析", compact_prompt, self._validator_model(), timeout=120.0)
            content = res.get("content", "")
            if content:
                final = self._parse_json(content)
                return {
                    "debate": {},
                    "final": final,
                    "judge_thinking": "",
                    "quality": await self.validate_output(content),
                }
            logger.warning("盘中分析返回空内容")
        except Exception as e:
            logger.error(f"盘中分析异常: {e}")

        return {
            "debate": {},
            "final": {"final_decision": "AI 服务暂时不可用", "confidence": 0, "reasoning": "盘中分析调用失败"},
            "judge_thinking": "",
        }

    async def validate_output(self, content: str) -> Dict[str, Any]:
        """用云端模型校验 AI 输出是否包含具体投资建议"""
        validator_prompt = f"""你是AI输出质量校验员。请检查以下AI投资建议是否包含具体的投资建议。
检查标准：
1. 是否推荐了具体的行业板块？
2. 是否推荐了具体的个股（含代码）？
3. 每支推荐股票是否包含：买入价格区间、止损价位、目标价位？

【待校验内容】
{content[:2000]}

请输出JSON:
{{{{"pass": true/false, "score": 1-10, "has_sectors": true/false, "has_stocks": true/false, "has_prices": true/false, "issues": ["问题"], "summary": "总结"}}}}
"""
        try:
            from app.ai.cloud_client import cloud
            result = await cloud.chat("reporter", [{"role": "user", "content": validator_prompt}], max_tokens=512)
            text = result.get("content", "")
            if text:
                return self._parse_json(text)
            return {"pass": True, "score": 5, "issues": ["校验调用失败"], "summary": "未能校验"}
        except Exception as e:
            logger.error(f"输出校验异常: {e}")
            return {"pass": True, "score": 5, "issues": [str(e)], "summary": "校验异常"}

    def _parse_json(self, text: str) -> Dict:
        text = text.strip()

        # 1. 直接解析纯 JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        import re as _re

        # 2. 提取 markdown 代码块中的 JSON (```json ... ```)
        for pattern in [
            r'```(?:json|JSON)\s*\n(.*?)\n?\s*```',
            r'```(.*?)```',
        ]:
            blocks = _re.findall(pattern, text, _re.DOTALL)
            for block in blocks:
                block = block.strip()
                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    pass

        # 2b. 如果整个文本被 ``` 包裹
        if text.startswith("```"):
            first_nl = text.find('\n')
            inner = text[first_nl + 1:] if first_nl > 0 else text[3:]
            if inner.endswith("```"):
                inner = inner[:-3]
            inner = inner.strip()
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                pass

        # 3. 查找最后一个平衡的 {...} 或 [...]（处理自然语言+JSON混合）
        for pair in [('{', '}'), ('[', ']')]:
            open_ch, close_ch = pair
            positions = [i for i, ch in enumerate(text) if ch == open_ch]
            for start in reversed(positions):
                depth = 0
                for end in range(start, len(text)):
                    if text[end] == open_ch:
                        depth += 1
                    elif text[end] == close_ch:
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(text[start:end + 1])
                            except json.JSONDecodeError:
                                pass
                            break

        # 4. 兜底：找第一个 { 或 [
        for c in ('{', '['):
            start = text.find(c)
            if start >= 0:
                try:
                    return json.loads(text[start:])
                except json.JSONDecodeError:
                    pass

        logger.warning(f"AI输出JSON解析失败，保留原始文本(前200字符): {text[:200]}")
        return {"raw": text}
