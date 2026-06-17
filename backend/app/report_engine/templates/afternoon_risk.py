"""午后风控模板"""
from datetime import datetime
from app.report_engine.report_schema import ReportData, PositionItem, RiskAlert, PerformanceData


def build_afternoon_risk_data(date: str, positions: list[dict], alerts: list[dict],
                              performance: dict) -> ReportData:
    """从持仓、预警和账户数据构建午后风控ReportData"""
    pos_items = []
    for p in positions:
        cost = p.get("cost_price", p.get("avg_cost", 0)) or 0
        if cost > 100:  # 可能是分单位，转换为元
            cost = cost / 100
        curr = p.get("current_price", p.get("market_price", 0)) or 0
        if curr > 100:  # 可能是分单位
            curr = curr / 100
        qty = p.get("quantity", 0)
        pnl_pct = ((curr - cost) / max(cost, 1)) * 100
        risk_lvl = "danger" if pnl_pct < -8 else "warning" if pnl_pct < -3 else "normal"
        pos_items.append(PositionItem(
            code=p.get("code", p.get("stock_code", "")),
            name=p.get("name", p.get("stock_name", "")),
            quantity=qty,
            cost_price=round(cost, 2),
            current_price=round(curr, 2),
            profit_pct=round(pnl_pct, 2),
            market_value=round(curr * qty, 2),
            risk_level=risk_lvl,
        ))

    alert_items = []
    for a in alerts:
        alert_items.append(RiskAlert(
            stock_code=a.get("stock_code", a.get("code", "")),
            stock_name=a.get("stock_name", a.get("name", "")),
            alert_type=a.get("alert_type", "composite"),
            level=a.get("level", a.get("alert_level", "low")),
            message=a.get("message", a.get("alert_message", "")),
            suggestion=a.get("suggestion", ""),
            timestamp=a.get("timestamp", datetime.now().strftime("%H:%M")),
        ))

    perf = PerformanceData(
        total_assets=performance.get("total_assets", 0),
        available_cash=performance.get("available_cash", 0),
    )

    return ReportData(
        report_type="risk",
        generated_at=datetime.now(),
        date=date,
        positions=pos_items,
        alerts=alert_items,
        performance=perf,
    )
