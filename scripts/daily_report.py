#!/usr/bin/env python3
"""📋 每日综合报告 — 聚合持仓策略 + 市场数据 + AI分析，生成一份完整MD报告"""
import sys
import os
import json
import asyncio
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'backend'))
os.chdir(PROJECT_ROOT)

DEFAULT_ARCHIVE_DIR = "/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告"
ARCHIVE_DIR = os.getenv("CONGXI_REPORT_ARCHIVE_DIR", DEFAULT_ARCHIVE_DIR)
DELIVERY_STATUS_FILENAME = "delivery_status.json"


from app.services.strategy_profile import get_strategy_profile



def build_feishu_summary(md_content: str, limit: int = 2500) -> str:
    """Build a short Feishu card body while pointing to the local full report."""
    if len(md_content) <= limit:
        return md_content
    return md_content[:limit].rstrip() + "\n\n...*(完整报告已保存至 Obsidian 报告目录)*"


def build_execution_guard(
    positions: list[dict],
    available_cash: float,
    total_assets: float,
    strategy_profile: dict | None = None,
) -> str:
    """Create deterministic execution constraints for small A-share accounts."""
    profile = strategy_profile or get_strategy_profile()
    lines = []
    reserve_pct = float(profile["cash_reserve_pct"])
    single_pct = (
        float(profile["single_position_limit_pct"])
        if total_assets < 5000
        else float(profile["standard_single_position_limit_pct"])
    )
    reserve_cash = round(total_assets * reserve_pct / 100, 2) if total_assets else 0
    single_limit = round(total_assets * single_pct / 100, 2) if total_assets else 0
    buy_budget = max(0.0, min(available_cash - reserve_cash, single_limit))
    max_affordable_price = round(buy_budget / 100, 2)

    lines.append(f"- 策略模式：{profile['title']}；目标：{profile['target']}。")
    lines.append(
        f"- 风险闸门：账户最大回撤 -{profile['max_drawdown_pct']}%，"
        f"单票上限 {single_pct:.0f}%，单笔硬止损 {profile['stop_loss_pct']}%。"
    )
    lines.append(f"- 账户可用现金 ¥{available_cash:,.2f}，{reserve_pct:.0f}%现金底线约 ¥{reserve_cash:,.2f}。")
    if profile["mode"] == "growth_sprint":
        lines.append("- 验收口径：不承诺收益，只验证系统按高收益试验规则输出和留痕。")
        lines.append("- 标的边界：允许低价高波动，但默认排除 ST、退市整理、流动性极差和无明确催化标的。")
    if buy_budget <= 0:
        lines.append("- 机器校验: 不新增买入；先恢复现金安全垫。")
    else:
        action_prefix = "若新增"
        if profile["mode"] == "capital_preservation":
            action_prefix = "原则上不新增买入；若新增"
        lines.append(
            f"- 机器校验: {action_prefix}，必须买得起一手100股，当前单笔预算约 ¥{buy_budget:,.2f}，"
            f"只考虑股价不高于 ¥{max_affordable_price:.2f} 的标的；其余只放观察名单。"
        )

    for p in positions:
        shares = int(p.get("shares", p.get("position", 0)) or 0)
        price = float(p.get("current_price", 0) or 0)
        value = float(p.get("current_value", shares * price) or 0)
        if not shares or not price or not total_assets:
            continue
        ratio = value / total_assets * 100
        target_shares = int((single_limit / price) // 1)
        if ratio > single_pct and total_assets < 5000:
            if shares <= 100:
                lines.append(
                    f"- {p.get('name', p.get('code', '持仓'))}: 当前{shares}股，占总资产约{ratio:.1f}%，"
                    f"超过小账户{single_pct:.0f}%上限；若要立刻合规，机器可执行方案是清仓{shares}股，"
                    "否则只能继续持有观察，不能执行非整手减仓后留下零碎仓的方案。"
                )
            else:
                sell_qty = max(0, shares - target_shares)
                sell_qty = ((sell_qty + 99) // 100) * 100
                sell_qty = min(sell_qty, shares)
                lines.append(
                    f"- {p.get('name', p.get('code', '持仓'))}: 当前{shares}股，占总资产约{ratio:.1f}%；"
                    f"若按{single_pct:.0f}%上限降仓，优先卖出约{sell_qty}股。"
                )

    return "\n".join(lines)


def build_final_action_summary(
    positions: list[dict],
    available_cash: float,
    total_assets: float,
    strategy_profile: dict | None = None,
) -> str:
    """Deterministic final action summary that overrides inconsistent AI quantities."""
    profile = strategy_profile or get_strategy_profile()
    if not positions:
        if profile["mode"] == "growth_sprint":
            return (
                "当前无持仓；高收益试验模式允许在 Sentinel 证据、市场企稳和一手金额同时通过后，"
                "进入人工复核买入。"
            )
        return "当前无持仓；原则上不新增买入，等待报告给出可买得起且通过风险过滤的一手标的。"
    actions = ["今日最终动作以机器校验为准，不直接执行 AI 原始文字中的零碎股数。"]
    single_pct = float(profile["single_position_limit_pct"]) if total_assets < 5000 else float(profile["standard_single_position_limit_pct"])
    for p in positions:
        shares = int(p.get("shares", 0) or 0)
        price = float(p.get("current_price", 0) or 0)
        value = float(p.get("current_value", shares * price) or 0)
        ratio = value / total_assets * 100 if total_assets else 0
        if total_assets < 5000 and ratio > single_pct and shares <= 100:
            actions.append(
                f"{p.get('name', p.get('code', '持仓'))}当前{shares}股，市值约¥{value:,.2f}，"
                f"占总资产约{ratio:.1f}%；若要马上合规，只能清仓{shares}股，"
                "否则继续持有观察但不加仓。"
            )
    actions.append("新标的只进观察名单，不新增买入，除非一手金额和风险过滤同时通过。")
    return " ".join(actions)


def load_sentinel_research_package(report_date: str) -> dict | None:
    """Load Sentinel research package for report_date if it exists."""
    try:
        from app.ai.sentinel_research import load_research_package

        return load_research_package(report_date)
    except Exception:
        return None


def _role_excerpt(role_data: dict) -> str:
    for key in ("analysis", "reasoning", "strategy", "position_advice"):
        value = role_data.get(key)
        if value:
            return str(value).replace("\n", " ")[:180]
    return "暂无结构化观点。"


def build_role_matrix(roles: dict, decision: dict) -> list[str]:
    """Render a comparable matrix for the four debaters."""
    role_specs = [
        ("猎手", "hunter", "短线技术", "short_term"),
        ("账房", "accountant", "基本面估值", "mid_low_freq"),
        ("守夜人", "guardian", "风险与纪律", ""),
        ("Serenity研究员", "researcher", "产业链瓶颈/研究证据", ""),
    ]
    lines = [
        "| 角色 | 分工 | 核心观点 | 执行可行性 |",
        "|---|---|---|---|",
    ]
    for label, role_key, duty, decision_key in role_specs:
        role_data = roles.get(role_key, {}) if isinstance(roles, dict) else {}
        if not isinstance(role_data, dict):
            role_data = {"analysis": str(role_data)}
        if not role_data and decision_key:
            role_data = decision.get(decision_key, {}) if isinstance(decision.get(decision_key), dict) else {}
        excerpt = _role_excerpt(role_data)
        feasible = "必须通过账户约束和一手金额校验"
        if role_key == "guardian":
            feasible = "优先生效，覆盖其他角色"
        lines.append(f"| {label} | {duty} | {excerpt} | {feasible} |")
    return lines


def build_sentinel_research_section(package: dict | None) -> list[str]:
    """Render Sentinel research package summary for the main report."""
    if not package:
        return [
            "- Sentinel 状态：降级，未找到当日研究包。",
            "- 处理方式：主报告继续生成，但不把缺失研究包伪装成结论。",
        ]
    lines = [
        f"- Sentinel 状态：{(package.get('source_status') or {}).get('status', 'unknown')}",
        f"- 高频新闻：{package.get('event_count', 0)} 条，关键新闻 {package.get('key_event_count', 0)} 条。",
    ]
    themes = package.get("top_themes") or []
    if themes:
        lines.append("- 主题热度：" + "；".join(f"{item.get('name')}({item.get('count')})" for item in themes[:5]))
    risks = package.get("risk_events") or []
    if risks:
        lines.append("- 风险线索：" + "；".join(str(item.get("excerpt", ""))[:80] for item in risks[:3]))
    dives = package.get("serenity_deep_dives") or []
    if dives:
        lines.append("- Serenity 深挖：")
        for dive in dives[:3]:
            candidates = dive.get("top_candidates") or []
            candidate_text = ""
            if candidates:
                candidate_text = "；候选：" + "、".join(
                    f"{item.get('name')}({item.get('code')})/{item.get('score')}"
                    for item in candidates[:3]
                )
            path_text = f"；学习报告：{dive.get('learning_report_path')}" if dive.get("learning_report_path") else ""
            lines.append(f"  - {dive.get('theme', '未知主题')}{candidate_text}{path_text}")
    lines.append("- 边界：以上只作为研究输入，不直接触发交易。")
    return lines


def build_data_source_audit(
    *,
    market_data: dict,
    sentinel_package: dict | None,
    portfolio_loaded: bool = True,
    sqlite_ok: bool = True,
    deepseek_ok: bool | None = None,
    qwen_ok: bool | None = None,
) -> list[str]:
    """Render data-source audit rows for the main report."""
    indices = market_data.get("indices", {}) if isinstance(market_data, dict) else {}
    sentinel_status = (sentinel_package or {}).get("source_status") or {}
    rows = [
        "| 数据源 | 状态 | 覆盖/说明 |",
        "|---|---|---|",
        f"| 行情数据 | {'ok' if indices else 'degraded'} | 指数 {len(indices)} 项 |",
        f"| Tushare 高频新闻 | {sentinel_status.get('status', 'missing')} | 新闻 {(sentinel_package or {}).get('event_count', 0)} 条 |",
        f"| Sentinel 研究包 | {'ok' if sentinel_package else 'missing'} | 研究输入，不产生交易指令 |",
        f"| DeepSeek | {('unknown' if deepseek_ok is None else ('ok' if deepseek_ok else 'degraded'))} | 四角色/裁判主模型 |",
        f"| Qwen | {('unknown' if qwen_ok is None else ('ok' if qwen_ok else 'degraded'))} | 研究员/备用裁判 |",
        f"| 本地持仓 | {'ok' if portfolio_loaded else 'missing'} | 账户约束优先生效 |",
        f"| SQLite | {'ok' if sqlite_ok else 'degraded'} | 辩论快照与持仓同步 |",
    ]
    return rows


def build_next_day_execution_playbook(
    *,
    positions: list[dict],
    available_cash: float,
    total_assets: float,
    decision: dict,
    risk_level: int,
    strategy_profile: dict | None = None,
) -> list[str]:
    """Render deterministic next-day execution playbook."""
    profile = strategy_profile or get_strategy_profile()
    guard = build_execution_guard(positions, available_cash, total_assets, profile)
    final_action = build_final_action_summary(positions, available_cash, total_assets, profile)
    lines = [
        f"- 策略模式：{profile['title']}",
        f"- 基准动作：{decision.get('final_decision', decision.get('final_view', '观望'))}",
        f"- 风险等级：R{risk_level}",
        f"- 账户动作：{final_action}",
    ]
    if guard:
        lines.append("- 账户校验：")
        lines.extend(f"  {line}" for line in guard.splitlines())
    lines.extend([
        "- 触发条件：只有市场企稳、标的一手金额可承受、且风控未否决时，才允许进入观察后的人工复核。",
        "- 禁止条件：大盘急跌未稳、单票一手金额超过预算、研究输入缺证据时，不新增买入。",
        f"- 退出条件：已有持仓触发止损、账户回撤接近 -{profile['max_drawdown_pct']}%、"
        "仓位超小账户上限、或裁判风险等级升至 R5 时优先降风险。",
    ])
    return lines


def build_next_day_strategy_sections(
    *,
    report_date: str,
    target_date: str,
    risk_level: int,
    final_view: str,
    confidence,
    positions: list[dict],
    available_cash: float,
    total_assets: float,
    market_data: dict,
    analysis_report: dict,
    decision: dict,
    roles: dict,
    sentinel_package: dict | None,
    strategy_profile: dict | None = None,
) -> list[str]:
    """Build the strategy-first opening sections of the main report."""
    profile = strategy_profile or get_strategy_profile()
    lines = [
        "## 一、明日总策略",
        "",
        f"- 服务交易日：{target_date}",
        f"- 策略模式：{profile['title']}",
        f"- 目标：{profile['target']}",
        "- 验收口径：不承诺收益，验证报告、风控、人工复核和后续复盘是否按规则执行。",
        f"- 裁判结论：{final_view}",
        f"- 置信度：{confidence}/10",
        f"- 风险等级：R{risk_level}",
        f"- 总体倾向：{analysis_report.get('overall_bias', 'neutral')}",
        "",
        "## 二、账户约束",
        "",
        f"- 可用现金：¥{available_cash:,.2f}",
        f"- 估算总资产：¥{total_assets:,.2f}",
        f"- 当前持仓数：{len(positions)}",
        "",
        "## 三、数据源审计",
        "",
    ]
    lines.extend(build_data_source_audit(market_data=market_data, sentinel_package=sentinel_package))
    lines.extend([
        "",
        "## 四、市场状态",
        "",
    ])
    indices = market_data.get("indices", {}) if isinstance(market_data, dict) else {}
    if indices:
        for key, value in indices.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- 行情数据缺失，市场状态降级。")
    market_note = analysis_report.get("market_context", "")
    if market_note:
        lines.append(f"- 市场摘要：{market_note}")
    lines.extend([
        "",
        "## 五、Sentinel 研究输入",
        "",
    ])
    lines.extend(build_sentinel_research_section(sentinel_package))
    lines.extend([
        "",
        "## 六、四人辩论矩阵",
        "",
    ])
    lines.extend(build_role_matrix(roles, decision))
    lines.extend([
        "",
        "## 七、裁判裁决",
        "",
        f"- 采用/否决说明：{decision.get('reasoning', decision.get('debate_summary', '暂无结构化裁决说明。'))}",
        "- 裁判输出必须继续接受账户约束、风控和一手金额校验。",
        "- AI 原文若出现旧仓位或现金规则，以本报告“机器可执行校验”中的当前策略模式为准。",
        "",
        "## 八、明日执行剧本",
        "",
    ])
    lines.extend(build_next_day_execution_playbook(
        positions=positions,
        available_cash=available_cash,
        total_assets=total_assets,
        decision=decision,
        risk_level=risk_level,
        strategy_profile=profile,
    ))
    lines.extend([
        "",
        "## 九、后续验证",
        "",
        f"- 本报告生成日：{report_date}",
        "- 四角色和裁判判断进入 Sentinel 预测留痕后，按 1/3/5/20 日回看。",
        "- 样本不足时只显示观察，不自动调整权重或 prompt。",
        "",
    ])
    return lines


def save_report_to_obsidian(
    md_content: str,
    report_date: str,
    archive_dir: str = ARCHIVE_DIR,
    title: str = "每日综合策略报告",
    push_status: dict | None = None,
) -> dict:
    """Write the markdown report, update the Obsidian index, and persist delivery state."""
    from app.services.report_archive import save_markdown_report

    os.makedirs(archive_dir, exist_ok=True)
    archive_result = save_markdown_report(
        md_content,
        report_date=report_date,
        archive_dir=archive_dir,
        title=title,
    )
    filepath = archive_result["report_path"]
    index_path = archive_result["index_path"]

    status_path = os.path.join(archive_dir, DELIVERY_STATUS_FILENAME)
    status = {"history": []}
    if os.path.exists(status_path):
        try:
            with open(status_path, 'r', encoding='utf-8') as f:
                status = json.load(f)
        except (json.JSONDecodeError, OSError):
            status = {"history": []}

    latest = {
        "report_date": report_date,
        "title": title,
        "report_path": filepath,
        "index_path": index_path,
        "obsidian_report": True,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if push_status:
        latest.update(push_status)

    history = [item for item in status.get("history", []) if item.get("report_date") != report_date]
    history.append(latest)
    status = {"latest": latest, "history": history[-60:]}
    with open(status_path, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    return {
        "report_path": filepath,
        "index_path": index_path,
        "status_path": status_path,
    }


async def push_daily_report_to_feishu(title: str, md_content: str) -> dict:
    """Push the daily report summary to Feishu and return a delivery status dict."""
    webhook_url = os.environ.get('FEISHU_WEBHOOK_URL')
    if not webhook_url or 'YOUR_WEBHOOK' in webhook_url:
        return {"feishu_webhook": False, "error": "FEISHU_WEBHOOK_URL 未配置"}

    try:
        from app.services.feishu_pusher import send_webhook_card

        ok = await send_webhook_card(
            webhook_url,
            title,
            build_feishu_summary(md_content),
        )
        if ok:
            return {"feishu_webhook": True, "error": ""}
        return {"feishu_webhook": False, "error": "Webhook 推送失败"}
    except Exception as e:
        return {"feishu_webhook": False, "error": f"飞书推送异常: {e}"}


async def main():
    import httpx
    from app.config import settings
    from app.data_sources.tencent_client import TencentDataSource
    from app.engine.analysis import run_analysis
    from app.engine.workshop import run_debate
    from app.services.portfolio_store import recalculate_portfolio, sync_db_from_user_portfolio

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
    portfolio = recalculate_portfolio(portfolio)
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            sync_result = sync_db_from_user_portfolio(db, portfolio_path)
            portfolio.setdefault("available_cash", sync_result.get("available_cash", 0))
        finally:
            db.close()
    except Exception as e:
        print(f"   ⚠️ 持仓同步数据库失败: {e}", flush=True)

    positions = portfolio.get("positions", [])
    closed = portfolio.get("closed_positions", [])

    # ===== 2. 获取行情 =====
    print("📊 获取实时行情...", flush=True)
    tc = TencentDataSource()
    strategy_profile = get_strategy_profile()
    available_cash = float(portfolio.get("available_cash", portfolio.get("cash", 0)) or 0)
    market_data = {
        "indices": {},
        "sectors": [],
        "holdings": [],
        "holdings_str": "空仓",
        "news": [],
        "available_cash": available_cash,
        "strategy_profile": strategy_profile,
    }

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
    portfolio["total_assets"] = round(available_cash + portfolio["total_value"], 2)
    market_data["total_assets"] = portfolio["total_assets"]

    # ===== 3. 分析 + 辩论 =====
    print("📊 构建市场数据摘要...", flush=True)
    report = await run_analysis(market_data)

    print("🧠 AI 辩论中...", flush=True)
    try:
        debate_result = await run_debate(report)
        decision = debate_result.get("decision", {})
        roles = debate_result.get("roles", {})
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
        roles = {}

    # ===== 4. 构建综合Markdown报告 =====
    lines = []
    try:
        from app.services.schedule_policy import main_report_target_date

        target_date = main_report_target_date(now.date()).isoformat()
    except Exception:
        target_date = today
    sentinel_package = load_sentinel_research_package(today)

    # 标题 + 元信息
    lines.append("# 📊 恭喜发财 — 次日投资策略主报告")
    lines.append("")
    lines.append(f"> 📅 **{today}** | 🕐 {time_str} | 服务交易日: **{target_date}**")
    lines.append(f"> 🤖 DeepSeek + Qwen 多角色辩论 | 📈 风险等级: **R{risk_level}**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(build_next_day_strategy_sections(
        report_date=today,
        target_date=target_date,
        risk_level=risk_level,
        final_view=final_view,
        confidence=confidence,
        positions=positions,
        available_cash=available_cash,
        total_assets=portfolio.get("total_assets", portfolio.get("total_value", 0) + available_cash),
        market_data=market_data,
        analysis_report=report,
        decision=decision,
        roles=roles,
        sentinel_package=sentinel_package,
        strategy_profile=strategy_profile,
    ))

    # ── 一、市场概况 ──
    idx = market_data.get("indices", {})
    lines.append("## 📈 一、市场概况")
    lines.append("")
    lines.append("| 指数 | 最新价 | 涨跌幅 |")
    lines.append("|------|:------:|:------:|")
    for label, key, col in [("上证指数", "shanghai", "sh_change"), ("深证成指", "shenzhen", "sz_change"), ("创业板指", "cyb", "cy_change")]:
        p = idx.get(key, "N/A")
        c = idx.get(col, 0)
        icon = "🟢" if c >= 0 else "🔴"
        lines.append(f"| {icon} {label} | {p} | {c:+.2f}% |")
    lines.append("")

    # ── 二、持仓概览 ──
    lines.append("## 💼 二、持仓概览")
    lines.append("")
    total_cost = portfolio.get("total_cost", 0)
    total_value = portfolio.get("total_value", 0)
    total_pnl = portfolio.get("total_pnl", 0)
    realized_pnl = portfolio.get("realized_pnl", 0)
    total_all = portfolio.get("total_pnl_all", 0)
    pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0

    lines.append("| 项目 | 金额 |")
    lines.append("|------|:----:|")
    lines.append(f"| 总投入成本 | ¥{total_cost:,.2f} |")
    lines.append(f"| 当前市值 | ¥{total_value:,.2f} |")
    lines.append(f"| 可用现金 | ¥{available_cash:,.2f} |")
    lines.append(f"| 估算总资产 | ¥{portfolio.get('total_assets', total_value + available_cash):,.2f} |")
    lines.append(f"| 浮动盈亏 | ¥{total_pnl:+,.2f} ({pnl_pct:+.2f}%) |")
    lines.append(f"| 已实现盈亏 | ¥{realized_pnl:+,.2f} |")
    lines.append(f"| 总盈亏 | ¥{total_all:+,.2f} |")
    lines.append("")

    if positions:
        lines.append("### 持有中")
        lines.append("")
        lines.append("| 股票 | 持仓 | 成本 | 现价 | 盈亏 | 今日涨跌 | PE | PB |")
        lines.append("|------|:----:|:----:|:----:|:----:|:--------:|:---:|:---:|")
        for p in positions:
            icon = "🟢" if p["pnl"] >= 0 else "🔴"
            change = p.get("change_pct", 0)
            change_str = f"{change:+.2f}%" if change else "—"
            pe = p.get("pe_ttm", "—")
            pb = p.get("pb", "—")
            lines.append(f"| {icon} {p['name']}({p['code']}) | {p['shares']}股 | ¥{p['avg_cost']:.3f} | ¥{p['current_price']:.3f} | {p['pnl_pct']:+.2f}% | {change_str} | {pe} | {pb} |")
        lines.append("")

    if closed:
        lines.append("### 已清仓")
        lines.append("")
        lines.append("| 股票 | 清仓价 | 盈亏 |")
        lines.append("|------|:------:|:----:|")
        for c in closed:
            icon = "🟢" if c["realized_pnl"] >= 0 else "🔴"
            lines.append(f"| {icon} {c['name']}({c['code']}) | ¥{c['close_price']:.2f} | {c['realized_pnl_pct']:+.2f}% (¥{c['realized_pnl']:+,.2f}) |")
        lines.append("")

    # ── 三、AI 多维度分析 ──
    scores = {
        "技术面": report.get("technical_score", 50),
        "基本面": report.get("fundamental_score", 50),
        "资金面": report.get("capital_score", 50),
        "情绪面": report.get("sentiment_score", 50),
    }
    bias = report.get("overall_bias", "neutral")
    bias_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(bias, "🟡")

    lines.append("## 🧠 三、AI 多维度分析")
    lines.append("")
    lines.append("| 维度 | 评分 | 评估 |")
    lines.append("|------|:----:|:----:|")
    for dim, score in scores.items():
        icon = "🟢" if score >= 60 else ("🟡" if score >= 40 else "🔴")
        level = "良好" if score >= 60 else ("中性" if score >= 40 else "偏弱")
        lines.append(f"| {dim} | **{score}** | {icon} {level} |")
    lines.append(f"| **综合倾向** | | **{bias_icon} {bias}** |")
    lines.append("")

    market_note = report.get("market_context", "")
    if market_note:
        lines.append(f"> 📌 {market_note}")
        lines.append("")

    # ── 四、AI 辩论全文 ──
    lines.append("## 🎯 四、AI 辩论结论")
    lines.append("")
    lines.append(f"- **裁判判断**: {final_view}")
    lines.append(f"- **置信度**: {confidence}/10")
    lines.append(f"- **风险等级**: R{risk_level}")
    lines.append("")

    # 辩论推理全文
    reasoning = decision.get("reasoning", decision.get("debate_summary", ""))
    if reasoning:
        lines.append("### 裁判推理")
        lines.append("")
        lines.append(f"{reasoning}")
        lines.append("")

    lines.append("### 今日行动清单")
    lines.append("")
    lines.append(f"- **总体动作**: {decision.get('final_decision', final_view)}")
    lines.append(f"- **风险等级**: R{risk_level}")
    final_action = build_final_action_summary(
        positions,
        available_cash,
        portfolio.get("total_assets", total_value + available_cash),
        strategy_profile,
    )
    lines.append(f"- **最终可执行动作**: {final_action}")
    stop_loss = decision.get("stop_loss_pct")
    if stop_loss is not None:
        lines.append(f"- **组合止损参考**: {stop_loss}%")
    lines.append("")

    guard = build_execution_guard(
        positions,
        available_cash,
        portfolio.get("total_assets", total_value + available_cash),
        strategy_profile,
    )
    if guard:
        lines.append("### 机器可执行校验")
        lines.append("")
        lines.append(guard)
        lines.append("")

    # 各角色策略详情
    role_keys = {
        "🎯 猎手（短线技术）": "short_term",
        "📊 账房（基本面估值）": "mid_low_freq",
    }
    for label, key in role_keys.items():
        role_data = decision.get(key, {})
        if role_data:
            lines.append(f"### {label}")
            lines.append("")
            strategy = role_data.get("strategy", "")
            action = role_data.get("action", "")
            advice = role_data.get("holdings_advice", "")
            if strategy:
                lines.append(f"**策略**: {strategy}")
            if action:
                lines.append(f"**操作**: {action}")
            if advice:
                lines.append(f"**持仓建议**: {advice}")
            lines.append("")

    researcher = roles.get("researcher", {}) if isinstance(roles, dict) else {}
    if researcher:
        lines.append("## 🧬 五、Serenity 产业链瓶颈视角")
        lines.append("")
        summary = researcher.get("industry_chain_summary") or researcher.get("analysis", "")
        if summary:
            lines.append(summary[:1200])
            lines.append("")

        bottlenecks = researcher.get("true_bottlenecks", [])
        if bottlenecks:
            lines.append("### 真实稀缺环节")
            lines.append("")
            for item in bottlenecks[:5]:
                if isinstance(item, dict):
                    sector = item.get("sector", "未知赛道")
                    scarce = item.get("scarce_resource", item.get("bottleneck", "未知卡点"))
                    note = item.get("beginner_note", item.get("why_overlooked", ""))
                    lines.append(f"- **{sector}**: {scarce}。{note}")
            lines.append("")

        overheated = researcher.get("overheated_sectors", [])
        if overheated:
            lines.append("### 过热/规避方向")
            lines.append("")
            for item in overheated[:5]:
                if isinstance(item, dict):
                    lines.append(f"- **{item.get('sector', '未知方向')}**: {item.get('reason', '')} {item.get('risk', '')}".strip())
            lines.append("")

        chain_risks = researcher.get("key_chain_risks", [])
        if chain_risks:
            lines.append("### 产业链风险")
            lines.append("")
            for risk in chain_risks[:5]:
                lines.append(f"- {risk}")
            lines.append("")

    # ── 五、个股操作建议（含辩论完整信息）──
    pool = decision.get("stock_pool", [])
    if positions or pool:
        lines.append("## 🎯 六、个股操作建议")
        lines.append("")

        # 当前持仓操作建议
        for p in positions:
            code = p["code"]
            name = p["name"]
            lines.append(f"### {name}({code})")
            lines.append("")
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
            lines.append("")

        # AI 推荐的其他标的
        additional = [sp for sp in pool if isinstance(sp, dict) and sp.get("code", "") not in [p["code"] for p in positions]]
        if additional:
            lines.append("### 📌 AI 关注标的")
            lines.append("")
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
                lines.append("")

    # ── 七、风险提示 ──
    risk_summary = decision.get("risk_summary", "")
    key_risks = report.get("key_risks", [])
    if risk_summary or key_risks:
        lines.append("## ⚠️ 八、风险提示")
        lines.append("")
        if risk_summary:
            lines.append(f"{risk_summary}")
            lines.append("")
        if key_risks:
            for r in key_risks:
                lines.append(f"- {r}")
            lines.append("")

    # ── 八、市场焦点 ──
    top_sectors = decision.get("top_sectors", [])
    if top_sectors:
        lines.append("## 🔍 九、市场焦点与关注板块")
        lines.append("")
        for s in top_sectors:
            if isinstance(s, dict):
                lines.append(f"- **{s.get('name', s.get('sector', '?'))}**: {s.get('reason', '')[:200]}")
            else:
                lines.append(f"- {s}")
        lines.append("")

    # ── 九、知识角 ──
    knowledge = decision.get("knowledge_corner", "")
    if knowledge:
        lines.append("## 📚 十、知识角")
        lines.append("")
        lines.append(f"{knowledge}")
        lines.append("")

    lines.append("---")
    lines.append(f"*报告生成时间: {today} {time_str}*")
    lines.append("*🤖 恭喜发财 — AI 智能分析 · 仅供参考，不构成投资建议*")

    md_content = '\n'.join(lines)

    # ===== 写入 Obsidian 报告目录 =====
    title = "次日投资策略主报告"
    delivery = save_report_to_obsidian(
        md_content,
        report_date=today,
        archive_dir=ARCHIVE_DIR,
        title=title,
        push_status={"feishu_webhook": False, "error": "pending"},
    )
    filepath = delivery["report_path"]
    print(f"✅ 报告已保存: {filepath}", flush=True)
    print(f"   📄 共 {len(lines)} 行 / {os.path.getsize(filepath)} 字节", flush=True)

    # ===== 更新持仓 =====
    portfolio["updated_at"] = now.strftime('%Y-%m-%d %H:%M:%S')
    with open(portfolio_path, 'w', encoding='utf-8') as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)
    print("✅ 持仓数据已更新", flush=True)

    # ===== 推送飞书 =====
    print("📤 推送飞书...", flush=True)
    push_status = await push_daily_report_to_feishu(
        f"📊 恭喜发财 — {today} 次日投资策略主报告",
        md_content,
    )
    if push_status.get("feishu_webhook"):
        print("   ✅ Webhook 卡片推送成功", flush=True)
    else:
        print(f"   ⚠️ {push_status.get('error', 'Webhook 推送失败')}", flush=True)

    save_report_to_obsidian(
        md_content,
        report_date=today,
        archive_dir=ARCHIVE_DIR,
        title=title,
        push_status=push_status,
    )

    print("=" * 60, flush=True)
    print("📋 每日综合报告完成", flush=True)
    print("=" * 60, flush=True)

    return filepath


if __name__ == "__main__":
    result = asyncio.run(main())
    if result:
        print(f"\n🔗 报告路径: {result}")
