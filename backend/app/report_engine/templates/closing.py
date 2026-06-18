"""收盘全景报告模板 — 收盘复盘+系统日报合并"""
from datetime import datetime
from app.report_engine.report_schema import (
    ReportData, PositionItem, RiskAlert, PerformanceData, SystemHealth
)


def build_closing_report_data(
    date: str,
    positions: list[dict],
    alerts: list[dict],
    performance: dict,
    market_summary: str,
    system_health: dict,
    preview: str = "",
) -> ReportData:
    """从各数据源构建收盘全景ReportData"""
    pos_items = []
    for p in positions:
        cost = p.get("cost", p.get("cost_price", 0)) or 0
        curr = p.get("current_price", p.get("market_price", 0)) or 0
        pnl_pct = ((curr - cost) / max(cost, 1)) * 100
        qty = p.get("quantity", p.get("position", 0))
        risk_lvl = "danger" if pnl_pct < -8 else "warning" if pnl_pct < -3 else "normal"
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

    alert_items = []
    for a in alerts:
        alert_items.append(RiskAlert(
            stock_code=a.get("stock_code", ""),
            stock_name=a.get("stock_name", ""),
            alert_type=a.get("alert_type", "composite"),
            level=a.get("alert_level", a.get("level", "low")),
            message=a.get("alert_message", a.get("message", "")),
            suggestion=a.get("suggestion", ""),
            timestamp=a.get("timestamp", ""),
        ))

    perf = PerformanceData(
        daily_pnl=performance.get("daily_pnl", 0),
        daily_pnl_pct=performance.get("daily_pnl_pct", 0),
        cumulative_pnl=performance.get("cumulative_pnl", 0),
        win_rate=performance.get("win_rate", 0),
        position_count=performance.get("position_count", 0),
        total_assets=performance.get("total_assets", 0),
        available_cash=performance.get("available_cash", 0),
    )

    health = SystemHealth(
        api_service=system_health.get("api_service", False),
        deepseek_api=system_health.get("deepseek_api", False),
        qwen_api=system_health.get("qwen_api", False),
        tencent_data=system_health.get("tencent_data", False),
        eastmoney_data=system_health.get("eastmoney_data", False),
        tushare_data=system_health.get("tushare_data", False),
        tasks_success=system_health.get("tasks_success", 0),
        tasks_fail=system_health.get("tasks_fail", 0),
        last_error=system_health.get("last_error"),
    )

    return ReportData(
        report_type="closing",
        generated_at=datetime.now(),
        date=date,
        market_summary=preview or market_summary[:300],
        positions=pos_items,
        alerts=alert_items,
        performance=perf,
        system_health=health,
    )
