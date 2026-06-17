"""飞书消息卡片 Markdown 构建器 — 标准化卡片格式"""
from app.report_engine.report_schema import ReportData


def build_premarket_card(data: ReportData) -> str:
    """盘前策略消息卡片"""
    lines = [
        f"🐕 **旺财V7 盘前策略 [R{data.risk_level}]**",
        f"📅 {data.date}",
        "",
        "**⚠️ 风险预警**",
    ]
    danger_positions = [p for p in data.positions if p.risk_level == "danger"]
    warning_positions = [p for p in data.positions if p.risk_level == "warning"]
    if danger_positions:
        for p in danger_positions[:3]:
            lines.append(f"- 🔴 {p.name}({p.code}): 盈亏{p.profit_pct:+.2f}% — 注意风险")
    if warning_positions:
        for p in warning_positions[:3]:
            lines.append(f"- 🟡 {p.name}({p.code}): 盈亏{p.profit_pct:+.2f}%")
    if not danger_positions and not warning_positions:
        lines.append("- 未识别到显著风险")
    lines.extend([
        "",
        "**📊 市场背景**",
        f"- 方向: {data.market_direction}",
        f"- 置信度: {data.confidence}/10",
        f"- 看好板块: {', '.join(data.top_sectors[:3]) or 'N/A'}",
        "",
        f"**📈 短线机会 ({len([r for r in data.recommendations if r.strategy_type=='short_term'])}支)**",
    ])
    for r in data.recommendations:
        if r.strategy_type == "short_term":
            lines.append(f"- **{r.name}**({r.code}): {r.reason[:80]}")
            lines.append(f"  🛑 {r.buy_range} | 🎯 {r.target}")
    lines.extend([
        "",
        f"**📈 中线机会 ({len([r for r in data.recommendations if r.strategy_type=='mid_low_freq'])}支)**",
    ])
    for r in data.recommendations:
        if r.strategy_type == "mid_low_freq":
            lines.append(f"- **{r.name}**({r.code}): {r.reason[:80]}")
            lines.append(f"  🛑 {r.buy_range} | 🎯 {r.target}")
    lines.extend([
        "",
        "**📚 知识角**",
        data.knowledge_tip or "—",
        "",
        "---",
        f"*生成: {data.generated_at.strftime('%H:%M') if data.generated_at else '--'}*",
    ])
    return "\n".join(lines)


def build_closing_card(data: ReportData) -> str:
    """收盘全景消息卡片"""
    pnl = data.performance
    health = data.system_health
    lines = [
        "📊 **旺财V7 收盘全景报告**",
        f"📅 {data.date}",
        "",
        "**📈 今日交易回顾**",
    ]
    if pnl:
        icon = "📈" if pnl.daily_pnl >= 0 else "📉"
        lines.append(f"- 日盈亏: {icon} ¥{pnl.daily_pnl:+,.2f} ({pnl.daily_pnl_pct:+.2f}%)")
        lines.append(f"- 累计盈亏: ¥{pnl.cumulative_pnl:+,.2f}")
        lines.append(f"- 持仓数: {pnl.position_count} | 总资产: ¥{pnl.total_assets:,.2f}")

    lines.extend(["", "**💼 持仓表现**"])
    for p in data.positions[:5]:
        icon = "📈" if p.profit_pct >= 0 else "📉"
        lines.append(f"- {icon} {p.name}({p.code}): {p.profit_pct:+.2f}%")
    if not data.positions:
        lines.append("- 无持仓")

    lines.extend(["", "**⚖️ 风控事件**"])
    alerts = [a for a in data.alerts if a.level in ("high", "mid")]
    if alerts:
        for a in alerts[:3]:
            icon = {"high": "🔴", "mid": "🟡"}.get(a.level, "⚪")
            lines.append(f"- {icon} {a.stock_name}({a.stock_code}): {a.message[:100]}")
    else:
        lines.append("- 今日无风控事件")

    lines.extend(["", "**🔮 明日预告**"])
    lines.append(data.market_summary[:200] if data.market_summary else "—")

    lines.extend(["", "**⚙️ 系统健康**"])
    if health:
        lines.append(f"- API: {'✅' if health.api_service else '❌'} | DeepSeek: {'✅' if health.deepseek_api else '❌'} | Qwen: {'✅' if health.qwen_api else '❌'}")
        lines.append(f"- 任务: {health.tasks_success}成功 / {health.tasks_fail}失败")

    lines.extend([
        "",
        "---",
        f"*生成: {data.generated_at.strftime('%H:%M') if data.generated_at else '--'}*",
    ])
    return "\n".join(lines)


def build_midday_card(data: ReportData) -> str:
    """午盘快报消息卡片"""
    lines = [
        "🌤️ **旺财V7 午盘快报**",
        f"📅 {data.date}",
        "",
        "**上午盘面**",
        data.market_summary[:300] or "—",
        "",
        "**💼 持仓表现**",
    ]
    for pos in data.positions[:5]:
        icon = "📈" if pos.profit_pct >= 0 else "📉"
        lines.append(f"- {icon} {pos.name}({pos.code}): {pos.profit_pct:+.2f}%")
    if not data.positions:
        lines.append("- 无持仓")
    lines.extend([
        "",
        "**🎯 下午策略**",
        data.knowledge_tip[:200] if data.knowledge_tip else "观望为主",
        "",
        "---",
        f"*生成: {data.generated_at.strftime('%H:%M') if data.generated_at else '--'}*",
    ])
    return "\n".join(lines)


def build_afternoon_risk_card(data: ReportData) -> str:
    """午后风控消息卡片（仅预警时推送）"""
    lines = [
        "🛡️ **旺财V7 午后风控告警**",
        f"📅 {data.date}",
        "",
    ]
    high_alerts = [a for a in data.alerts if a.level == "high"]
    mid_alerts = [a for a in data.alerts if a.level == "mid"]
    if high_alerts:
        for a in high_alerts[:3]:
            lines.append(f"- 🔴 **{a.stock_name}**({a.stock_code}): {a.message}")
            if a.suggestion:
                lines.append(f"  建议: {a.suggestion}")
    if mid_alerts:
        for a in mid_alerts[:2]:
            lines.append(f"- 🟡 **{a.stock_name}**({a.stock_code}): {a.message}")
    lines.extend(["", "**💳 账户概览**"])
    if data.performance:
        mv = data.performance.total_assets - data.performance.available_cash
        lines.append(f"- 总资产: ¥{data.performance.total_assets:,.2f}")
        lines.append(f"- 持仓市值: ¥{mv:,.2f}")
        lines.append(f"- 可用现金: ¥{data.performance.available_cash:,.2f}")
    lines.extend([
        "",
        "---",
        f"*生成: {data.generated_at.strftime('%H:%M') if data.generated_at else '--'}*",
    ])
    return "\n".join(lines)
