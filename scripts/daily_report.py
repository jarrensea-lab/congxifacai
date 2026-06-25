#!/usr/bin/env python3
"""📋 每日综合报告 — 聚合持仓策略 + 市场数据 + AI分析，生成一份完整MD报告"""
import sys
import os
import json
import asyncio

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'backend'))
os.chdir(PROJECT_ROOT)

ARCHIVE_DIR = "/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告"


async def main():
    import httpx
    from datetime import datetime
    from app.config import settings
    from app.data_sources.tencent_client import TencentDataSource
    from app.engine.analysis import run_analysis
    from app.engine.workshop import run_debate

    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')

    print(f"📋 每日综合报告 — {today}", flush=True)

    print("=" * 60, flush=True)

    # ===== 1. 读取持仓 =====
    portfolio_path = os.path.join(PROJECT_ROOT, 'data', 'user_portfolio.json')
    if not os.path.exists(portfolio_path):
        print("❌ 未找到持仓数据", flush=True)
        return

    with open(portfolio_path, 'r', encoding='utf-8') as f:
        portfolio = json.load(f)

    positions = portfolio.get("positions", [])
    closed = portfolio.get("closed_positions", [])

    # ===== 2. 获取行情 =====
    print("📊 获取实时行情...", flush=True)
    tc = TencentDataSource()
    market_data = {"indices": {}, "sectors": [], "holdings": [], "holdings_str": "空仓", "news": []}

    try:
        indices = await tc.fetch_batch(["sh000001", "sz399001", "sz399006"])
        sh = indices.get("sh000001", {})
        sz = indices.get("sz399001", {})
        cy = indices.get("sz399006", {})
        market_data["indices"] = {
            "shanghai": sh.get("price", 0),
            "shenzhen": sz.get("price", 0),
            "cyb": cy.get("price", 0),
            "sh_change": sh.get("change_pct", 0),
            "sz_change": sz.get("change_pct", 0),
            "cy_change": cy.get("change_pct", 0),
        }
        print(f"   上证: {sh.get('price','?')} ({sh.get('change_pct',0):+.2f}%) | "
              f"深证: {sz.get('price','?')} ({sz.get('change_pct',0):+.2f}%)", flush=True)
    except Exception as e:
        print(f"   ⚠️ 指数获取失败: {e}", flush=True)

    if positions:
        codes = [p["code"] for p in positions]
        formatted = [f"sh{c}" if c.startswith("6") else f"sz{c}" for c in codes]
        try:
            quotes = await tc.fetch_batch(formatted)
            for p in positions:
                q = quotes.get(formatted[codes.index(p["code"])], {})
                p["current_price"] = q.get("price", p.get("current_price", 0))
                p["change_pct"] = q.get("change_pct", 0)
                p["current_value"] = p["shares"] * p["current_price"]
                p["pnl"] = p["current_value"] - p["total_cost"]
                p["pnl_pct"] = (p["pnl"] / p["total_cost"]) * 100 if p["total_cost"] else 0
        except Exception as e:
            print(f"   ⚠️ 行情获取失败: {e}", flush=True)

        market_data["holdings"] = positions
        market_data["holdings_str"] = ", ".join(f"{p['name']}({p['code']})" for p in positions)

    portfolio["total_value"] = sum(p["current_value"] for p in positions)
    portfolio["total_pnl"] = sum(p["pnl"] for p in positions)
    portfolio["total_pnl_all"] = portfolio.get("total_pnl", 0) + portfolio.get("realized_pnl", 0)

    # ===== 3. 分析 + 辩论 =====
    print("📊 构建市场数据摘要...", flush=True)
    report = await run_analysis(market_data)

    print("🧠 AI 辩论中...", flush=True)
    try:
        debate_result = await run_debate(report)
        decision = debate_result.get("decision", {})
        risk_level = debate_result.get("recommended_risk_level", 3)
        final_view = decision.get("final_view", decision.get("final_decision", "待分析"))
        confidence = decision.get("confidence", "N/A")
        print(f"   辩论完成 — 裁判结论: {final_view} | R{risk_level}", flush=True)
    except Exception as e:
        print(f"   ⚠️ 辩论异常: {e}", flush=True)
        decision = {}
        risk_level = 3
        final_view = "分析失败"
        confidence = "N/A"

    # ===== 4. 构建综合Markdown报告 =====
    lines = []

    # 标题 + 元信息
    lines.append(f"# 📊 恭喜发财 — 每日综合策略报告")
    lines.append(f"")
    lines.append(f"> 📅 **{today}** | 🕐 {time_str}")
    lines.append(f"> 🤖 DeepSeek + Qwen 多角色辩论 | 📈 风险等级: **R{risk_level}**")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # ── 一、市场概况 ──
    idx = market_data.get("indices", {})
    lines.append(f"## 📈 一、市场概况")
    lines.append(f"")
    lines.append(f"| 指数 | 最新价 | 涨跌幅 |")
    lines.append(f"|------|:------:|:------:|")
    for label, key, col in [("上证指数", "shanghai", "sh_change"), ("深证成指", "shenzhen", "sz_change"), ("创业板指", "cyb", "cy_change")]:
        p = idx.get(key, "N/A")
        c = idx.get(col, 0)
        icon = "🟢" if c >= 0 else "🔴"
        lines.append(f"| {icon} {label} | {p} | {c:+.2f}% |")
    lines.append(f"")

    # ── 二、持仓概览 ──
    lines.append(f"## 💼 二、持仓概览")
    lines.append(f"")
    total_cost = portfolio.get("total_cost", 0)
    total_value = portfolio.get("total_value", 0)
    total_pnl = portfolio.get("total_pnl", 0)
    realized_pnl = portfolio.get("realized_pnl", 0)
    total_all = portfolio.get("total_pnl_all", 0)
    pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0

    lines.append(f"| 项目 | 金额 |")
    lines.append(f"|------|:----:|")
    lines.append(f"| 总投入成本 | ¥{total_cost:,.2f} |")
    lines.append(f"| 当前市值 | ¥{total_value:,.2f} |")
    lines.append(f"| 浮动盈亏 | ¥{total_pnl:+,.2f} ({pnl_pct:+.2f}%) |")
    lines.append(f"| 已实现盈亏 | ¥{realized_pnl:+,.2f} |")
    lines.append(f"| 总盈亏 | ¥{total_all:+,.2f} |")
    lines.append(f"")

    if positions:
        lines.append(f"### 持有中")
        lines.append(f"")
        lines.append(f"| 股票 | 持仓 | 成本 | 现价 | 盈亏 | 今日涨跌 | PE | PB |")
        lines.append(f"|------|:----:|:----:|:----:|:----:|:--------:|:---:|:---:|")
        for p in positions:
            icon = "🟢" if p["pnl"] >= 0 else "🔴"
            change = p.get("change_pct", 0)
            change_str = f"{change:+.2f}%" if change else "—"
            pe = p.get("pe_ttm", "—")
            pb = p.get("pb", "—")
            lines.append(f"| {icon} {p['name']}({p['code']}) | {p['shares']}股 | ¥{p['avg_cost']:.3f} | ¥{p['current_price']:.3f} | {p['pnl_pct']:+.2f}% | {change_str} | {pe} | {pb} |")
        lines.append(f"")

    if closed:
        lines.append(f"### 已清仓")
        lines.append(f"")
        lines.append(f"| 股票 | 清仓价 | 盈亏 |")
        lines.append(f"|------|:------:|:----:|")
        for c in closed:
            icon = "🟢" if c["realized_pnl"] >= 0 else "🔴"
            lines.append(f"| {icon} {c['name']}({c['code']}) | ¥{c['close_price']:.2f} | {c['realized_pnl_pct']:+.2f}% (¥{c['realized_pnl']:+,.2f}) |")
        lines.append(f"")

    # ── 三、AI 多维度分析 ──
    scores = {
        "技术面": report.get("technical_score", 50),
        "基本面": report.get("fundamental_score", 50),
        "资金面": report.get("capital_score", 50),
        "情绪面": report.get("sentiment_score", 50),
    }
    bias = report.get("overall_bias", "neutral")
    bias_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(bias, "🟡")

    lines.append(f"## 🧠 三、AI 多维度分析")
    lines.append(f"")
    lines.append(f"| 维度 | 评分 | 评估 |")
    lines.append(f"|------|:----:|:----:|")
    for dim, score in scores.items():
        icon = "🟢" if score >= 60 else ("🟡" if score >= 40 else "🔴")
        level = "良好" if score >= 60 else ("中性" if score >= 40 else "偏弱")
        lines.append(f"| {dim} | **{score}** | {icon} {level} |")
    lines.append(f"| **综合倾向** | | **{bias_icon} {bias}** |")
    lines.append(f"")

    market_note = report.get("market_context", "")
    if market_note:
        lines.append(f"> 📌 {market_note}")
        lines.append(f"")

    # ── 四、AI 辩论全文 ──
    lines.append(f"## 🎯 四、AI 辩论结论")
    lines.append(f"")
    lines.append(f"- **裁判判断**: {final_view}")
    lines.append(f"- **置信度**: {confidence}/10")
    lines.append(f"- **风险等级**: R{risk_level}")
    lines.append(f"")

    # 辩论推理全文
    reasoning = decision.get("reasoning", decision.get("debate_summary", ""))
    if reasoning:
        lines.append(f"### 裁判推理")
        lines.append(f"")
        lines.append(f"{reasoning}")
        lines.append(f"")

    # 各角色策略详情
    role_keys = {
        "🎯 猎手（短线技术）": "short_term",
        "📊 账房（基本面估值）": "mid_low_freq",
    }
    for label, key in role_keys.items():
        role_data = decision.get(key, {})
        if role_data:
            lines.append(f"### {label}")
            lines.append(f"")
            strategy = role_data.get("strategy", "")
            action = role_data.get("action", "")
            advice = role_data.get("holdings_advice", "")
            if strategy:
                lines.append(f"**策略**: {strategy}")
            if action:
                lines.append(f"**操作**: {action}")
            if advice:
                lines.append(f"**持仓建议**: {advice}")
            lines.append(f"")

    # ── 五、个股操作建议（含辩论完整信息）──
    pool = decision.get("stock_pool", [])
    if positions or pool:
        lines.append(f"## 🎯 五、个股操作建议")
        lines.append(f"")

        # 当前持仓操作建议
        for p in positions:
            code = p["code"]
            name = p["name"]
            lines.append(f"### {name}({code})")
            lines.append(f"")
            lines.append(f"- 现价: ¥{p.get('current_price', 'N/A'):.3f} | 成本: ¥{p.get('avg_cost', 0):.3f} | 盈亏: {p.get('pnl_pct', 0):+.2f}%")
            if p.get("pe_ttm"):
                lines.append(f"- PE: {p['pe_ttm']} | PB: {p.get('pb', '—')} | 换手: {p.get('turnover_pct', '—')}%")

            for sp in pool:
                if isinstance(sp, dict) and code in (sp.get("code", ""), sp.get("stock_code", "")):
                    sig = sp.get("signal", sp.get("action", "hold"))
                    conf = sp.get("confidence", sp.get("score", "N/A"))
                    sig_icon = "🟢 买入" if sig in ("buy", "add") else ("🔴 卖出" if sig in ("sell", "reduce") else "🟡 持有")
                    lines.append(f"- **辩论建议**: {sig_icon} (置信度: {conf})")
                    reason = sp.get("reason", "")
                    if reason: lines.append(f"- **理由**: {reason}")
                    buy_range = sp.get("buy_range", "")
                    stop_loss = sp.get("stop_loss", "")
                    target = sp.get("target", "")
                    if buy_range: lines.append(f"- **买入区间**: {buy_range}")
                    if stop_loss: lines.append(f"- **止损位**: {stop_loss}")
                    if target: lines.append(f"- **目标位**: {target}")
                    guide = sp.get("beginner_guide", "")
                    if guide: lines.append(f"- **新手指南**: {guide}")
                    break
            lines.append(f"")

        # AI 推荐的其他标的
        additional = [sp for sp in pool if isinstance(sp, dict) and sp.get("code", "") not in [p["code"] for p in positions]]
        if additional:
            lines.append(f"### 📌 AI 关注标的")
            lines.append(f"")
            for sp in additional:
                code = sp.get("code", "")
                name = sp.get("name", "")
                sig = sp.get("signal", sp.get("action", "hold"))
                conf = sp.get("confidence", sp.get("score", "N/A"))
                sig_icon = "🟢" if sig in ("buy", "add") else ("🔴" if sig in ("sell", "reduce") else "🟡")
                reason = sp.get("reason", "")
                lines.append(f"- {sig_icon} **{name}({code})** — {reason[:200]}")
                buy_range = sp.get("buy_range", "")
                stop_loss = sp.get("stop_loss", "")
                target = sp.get("target", "")
                guide = sp.get("beginner_guide", "")
                if buy_range: lines.append(f"  - 买入区间: {buy_range}")
                if stop_loss: lines.append(f"  - 止损位: {stop_loss}")
                if target: lines.append(f"  - 目标位: {target}")
                if guide: lines.append(f"  - 新手指南: {guide}")
                lines.append(f"")

    # ── 六、仓位建议 ──
    pos_advice = decision.get("position_advice", "")
    if pos_advice:
        lines.append(f"## 💡 六、仓位建议")
        lines.append(f"")
        lines.append(f"{pos_advice}")
        lines.append(f"")

    # ── 七、风险提示 ──
    risk_summary = decision.get("risk_summary", "")
    key_risks = report.get("key_risks", [])
    if risk_summary or key_risks:
        lines.append(f"## ⚠️ 七、风险提示")
        lines.append(f"")
        if risk_summary:
            lines.append(f"{risk_summary}")
            lines.append(f"")
        if key_risks:
            for r in key_risks:
                lines.append(f"- {r}")
            lines.append(f"")

    # ── 八、市场焦点 ──
    top_sectors = decision.get("top_sectors", [])
    if top_sectors:
        lines.append(f"## 🔍 八、市场焦点与关注板块")
        lines.append(f"")
        for s in top_sectors:
            if isinstance(s, dict):
                lines.append(f"- **{s.get('name', s.get('sector', '?'))}**: {s.get('reason', '')[:200]}")
            else:
                lines.append(f"- {s}")
        lines.append(f"")

    # ── 九、知识角 ──
    knowledge = decision.get("knowledge_corner", "")
    if knowledge:
        lines.append(f"## 📚 九、知识角")
        lines.append(f"")
        lines.append(f"{knowledge}")
        lines.append(f"")

    lines.append(f"---")
    lines.append(f"*报告生成时间: {today} {time_str}*")
    lines.append(f"*🤖 恭喜发财 — AI 智能分析 · 仅供参考，不构成投资建议*")

    md_content = '\n'.join(lines)

    # ===== 写入文件 =====
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    filename = f"{today}_每日综合策略报告.md"
    filepath = os.path.join(ARCHIVE_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"✅ 报告已保存: {filepath}", flush=True)
    print(f"   📄 共 {len(lines)} 行 / {os.path.getsize(filepath)} 字节", flush=True)

    # ===== 更新持仓 =====
    portfolio["updated_at"] = now.strftime('%Y-%m-%d %H:%M:%S')
    with open(portfolio_path, 'w', encoding='utf-8') as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)
    print(f"✅ 持仓数据已更新", flush=True)

    # ===== 推送飞书 =====
    print("📤 推送飞书...", flush=True)
    try:
        from app.services.feishu_pusher import send_webhook_card
        webhook_url = os.environ.get('FEISHU_WEBHOOK_URL')
        if webhook_url and 'YOUR_WEBHOOK' not in webhook_url:
            summary = md_content[:2500] + "

...*(完整报告已保存至本地)*"
            ok = await send_webhook_card(
                webhook_url,
                f"📊 恭喜发财 — {today} 每日综合策略报告",
                summary
            )
            if ok:
                print(f"   ✅ Webhook 卡片推送成功", flush=True)
            else:
                print(f"   ⚠️ Webhook 推送失败", flush=True)
        else:
            print(f"   ⚠️ 未配置 FEISHU_WEBHOOK_URL，跳过飞书推送", flush=True)
    except Exception as e:
        print(f"   ⚠️ 飞书推送异常: {e}", flush=True)

    print("=" * 60, flush=True)
    print(f"📋 每日综合报告完成", flush=True)
    print("=" * 60, flush=True)

    return filepath


if __name__ == "__main__":
    result = asyncio.run(main())
    if result:
        print(f"\n🔗 报告路径: {result}")
