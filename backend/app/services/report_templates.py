"""V7 报告模板 — 飞书消息卡片 + Markdown 报告格式"""
from datetime import datetime


def daily_report_md(date: str, api_health: bool, deepseek_health: bool,
                    tasks_run: int, tasks_fail: int, errors: list,
                    daily_pnl: float = 0, positions_count: int = 0) -> str:
    """每日运行日报 — Markdown 格式，推送到飞书消息卡片"""
    status_icon = "✅" if api_health else "❌"
    ds_icon = "✅" if deepseek_health else "⚠️"

    lines = [
        "📊 **恭喜发财 V7 运行日报**",
        f"📅 {date}",
        "",
        "**运行状态**",
        f"- API 服务: {status_icon}",
        f"- DeepSeek: {ds_icon}",
        f"- 定时任务: {tasks_run}/{tasks_run + tasks_fail} 完成",
    ]

    if daily_pnl != 0:
        pnl_icon = "📈" if daily_pnl >= 0 else "📉"
        lines.append(f"- 今日收益: {pnl_icon} ¥{daily_pnl:+,.2f}")
        lines.append(f"- 持仓数: {positions_count}")

    if errors:
        lines.extend(["", "**⚠️ 异常事件**"])
        for e in errors[:5]:
            lines.append(f"- {e[:200]}")

    lines.extend([
        "",
        "---",
        f"🕐 报告生成时间: {datetime.now().strftime('%H:%M:%S')}",
    ])
    return "\n".join(lines)


def strategy_report_md(decision: dict, backtests: dict = None) -> str:
    """策略报告 — 风险优先结构（霍华德·马克斯：风险应置于首位）"""
    st = decision.get("short_term", {})
    ml = decision.get("mid_low_freq", {})
    reasoning = decision.get("reasoning", "")

    # 提取反方论证段落（裁判输出的"反方观点"部分）
    counter_lines = []
    for line in reasoning.split("反方"):
        for sub in ["观点", "不成立"]:
            if sub in line:
                counter_lines.append(f"  反方自检: {line.strip()[:200]}")

    lines = [
        "# 🎯 恭喜发财 V7 每日策略报告",
        f"📅 {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## ⚠️ 风险预警（首要关注）",
    ]

    # 收集所有风险点
    all_risks = []
    for r in st.get("key_risks", []):
        all_risks.append(f"- ⚠️ {r}")
    for r in ml.get("key_risks", []):
        all_risks.append(f"- ⚠️ {r}")
    for rec in st.get("recommendations", []):
        sl = rec.get("stop_loss", "")
        if sl:
            all_risks.append(f"- {rec.get('name','?')}({rec.get('code','?')}): 止损{sl}")
    for rec in ml.get("recommendations", []):
        sl = rec.get("stop_loss", "")
        if sl:
            all_risks.append(f"- {rec.get('name','?')}({rec.get('code','?')}): 止损{sl}")
    lines.append("\n".join(all_risks[:6]) if all_risks else "- 未识别到显著风险")
    lines.append("")

    # 市场背景（风险之后）
    lines.extend([
        "## 市场背景",
        f"- **方向**: {decision.get('final_decision', 'N/A')}",
        f"- **置信度**: {decision.get('confidence', 0)}/10",
    ])
    if counter_lines:
        lines.extend(counter_lines[:1])
    lines.append(f"- **核心理由**: {reasoning[:300]}")
    lines.append("")

    # 仓位管理
    pos_plan = decision.get("position_plan", {})
    if pos_plan.get("entries"):
        lines.append("## 仓位管理")
        lines.append(f"- 建议保留现金: {pos_plan.get('suggested_cash_pct', 20)}%")
        lines.append(f"- 看好板块: {', '.join(decision.get('top_sectors', [])) or 'N/A'}")
        lines.append("")
        for entry in pos_plan["entries"]:
            lines.append(f"### {entry.get('name', '')}({entry.get('code', '')}) — 仓位 {entry.get('weight_pct', 0)}%")
            sl = entry.get("stop_loss", {})
            lines.append(f"- 🛑 止损: ¥{sl.get('price', '?')} ({sl.get('pct', '?')}%) — {sl.get('reason', '')}")
            for tp in entry.get("take_profit", []):
                lines.append(f"- 🎯 止盈: ¥{tp.get('target', '?')} ({tp.get('profit_pct', '?')}%) — {tp.get('reason', '')}")
            lines.append("")

    # 短线机会（在风险之后）
    if st.get("recommendations"):
        lines.append("## ⚡ 短线机会 (1-5天)")
        for r in st["recommendations"]:
            lines.append(f"- **{r.get('name', '')}**({r.get('code', '')}): {r.get('reason', '')}")
            lines.append(f"  买入: {r.get('buy_range', '')} | 止损: {r.get('stop_loss', '')} | 目标: {r.get('target', '')}")
            lines.append(f"  👶 {r.get('beginner_guide', '')}")

    # 中线机会
    if ml.get("recommendations"):
        lines.append("")
        lines.append("## 📈 中线机会 (1-4周)")
        for r in ml["recommendations"]:
            lines.append(f"- **{r.get('name', '')}**({r.get('code', '')}): {r.get('reason', '')}")
            lines.append(f"  买入: {r.get('buy_range', '')} | 止损: {r.get('stop_loss', '')} | 目标: {r.get('target', '')}")
            lines.append(f"  👶 {r.get('beginner_guide', '')}")

    # 回测摘要（放在末尾做参考）
    if backtests:
        lines.append("")
        lines.append("## 📊 策略回测参考")
        for code, bt in backtests.items():
            icon = "✅" if bt.get("win_rate_pct", 0) >= 40 else "⚠️" if bt.get("win_rate_pct", 0) >= 30 else "❌"
            lines.append(f"{icon} {code}: 胜率{bt.get('win_rate_pct', 0)}% | "
                         f"累计{bt.get('cumulative_return_pct', 0):+.1f}% | "
                         f"最大回撤{bt.get('max_drawdown_pct', 0)}%")

    # 知识角
    kc = decision.get("knowledge_corner", "")
    if kc:
        lines.extend(["", "## 📚 知识角", kc])

    lines.extend([
        "",
        "---",
        f"*报告由 恭喜发财 V7 自动生成 | {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
    ])
    return "\n".join(lines)


def backtest_card_md(backtests: dict) -> str:
    """回测结果飞书消息卡片 — 简洁版"""
    lines = ["📊 **策略回测结果 (MA金叉死叉, 近半年)**", ""]
    for code, bt in backtests.items():
        wr = bt.get("win_rate_pct", 0)
        cr = bt.get("cumulative_return_pct", 0)
        emoji = "🟢" if cr > 0 else "🔴"
        lines.append(
            f"{emoji} **{code}**: "
            f"交易{bt.get('total_trades', 0)}次 | "
            f"胜率{wr}% | "
            f"累计{cr:+.1f}% | "
            f"最大回撤{bt.get('max_drawdown_pct', 0)}%"
        )
    lines.append("")
    lines.append(f"*回测引擎: V7 backtest_engine | {datetime.now().strftime('%H:%M')}*")
    return "\n".join(lines)
