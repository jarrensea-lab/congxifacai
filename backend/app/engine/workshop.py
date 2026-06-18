"""② 策略工坊引擎 — AI 辩论 + 风险定级 + 策略决策卡 — V6: DeepSeek 云端"""
import logging
import json
from datetime import datetime

logger = logging.getLogger("congxi")

# 风险等级定义
RISK_LEVELS = {
    1: {"label": "R1 保守", "position_limit": 10, "stop_loss": -2, "stock_types": "ETF/债基"},
    2: {"label": "R2 稳健", "position_limit": 20, "stop_loss": -3, "stock_types": "蓝筹低波动"},
    3: {"label": "R3 适中", "position_limit": 30, "stop_loss": -5, "stock_types": "加入成长股"},
    4: {"label": "R4 积极", "position_limit": 50, "stop_loss": -8, "stock_types": "允许小盘"},
    5: {"label": "R5 激进", "position_limit": 70, "stop_loss": -12, "stock_types": "允许题材博弈"},
}


async def run_debate(analysis_report: dict, strategy_type: str = "premarket") -> dict:
    """执行 AI 辩论 — V6: DeepSeek 云端多模型并行辩论

    Args:
        analysis_report: 阶段①的分析研判报告

    Returns:
        辩论摘要 + 决策卡参数
    """
    from app.ai.debate import AIDebateEngine
    from app.engine.debate_tracker import DebateTracker

    # 从分析报告提取数据
    market = analysis_report.get("market", analysis_report)
    market_data_str = json.dumps(market, ensure_ascii=False)
    holdings_str = analysis_report.get("holdings_str", "无持仓数据")
    if isinstance(holdings_str, list):
        holdings_str = json.dumps(holdings_str, ensure_ascii=False)
    elif isinstance(holdings_str, dict):
        holdings_str = json.dumps(holdings_str, ensure_ascii=False)
    # 注入真实可用现金 — 从 analysis_report 中读取，否则从 market_data 原始数据读取
    available_cash = analysis_report.get("available_cash", 0)
    if not available_cash:
        raw_market = analysis_report.get("_market_data_raw", {})
        available_cash = raw_market.get("available_cash", 0)
    holdings_data = holdings_str
    if available_cash and available_cash > 0:
        holdings_data += f"\n\n【账户资金】\n总资产: ¥{float(available_cash):,.2f}\n可用现金: ¥{float(available_cash):,.2f}\n"
    news_str = json.dumps(analysis_report.get("news", []), ensure_ascii=False)

    engine = AIDebateEngine()

    # 获取辩论历史表现（注入裁判 prompt）
    role_perf = ""
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            role_perf = DebateTracker.get_performance_summary(db)
        finally:
            db.close()
    except Exception:
        pass

    result = await engine.debate(market_data_str, holdings_data, news_str, role_performance=role_perf)

    final = result.get("final", {})
    short_term = final.get("short_term", {})
    mid_low = final.get("mid_low_freq", {})

    stock_pool = short_term.get("recommendations", []) + mid_low.get("recommendations", [])

    decision = {
        "final_view": final.get("final_decision", "N/A"),
        "final_decision": final.get("final_decision", "N/A"),
        "confidence": final.get("confidence", 5),
        "reasoning": final.get("reasoning", ""),
        "stock_pool": stock_pool,
        "position_limit_pct": _extract_limit(final),
        "stop_loss_pct": _extract_stop_loss(final),
        "debate_summary": final.get("reasoning", "N/A")[:150],
        "short_term": short_term,
        "mid_low_freq": mid_low,
        "position_advice": final.get("position_advice", ""),
        "top_sectors": final.get("top_sectors", []),
        "position_plan": final.get("position_plan", {}),
        "backtest_summary": final.get("backtest_summary", {}),
        "risk_summary": final.get("risk_summary", ""),
        "knowledge_corner": final.get("knowledge_corner", ""),
    }

    confidence = final.get("confidence", 5)
    risk_level = min(5, max(1, 6 - confidence))

    # 保存辩论快照用于质量追踪
    try:
        # 从 analysis_report 中提取上证指数涨跌幅
        sh_change = 0
        if isinstance(analysis_report, dict):
            indices = analysis_report.get("indices", {})
            sh_data = indices.get("sh000001", {}) if isinstance(indices, dict) else {}
            if isinstance(sh_data, dict):
                sh_change = sh_data.get("change_pct", 0) or 0
        # engine_result 是 result（来自 AIDebateEngine.debate()）
        # run_debate 的参数 analysis_report 包含 market 数据
        DebateTracker.save(strategy_type, result, sh_change_pct=sh_change)
    except Exception as e:
        logger.warning(f"辩论快照保存异常（不影响主流程）: {e}")

    return {
        "roles": result.get("debate", {}),
        "decision": decision,
        "recommended_risk_level": risk_level,
        "debate_timestamp": str(datetime.now()),
        "quality": result.get("quality", {}),
        "judge_thinking": result.get("judge_thinking", ""),
    }


async def ask_role(role: str, question: str, context: str) -> dict:
    """追问特定角色 — V6: DeepSeek 云端"""
    from app.ai.debate import AIDebateEngine

    role_personas = {
        "hunter": ("猎手", "短线技术分析师，风格偏向进攻"),
        "accountant": ("账房", "估值和趋势分析师，风格稳健"),
        "guardian": ("守夜人", "风险控制专家，风格保守"),
        "judge": ("裁判", "综合决策者，负责最终判断"),
        "researcher": ("Serenity·研究员", "产业链深度分析师，专注供应链和产业逻辑"),
    }
    name, persona = role_personas.get(role, (role, "AI 助手"))
    model_map = {"hunter": "cloud-hunter", "accountant": "cloud-accountant",
                 "guardian": "cloud-guardian", "researcher": "cloud-researcher", "judge": "cloud-judge"}
    model = model_map.get(role, "cloud-judge")

    prompt = f"""你是「{name}」——{persona}。

上下文: {context}

用户追问: {question}

请直接回答用户的问题，给出具体、有依据的回复。可以引用之前分析中的具体数据和逻辑。
不要输出 JSON——直接输出自然语言回答。"""

    engine = AIDebateEngine()
    result = await engine._call_role(role, prompt, model, timeout=120.0)
    return {"role": name, "question": question, "answer": result.get("content", "")}


def _extract_limit(final: dict) -> int:
    pos_plan = final.get("position_plan", {})
    if pos_plan and pos_plan.get("entries"):
        weights = sum(e.get("weight_pct", 0) for e in pos_plan["entries"])
        return min(70, max(10, weights))
    return 30


def _extract_stop_loss(final: dict) -> int:
    pos_plan = final.get("position_plan", {})
    if pos_plan and pos_plan.get("entries"):
        stops = [e.get("stop_loss", {}).get("pct", -5) for e in pos_plan["entries"] if "stop_loss" in e]
    if pos_plan and pos_plan.get("entries"):
        stops_raw = [e.get("stop_loss", {}).get("pct", -5) for e in pos_plan["entries"] if "stop_loss" in e]
    return -5
