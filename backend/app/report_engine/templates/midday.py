"""午盘快报模板"""
from datetime import datetime
from app.report_engine.report_schema import ReportData, PositionItem


def build_midday_report_data(date: str, market_summary: str, positions: list[dict],
                             afternoon_tip: str = "") -> ReportData:
    """从盘中和持仓数据构建午盘ReportData"""
    pos_items = []
    for p in positions:
        cost = p.get("cost", p.get("cost_price", 0)) or 0
        curr = p.get("current_price", p.get("market_price", 0)) or 0
        qty = p.get("position", p.get("quantity", 0))
        pnl_pct = ((curr - cost) / max(cost, 1)) * 100
        pos_items.append(PositionItem(
            code=p.get("code", ""),
            name=p.get("name", ""),
            quantity=qty,
            cost_price=round(cost, 2),
            current_price=round(curr, 2),
            profit_pct=round(pnl_pct, 2),
            market_value=round(curr * qty, 2),
        ))
    return ReportData(
        report_type="midday",
        generated_at=datetime.now(),
        date=date,
        market_summary=market_summary,
        positions=pos_items,
        knowledge_tip=afternoon_tip,
    )
