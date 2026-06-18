"""盘前策略报告模板 — 从AI辩论结果构建标准化ReportData"""
from datetime import datetime
from app.report_engine.report_schema import ReportData, Recommendation, PositionItem


def build_premarket_report_data(
    date: str,
    decision: dict,
    positions: list[dict],
    risk_level: int = 3,
) -> ReportData:
    """从AI辩论的decision字典构建ReportData"""
    recs = []
    for item in decision.get("short_term", {}).get("recommendations", []):
        recs.append(Recommendation(
            code=item.get("code", ""),
            name=item.get("name", ""),
            strategy_type="short_term",
            buy_range=item.get("buy_range", ""),
            stop_loss=item.get("stop_loss", ""),
            target=item.get("target", ""),
            reason=item.get("reason", ""),
            technical_signals=item.get("technical_signals", item.get("reason", "")[:60]),
            concept_tags=item.get("concept_tags", decision.get("top_sectors", [])),
            trend_score=item.get("trend_score", 5),
            beginner_guide=item.get("beginner_guide", ""),
            recommend_date=date,
        ))
    for item in decision.get("mid_low_freq", {}).get("recommendations", []):
        recs.append(Recommendation(
            code=item.get("code", ""),
            name=item.get("name", ""),
            strategy_type="mid_low_freq",
            buy_range=item.get("buy_range", ""),
            stop_loss=item.get("stop_loss", ""),
            target=item.get("target", ""),
            reason=item.get("reason", ""),
            technical_signals=item.get("technical_signals", ""),
            concept_tags=item.get("concept_tags", []),
            trend_score=item.get("trend_score", 5),
            beginner_guide=item.get("beginner_guide", ""),
            recommend_date=date,
        ))

    pos_items = []
    for p in positions:
        cost = p.get("cost", 0) or 0
        curr = p.get("current_price", 0) or 0
        pnl_pct = ((curr - cost) / cost) * 100 if cost > 0 else 0
        risk_lvl = "danger" if pnl_pct < -8 else "warning" if pnl_pct < -3 else "normal"
        qty = p.get("position", p.get("quantity", 0))
        pos_items.append(PositionItem(
            code=p.get("code", ""),
            name=p.get("name", ""),
            quantity=qty,
            cost_price=round(cost, 2),
            current_price=round(curr, 2),
            profit_pct=round(pnl_pct, 2),
            market_value=round(curr * qty, 2),
            risk_level=risk_lvl,
        ))

    return ReportData(
        report_type="premarket",
        generated_at=datetime.now(),
        date=date,
        risk_level=risk_level,
        market_direction=decision.get("final_decision", decision.get("final_view", "N/A")),
        market_summary=decision.get("reasoning", "")[:300],
        confidence=decision.get("confidence", 5),
        positions=pos_items,
        recommendations=recs,
        knowledge_tip=decision.get("knowledge_corner", ""),
        top_sectors=decision.get("top_sectors", []),
    )
