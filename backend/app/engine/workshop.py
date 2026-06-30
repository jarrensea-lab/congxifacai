"""② 策略工坊引擎 — AI 辩论 + 风险定级 + 策略决策卡 — V6: DeepSeek 云端"""
import logging
import json
import re
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
    total_assets = _extract_total_assets(analysis_report, available_cash)
    holdings_data = holdings_str
    if available_cash and available_cash > 0:
        holdings_data += (
            f"\n\n【账户资金】\n"
            f"总资产: ¥{float(total_assets):,.2f}\n"
            f"可用现金: ¥{float(available_cash):,.2f}\n"
        )
    strategy_profile = analysis_report.get("strategy_profile") or {}
    if strategy_profile:
        holdings_data += (
            f"\n【当前策略模式】\n"
            f"模式: {strategy_profile.get('title', '未指定')}\n"
            f"目标: {strategy_profile.get('target', '未指定')}\n"
            f"账户最大回撤: -{strategy_profile.get('max_drawdown_pct', '?')}%\n"
            f"单票上限: {strategy_profile.get('single_position_limit_pct', '?')}%\n"
            f"现金底线: {strategy_profile.get('cash_reserve_pct', '?')}%\n"
            f"单笔硬止损: {strategy_profile.get('stop_loss_pct', '?')}%\n"
            "报告中的仓位、止损、现金底线必须以上述当前策略模式为准；"
            "不要沿用旧的30%现金底线或10%单票上限，除非当前策略模式明确如此。\n"
        )
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

    final = _repair_final_decision(result)
    short_term = final.get("short_term", {})
    mid_low = final.get("mid_low_freq", {})

    stock_pool = short_term.get("recommendations", []) + mid_low.get("recommendations", [])

    holdings_codes = _extract_holding_codes(analysis_report)
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
    decision = _apply_account_constraints(
        decision,
        available_cash=available_cash,
        holdings_codes=holdings_codes,
        total_assets=total_assets,
    )

    risk_level = max(
        _derive_risk_level(decision, available_cash=available_cash),
        _derive_portfolio_risk(analysis_report),
    )

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

    # Sentinel 角色绩效评审旁路留痕：失败不影响成熟辩论/日报流程。
    try:
        from app.ai import sentinel_role_performance

        sentinel_role_performance.record_debate_predictions(
            result,
            decision=decision,
            strategy_type=strategy_type,
            source_report=str(analysis_report.get("source_report", "")) if isinstance(analysis_report, dict) else "",
        )
    except Exception as e:
        logger.warning(f"Sentinel角色预测留痕异常（不影响主流程）: {e}")

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


def _repair_final_decision(result: dict) -> dict:
    """Return a usable final decision even when the judge emits invalid JSON."""
    final = result.get("final", {}) or {}
    if final.get("final_decision") and final.get("final_decision") != "N/A":
        return final

    debate = result.get("debate", {}) or {}
    hunter = debate.get("hunter", {}) if isinstance(debate.get("hunter", {}), dict) else {}
    accountant = debate.get("accountant", {}) if isinstance(debate.get("accountant", {}), dict) else {}
    guardian = debate.get("guardian", {}) if isinstance(debate.get("guardian", {}), dict) else {}
    researcher = debate.get("researcher", {}) if isinstance(debate.get("researcher", {}), dict) else {}

    advice_items = []
    for role_data in (hunter, accountant, researcher):
        items = role_data.get("holdings_advice", [])
        if isinstance(items, list):
            advice_items.extend(items)

    action_text = json.dumps(advice_items, ensure_ascii=False) + str(guardian.get("position_advice", ""))
    if any(word in action_text for word in ("清仓", "卖出", "减仓", "降仓")):
        decision = "减仓"
    elif any(word in action_text for word in ("买入", "加仓")):
        decision = "买入"
    elif advice_items:
        decision = "持有"
    else:
        decision = "观望"

    convictions = []
    for role_data in (hunter, accountant, guardian, researcher):
        try:
            convictions.append(float(role_data.get("conviction", 0) or 0))
        except (TypeError, ValueError):
            pass
    confidence = int(round(sum(convictions) / len(convictions))) if convictions else 5
    confidence = min(8, max(4, confidence))

    bottlenecks = researcher.get("true_bottlenecks", [])
    top_sectors = []
    if isinstance(bottlenecks, list):
        top_sectors = [
            item.get("sector", "") for item in bottlenecks
            if isinstance(item, dict) and item.get("sector")
        ][:3]

    guardian_risks = guardian.get("systemic_risks", []) or guardian.get("short_term_risks", [])
    if isinstance(guardian_risks, list):
        risk_summary = "；".join(str(r) for r in guardian_risks[:3])
    else:
        risk_summary = str(guardian_risks or "")

    return {
        "final_decision": decision,
        "confidence": confidence,
        "reasoning": (
            "裁判输出未形成可解析JSON，系统根据四角色结构化观点生成保守决策。"
            f"守夜人仓位意见: {guardian.get('position_advice', '暂无')}；"
            f"Serenity产业链关注: {', '.join(top_sectors) or '暂无明确卡点'}。"
        ),
        "short_term": {
            "strategy": hunter.get("market_view", hunter.get("analysis", "")),
            "action": decision,
            "holdings_advice": hunter.get("holdings_advice", []),
            "recommendations": hunter.get("recommendations", []),
            "key_risks": hunter.get("key_risks", []),
        },
        "mid_low_freq": {
            "strategy": accountant.get("market_view", accountant.get("analysis", "")),
            "action": decision,
            "holdings_advice": accountant.get("holdings_advice", []),
            "recommendations": accountant.get("recommendations", []),
            "key_risks": accountant.get("key_risks", []),
        },
        "position_advice": guardian.get("position_advice", ""),
        "top_sectors": top_sectors,
        "position_plan": {"entries": []},
        "risk_summary": risk_summary,
        "knowledge_corner": "",
    }


def _derive_risk_level(final: dict, available_cash: float = 0) -> int:
    """Derive strategy risk level from confidence, data quality, and account constraints."""
    try:
        confidence = float(final.get("confidence", 5) or 5)
    except (TypeError, ValueError):
        confidence = 5

    risk_level = min(5, max(1, int(round(6 - confidence))))

    text = json.dumps(final, ensure_ascii=False)
    data_sparse_terms = ("数据不足", "证据不足", "无法验证", "待验证", "建议观望")
    if any(term in text for term in data_sparse_terms):
        risk_level = max(risk_level, 4)

    entries = final.get("position_plan", {}).get("entries", [])
    has_actionable_entries = bool(entries)
    if available_cash and available_cash < 5000 and has_actionable_entries:
        risk_level = max(risk_level, 4)

    for entry in entries:
        try:
            weight = float(str(entry.get("weight_pct", 0)).replace("%", "").strip() or 0)
        except (TypeError, ValueError):
            weight = 0
        if weight > 20:
            risk_level = max(risk_level, 4)
        if weight > 50:
            risk_level = 5

    return min(5, max(1, risk_level))


def _derive_portfolio_risk(analysis_report: dict) -> int:
    """Raise risk for concentrated positions in small accounts."""
    try:
        total_assets = float(analysis_report.get("total_assets", 0) or 0)
    except (TypeError, ValueError):
        total_assets = 0
    if total_assets <= 0:
        return 1
    risk = 1
    for item in analysis_report.get("holdings", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            value = float(item.get("current_value") or item.get("market_value") or 0)
        except (TypeError, ValueError):
            value = 0
        ratio = value / total_assets if total_assets else 0
        if total_assets < 5000 and ratio > 0.10:
            risk = max(risk, 4)
        if ratio > 0.50:
            risk = max(risk, 5)
    return risk


def _extract_holding_codes(analysis_report: dict) -> set[str]:
    holdings = analysis_report.get("holdings", [])
    codes = set()
    if isinstance(holdings, list):
        for item in holdings:
            if isinstance(item, dict):
                code = item.get("code") or item.get("stock_code")
                if code:
                    codes.add(str(code))
    return codes


def _extract_total_assets(analysis_report: dict, available_cash: float = 0) -> float:
    if analysis_report.get("total_assets"):
        try:
            return float(analysis_report["total_assets"])
        except (TypeError, ValueError):
            pass
    total = float(available_cash or 0)
    for item in analysis_report.get("holdings", []) or []:
        if not isinstance(item, dict):
            continue
        total += float(item.get("current_value") or item.get("market_value") or 0)
    return total


def _extract_first_price(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", str(text).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _apply_account_constraints(
    decision: dict,
    available_cash: float = 0,
    holdings_codes: set[str] | None = None,
    total_assets: float = 0,
) -> dict:
    """Filter actionable stock pool by current cash and A-share lot size."""
    holdings_codes = holdings_codes or set()
    available_cash = round(float(available_cash or 0), 2)
    total_assets = round(float(total_assets or available_cash or 0), 2)
    reserve_cash = round(total_assets * 0.30, 2) if total_assets else 0
    executable_cash = max(0.0, min(available_cash, available_cash - reserve_cash))
    small_account_single_limit = round(total_assets * 0.10, 2) if total_assets and total_assets < 5000 else None
    max_new_ticket = executable_cash
    if small_account_single_limit is not None:
        max_new_ticket = min(max_new_ticket, small_account_single_limit)

    watchlist = []
    stock_pool = []
    for bucket in ("short_term", "mid_low_freq"):
        section = decision.get(bucket, {})
        if not isinstance(section, dict):
            continue
        kept = []
        for rec in section.get("recommendations", []) or []:
            if not isinstance(rec, dict):
                continue
            code = str(rec.get("code", ""))
            price = _extract_first_price(rec.get("buy_range", "") or rec.get("price", ""))
            min_lot_amount = round(price * 100, 2) if price else None
            is_existing = code in holdings_codes
            if (
                code
                and not is_existing
                and min_lot_amount is not None
                and (min_lot_amount > max_new_ticket or min_lot_amount > available_cash)
            ):
                moved = dict(rec)
                moved["reason_unaffordable"] = (
                    f"一手约需¥{min_lot_amount:,.2f}，当前可用现金¥{available_cash:,.2f}，"
                    f"按保留现金后可执行预算约¥{max_new_ticket:,.2f}"
                )
                watchlist.append(moved)
                continue
            kept.append(rec)
            stock_pool.append(rec)
        section["recommendations"] = kept

    decision["stock_pool"] = stock_pool
    decision["unaffordable_watchlist"] = watchlist
    decision["account_constraints"] = {
        "available_cash": available_cash,
        "total_assets": total_assets,
        "reserve_cash": reserve_cash,
        "executable_cash": round(max_new_ticket, 2),
        "lot_size": 100,
    }
    if watchlist:
        suffix = f" 已将 {len(watchlist)} 个一手买不起的新标的移入观察名单。"
        decision["position_advice"] = (decision.get("position_advice") or "").rstrip() + suffix
    return decision


def _extract_stop_loss(final: dict) -> float:
    pos_plan = final.get("position_plan", {})
    stops = []
    if pos_plan and pos_plan.get("entries"):
        for entry in pos_plan["entries"]:
            raw_pct = entry.get("stop_loss", {}).get("pct")
            if raw_pct is None:
                continue
            try:
                pct = float(str(raw_pct).replace("%", "").strip())
            except (TypeError, ValueError):
                continue
            stops.append(pct if pct <= 0 else -pct)
    if not stops:
        return -5
    stop = min(stops)
    return int(stop) if stop.is_integer() else stop
