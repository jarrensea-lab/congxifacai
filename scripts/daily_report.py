#!/usr/bin/env python3
"""📋 每日综合报告 — 聚合持仓策略 + 市场数据 + AI分析，生成一份完整MD报告"""
import sys
import os
import json
import asyncio
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'backend'))
os.chdir(PROJECT_ROOT)

DEFAULT_SIKU_VAULT_DIR = os.getenv(
    "SIKU_VAULT_DIR",
    os.path.join(os.path.expanduser("~"), "AI", "projects", "司库"),
)
DEFAULT_ARCHIVE_DIR = os.path.join(
    DEFAULT_SIKU_VAULT_DIR,
    "01-资料采集",
    "量化投资",
    "恭喜发财报告",
)
ARCHIVE_DIR = os.getenv("CONGXI_REPORT_ARCHIVE_DIR", DEFAULT_ARCHIVE_DIR)
DELIVERY_STATUS_FILENAME = "delivery_status.json"
SENTINEL_OUTPUT_ROOT = Path(os.getenv("CONGXI_SENTINEL_OUTPUT_ROOT", os.path.join(PROJECT_ROOT, "data", "sentinel")))


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
    max_affordable_main = round(buy_budget / 100, 2)
    max_affordable_star = round(buy_budget / 200, 2)

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
            f"- 机器校验: {action_prefix}，必须买得起对应板块最小交易单位，当前单笔预算约 ¥{buy_budget:,.2f}，"
            f"主板/创业板100股标的不高于 ¥{max_affordable_main:.2f}，科创板200股标的不高于 ¥{max_affordable_star:.2f}；"
            "买不起的只作研究参照，不进入可执行策略池。"
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
    """Load Sentinel research package for report_date, falling back to the latest available package."""
    try:
        from app.ai.sentinel_research import load_research_package

        package = load_research_package(report_date, output_root=SENTINEL_OUTPUT_ROOT)
        if package:
            return package
        package_dir = SENTINEL_OUTPUT_ROOT / "research_packages"
        if not package_dir.exists():
            return None
        for path in sorted(package_dir.glob("*.json"), reverse=True):
            fallback = load_research_package(path.stem, output_root=SENTINEL_OUTPUT_ROOT)
            if fallback:
                fallback = dict(fallback)
                fallback["fallback_used"] = True
                fallback["requested_date"] = report_date
                fallback["fallback_reason"] = "requested_date_package_missing"
                return fallback
        return None
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


def build_role_vote_audit(decision: dict) -> list[str]:
    """Render per-symbol role votes so strategy influence is auditable."""
    votes = decision.get("role_votes") if isinstance(decision, dict) else {}
    if not isinstance(votes, dict) or not votes:
        return ["- 角色投票审计：本次裁判未返回结构化 role_votes。"]

    lines = [
        "### 角色投票审计",
        "",
        "| 标的 | 猎手 | 账房 | 守夜人 | Serenity | Evidence |",
        "|---|---:|---:|---|---:|---|",
    ]
    for code, item in votes.items():
        if not isinstance(item, dict):
            continue
        hunter = item.get("hunter") if isinstance(item.get("hunter"), dict) else {}
        accountant = item.get("accountant") if isinstance(item.get("accountant"), dict) else {}
        guardian = item.get("guardian") if isinstance(item.get("guardian"), dict) else {}
        serenity = item.get("serenity") if isinstance(item.get("serenity"), dict) else {}
        evidence_ids = item.get("evidence_ids") or []
        if isinstance(evidence_ids, list):
            evidence_text = "、".join(str(eid) for eid in evidence_ids[:3])
        else:
            evidence_text = str(evidence_ids)
        veto = "否决" if guardian.get("veto") else "通过"
        guardian_reason = str(guardian.get("reason", ""))[:40]
        lines.append(
            f"| {code} | H{hunter.get('score', 0)} | A{accountant.get('score', 0)} | "
            f"{veto} {guardian_reason} | S{serenity.get('score', 0)} | {evidence_text or '无'} |"
        )
    if len(lines) == 4:
        return ["- 角色投票审计：role_votes 为空或格式不可用。"]
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
    if package.get("fallback_used"):
        lines.append(
            f"- 研究包日期：{package.get('date')}；请求日期 {package.get('requested_date')} 无包，已使用最新可用包。"
        )
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
    deepseek_status = "configured" if os.getenv("DEEPSEEK_API_KEY") else "missing"
    qwen_status = "configured" if (os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")) else "missing"
    if deepseek_ok is not None:
        deepseek_status = "ok" if deepseek_ok else "degraded"
    if qwen_ok is not None:
        qwen_status = "ok" if qwen_ok else "degraded"
    rows = [
        "| 数据源 | 状态 | 覆盖/说明 |",
        "|---|---|---|",
        f"| 行情数据 | {'ok' if indices else 'degraded'} | 指数 {len(indices)} 项 |",
        f"| Tushare 高频新闻 | {sentinel_status.get('status', 'missing')} | 新闻 {(sentinel_package or {}).get('event_count', 0)} 条 |",
        f"| Sentinel 研究包 | {'ok' if sentinel_package else 'missing'} | 研究输入，不产生交易指令 |",
        f"| DeepSeek | {deepseek_status} | 四角色/裁判主模型；状态表示配置存在，不等于本次探活成功 |",
        f"| Qwen | {qwen_status} | 研究员/备用裁判；状态表示配置存在，不等于本次探活成功 |",
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


def _money(value) -> str:
    try:
        return f"¥{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _cell(value, limit: int = 120) -> str:
    text = str(value or "—").replace("\n", " ").replace("|", "/").strip()
    return text[:limit] if len(text) > limit else text


def _target_label(item: dict) -> str:
    code = str(item.get("code") or item.get("stock_code") or "").strip()
    name = str(item.get("name") or item.get("stock_name") or code).strip()
    return f"{name}({code})" if code else name


def _target_scores(decision: dict) -> list[dict]:
    scores = decision.get("target_scores") if isinstance(decision, dict) else None
    return [item for item in scores if isinstance(item, dict)] if isinstance(scores, list) else []


def _outside_pool_scan(decision: dict) -> list[dict]:
    rows = decision.get("outside_pool_scan") if isinstance(decision, dict) else None
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _round_price(value: float) -> float:
    return round(max(0.0, float(value or 0)), 2)


def _split_target_scores(decision: dict) -> dict[str, list[dict]]:
    buckets = {
        "executable": [],
        "watching": [],
        "research_reference": [],
        "removed": [],
    }
    for item in _target_scores(decision):
        action = str(item.get("action") or item.get("status") or "").lower()
        block_reason = str(item.get("block_reason") or "")
        if action in {"buy", "add", "actionable", "executable"}:
            buckets["executable"].append(item)
        elif action in {"research_only", "research_reference"} or block_reason == "lot_size_exceeded":
            buckets["research_reference"].append(item)
        elif action in {"remove", "removed", "sell", "avoid", "expired"}:
            buckets["removed"].append(item)
        else:
            buckets["watching"].append(item)
    return buckets


def _render_target_bucket(title: str, rows: list[dict], empty_text: str) -> list[str]:
    lines = [f"### {title}", ""]
    if not rows:
        return lines + [f"- {empty_text}", ""]

    lines.extend([
        "| 标的 | 动作 | 买入/触发价 | 仓位 | 止损 | 目标 | 依据 |",
        "|---|---|---:|---:|---:|---:|---|",
    ])
    action_names = {
        "buy": "买入/试仓",
        "add": "加仓",
        "watch": "继续观察",
        "hold": "持有",
        "research_only": "研究参照",
        "research_reference": "研究参照",
        "remove": "剔除",
        "removed": "剔除",
    }
    for item in rows:
        action = str(item.get("action") or item.get("status") or "watch").lower()
        label = _target_label(item)
        entry = _money(item.get("entry_price") or item.get("trigger_price"))
        amount = _money(item.get("position_amount") or item.get("suggested_amount"))
        stop_loss = _money(item.get("stop_loss"))
        target_price = _money(item.get("target_price"))
        reason = item.get("decision_reason") or item.get("reason") or item.get("block_reason")
        if item.get("lot_value") and "买不起" not in str(reason):
            reason = f"{reason or ''}；一手金额约{_money(item.get('lot_value'))}".strip("；")
        lines.append(
            f"| {_cell(label, 40)} | {action_names.get(action, _cell(action, 20))} | "
            f"{entry} | {amount} | {stop_loss} | {target_price} | {_cell(reason, 160)} |"
        )
    lines.append("")
    return lines


def _first_executable_target(decision: dict) -> dict | None:
    for item in _split_target_scores(decision)["executable"]:
        if str(item.get("action") or "").lower() in {"buy", "add", "actionable", "executable"}:
            return item
    return None


def _holding_action_lines(positions: list[dict], total_assets: float) -> list[str]:
    lines = []
    if not positions:
        return [
            "- 当前持仓怎么处理：当前无持仓，无卖出动作。",
            "- 是否需要卖：不需要，卖出监控保持空转。",
        ]

    lines.extend([
        "| 持仓 | 股数 | 现价 | 市值 | 动作 | 触发信号 |",
        "|---|---:|---:|---:|---|---|",
    ])
    for pos in positions:
        shares = int(pos.get("shares", pos.get("position", 0)) or 0)
        price = float(pos.get("current_price", 0) or 0)
        value = float(pos.get("current_value", shares * price) or 0)
        ratio = value / total_assets * 100 if total_assets else 0
        label = _target_label(pos)
        action = "持有监控"
        signal = "跌破止损、触及目标价、或仓位超限时触发飞书预警"
        lines.append(f"| {_cell(label, 40)} | {shares} | {_money(price)} | {_money(value)} | {action} | {_cell(signal)} |")
        if ratio:
            lines.append(f"<!-- {label} 当前约占总资产 {ratio:.1f}% -->")
    return lines


def _new_entry_action_lines(decision: dict) -> list[str]:
    buy = _first_executable_target(decision)
    if not buy:
        outside_scan = _outside_pool_scan(decision)
        actionable_scan = [
            item for item in outside_scan
            if item.get("affordable") is not False and (item.get("suggested_amount") or item.get("lot_value"))
        ]
        if actionable_scan:
            top = actionable_scan[0]
            label = _target_label(top)
            amount = top.get("suggested_amount") or top.get("lot_value")
            trigger = top.get("trigger_price") or top.get("current_price") or top.get("max_entry_price")
            stop_loss = top.get("stop_loss")
            target = top.get("target_price")
            budget = top.get("executable_budget") or top.get("lot_value")
            reason = top.get("watch_reason") or "等实时价格、量能、成交额、资金流同时触发。"
            return [
                "- 是否需要买：今天不主动买入；明天只做条件触发，不预挂单。",
                f"- 如果动，动哪只：优先复核 {label}。",
                f"- 多少钱：一手试错约{_money(amount)}，不得超过单票预算{_money(budget)}。",
                f"- 什么价格买：{_money(trigger)}以内观察，资金流未转正或高开追涨不买。",
                f"- 错了哪里止损：跌破{_money(stop_loss)}止损；第一目标看{_money(target)}。",
                f"- 今天不动，明天等什么信号：{reason}",
            ]
        if outside_scan:
            top = outside_scan[0]
            reason = top.get("watch_reason") or "等回落到最高观察价以内，并补齐实时量能和资金流。"
            return [
                "- 是否需要买：今天不主动买入。",
                f"- 如果动，动哪只：暂无可执行买入；池外先观察 {_target_label(top)}。",
                "- 多少钱：不下单。",
                f"- 什么价格买：等回落到{_money(top.get('max_entry_price') or top.get('trigger_price'))}以内并重新评分。",
                "- 错了哪里止损：无新仓，不设置新止损；已有持仓按持仓策略执行。",
                f"- 今天不动，明天等什么信号：{reason}",
            ]
        return [
            "- 是否需要买：今天不主动买入。",
            "- 如果动，动哪只：暂无通过账户预算、最小交易单位、价格触发和风控过滤的标的。",
            "- 多少钱：不下单。",
            "- 什么价格买：等待标的池给出明确触发价。",
            "- 错了哪里止损：无新仓，不设置新止损；已有持仓按持仓策略执行。",
            "- 今天不动，明天等什么信号：等可执行标的同时满足放量、价格触发、风险未否决、且一手金额买得起。",
        ]
    label = _target_label(buy)
    amount = _money(buy.get("position_amount") or buy.get("suggested_amount"))
    entry = _money(buy.get("entry_price") or buy.get("trigger_price"))
    stop_loss = _money(buy.get("stop_loss"))
    target = _money(buy.get("target_price"))
    reason = buy.get("decision_reason") or "通过结构化评分和账户可执行性校验。"
    return [
        "- 是否需要买：可以进入人工复核买入。",
        f"- 如果动，动哪只：{label}。",
        f"- 多少钱：建议金额 {amount}，不得超过报告给出的单票预算。",
        f"- 什么价格买：{entry} 附近或触发价内，不追高。",
        f"- 错了哪里止损：{stop_loss} 硬止损；目标位 {target}。",
        f"- 依据：{reason}",
        "- 今天不动，明天等什么信号：若未成交，继续等放量延续、回踩不破触发位、资金流未转弱。",
    ]


def _research_archive_lines(sentinel_package: dict | None, roles: dict, decision: dict) -> list[str]:
    lines = [
        "- 完整辩论记录：保留在本地辩论/报告归档，主报告只展示可执行结论。",
        "- Sentinel 归一数据包：保留高频新闻、主题热度、风险事件和证据编号。",
        "- Serenity 深挖：保留产业链瓶颈、候选锚点和验证问题；进入策略前必须再过账户与行情评分。",
    ]
    if sentinel_package:
        lines.append(
            f"- Sentinel 数据状态：{(sentinel_package.get('source_status') or {}).get('status', 'unknown')}；"
            f"新闻 {sentinel_package.get('event_count', 0)} 条，关键新闻 {sentinel_package.get('key_event_count', 0)} 条。"
        )
        themes = sentinel_package.get("top_themes") or []
        if themes:
            lines.append("- 主题热度：" + "；".join(f"{item.get('name')}({item.get('count')})" for item in themes[:5]))
        dives = sentinel_package.get("serenity_deep_dives") or []
        if dives:
            lines.append("- Serenity 深挖文件：")
            for dive in dives[:5]:
                path = str(dive.get("learning_report_path") or "")
                basename = os.path.basename(path) if path else "未记录路径"
                candidates = dive.get("top_candidates") or []
                candidate_text = ""
                if candidates:
                    candidate_text = "；候选：" + "、".join(
                        f"{item.get('name', item.get('code'))}({item.get('code')})"
                        for item in candidates[:3]
                    )
                lines.append(f"  - {dive.get('theme', '未知主题')}: {basename}{candidate_text}")
    researcher = roles.get("researcher") if isinstance(roles, dict) else None
    if researcher:
        lines.append("- Serenity研究员：本次已作为研究证据源参与，具体观点以归档全文为准。")
    if isinstance(decision, dict) and decision.get("role_votes"):
        lines.append("- 角色投票明细：见“数据覆盖与评分”中的 role_votes 审计表。")
    return lines


def _render_outside_pool_scan(rows: list[dict]) -> list[str]:
    lines = ["### 池外小账户补扫", ""]
    if not rows:
        return lines + ["- 本次未生成池外补扫候选；需要扩展可执行候选源。", ""]
    lines.extend([
        "| 标的 | 现价 | 一手金额 | 最高观察价 | 触发/观察价 | 止损 | 目标 | 来源 | 明日等待信号 |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ])
    for item in rows[:8]:
        lines.append(
            f"| {_cell(_target_label(item), 40)} | {_money(item.get('current_price'))} | "
            f"{_money(item.get('lot_value'))} | {_money(item.get('max_entry_price'))} | "
            f"{_money(item.get('trigger_price') or item.get('max_entry_price'))} | "
            f"{_money(item.get('stop_loss'))} | {_money(item.get('target_price'))} | "
            f"{_cell(item.get('source'), 40)} | {_cell(item.get('watch_reason'), 160)} |"
        )
    lines.append("")
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
    total_assets = total_assets or available_cash
    target_buckets = _split_target_scores(decision)
    final_action = build_final_action_summary(positions, available_cash, total_assets, profile)
    lines = [
        "## 一、今日账户操作策略",
        "",
        f"- 服务交易日：{target_date}",
        f"- 策略模式：{profile['title']}",
        f"- 目标：{profile['target']}",
        "- 验收口径：不承诺收益，验证报告、风控、人工复核和后续复盘是否按规则执行。",
        f"- 裁判结论：{final_view}",
        f"- 置信度：{confidence}/10",
        f"- 风险等级：R{risk_level}",
        f"- 总体倾向：{analysis_report.get('overall_bias', 'neutral')}",
        f"- 当前资产：现金 {_money(available_cash)}；估算总资产 {_money(total_assets)}；持仓 {len(positions)} 只。",
        f"- 当前持仓怎么处理：{final_action}",
    ]
    lines.extend(_new_entry_action_lines(decision))
    lines.extend([
        "",
        "## 二、持仓处理策略",
        "",
    ])
    lines.extend(_holding_action_lines(positions, total_assets))
    lines.extend([
        "",
        "## 三、新开仓策略",
        "",
    ])
    lines.extend(_render_target_bucket(
        "今日可执行标的",
        target_buckets["executable"],
        "暂无通过账户预算、最小交易单位、行情触发和风控过滤的标的。",
    ))
    lines.extend(_render_outside_pool_scan(_outside_pool_scan(decision)))
    lines.extend([
        "## 四、标的池分层",
        "",
    ])
    lines.extend(_render_target_bucket(
        "观察等待触发标的",
        target_buckets["watching"],
        "暂无观察标的；若 Sentinel/Serenity 有新线索，先进入研究参照或观察等待触发。",
    ))
    lines.extend(_render_target_bucket(
        "研究参照标的",
        target_buckets["research_reference"],
        "暂无研究参照标的；买不起一手或只具备产业链锚点价值的标的会放在这里。",
    ))
    lines.extend(_render_target_bucket(
        "今日剔除标的",
        target_buckets["removed"],
        "暂无剔除标的。",
    ))
    lines.extend([
        "",
        "## 五、数据覆盖与评分",
        "",
        "- 数据源审计：",
    ])
    lines.extend(f"  {line}" for line in build_data_source_audit(market_data=market_data, sentinel_package=sentinel_package))
    target_scores = _target_scores(decision)
    if target_scores:
        lines.extend([
            "",
            "### 标的评分",
            "",
            "| 标的 | 分数 | 动作 | 数据缺口 | 阻断原因 |",
            "|---|---:|---|---|---|",
        ])
        for item in target_scores:
            missing = item.get("missing_data") or []
            missing_text = "、".join(str(value) for value in missing) if isinstance(missing, list) else str(missing)
            lines.append(
                f"| {_cell(_target_label(item), 40)} | {item.get('score', 0)} | "
                f"{_cell(item.get('action') or item.get('status'), 30)} | {_cell(missing_text or '无', 80)} | "
                f"{_cell(item.get('block_reason') or '无', 80)} |"
            )
    else:
        lines.extend([
            "",
            "- 标的评分：本次未生成结构化 target_scores；不能把研究线索直接当成买入建议。",
        ])
    lines.extend([
        "",
        "- 辩论权重说明：Serenity研究员提供产业链瓶颈证据，不直接下买卖指令；守夜人风控否决优先生效。",
        "",
    ])
    lines.extend(build_role_vote_audit(decision))
    lines.extend([
        "",
        "## 六、复盘与自迭代",
        "",
        f"- 本报告生成日：{report_date}",
        f"- 裁判采用/否决说明：{decision.get('reasoning', decision.get('debate_summary', '暂无结构化裁决说明。'))}",
        "- 每个进入可执行池的标的必须留存触发价、止损、目标位、账户预算和证据编号。",
        "- 每个观察标的按 1/3/5/20 日回看：符合预期进可执行池，不符合预期剔除。",
        "- 每个研究参照标的只验证方向和产业链假设，买得起且行情触发后才允许迁移到观察/可执行池。",
        "",
        "## 七、研究归档链接",
        "",
    ])
    lines.extend(_research_archive_lines(sentinel_package, roles, decision))
    lines.append("")
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


async def build_target_scores_for_report(
    *,
    available_cash: float,
    total_assets: float,
    limit: int | None = None,
) -> list[dict]:
    """Score current target-pool items with normalized data snapshots."""
    from app.ai.serenity_financial_evidence import fetch_financial_evidence
    from app.data_sources.akshare_market import AKShareMarketClient
    from app.data_sources.akshare_news import AKShareNewsClient
    from app.data_sources.tencent_client import TencentDataSource
    from app.services.quant_lifecycle import TargetPoolStore
    from app.services.target_scoring import score_target
    from app.services.target_snapshot import build_target_snapshot

    class CachedMarketSource:
        def __init__(self):
            self.client = AKShareMarketClient()
            self._fund_flows = None
            self._northbound = None

        async def fetch_fund_flow_individual(self):
            if self._fund_flows is None:
                self._fund_flows = await self.client.fetch_fund_flow_individual()
            return self._fund_flows

        async def fetch_hsgt_flow(self):
            if self._northbound is None:
                self._northbound = await self.client.fetch_hsgt_flow()
            return self._northbound

    financial_cache: dict[str, dict] = {}

    async def cached_financial_fetcher(codes: list[str]) -> dict[str, dict]:
        missing = [code for code in codes if code not in financial_cache]
        if missing:
            financial_cache.update(await fetch_financial_evidence(missing))
        return {code: financial_cache.get(code, {}) for code in codes}

    store = TargetPoolStore()
    payload = store.load()
    items = [
        item for item in payload.get("items", {}).values()
        if isinstance(item, dict) and item.get("status") not in {"removed", "expired"}
    ]
    max_items = limit or int(os.getenv("CONGXI_TARGET_SCORE_LIMIT", "12"))
    items = items[:max_items]
    if not items:
        return []

    quote_source = TencentDataSource()
    market_source = CachedMarketSource()
    news_source = AKShareNewsClient()
    scores: list[dict] = []
    for item in items:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        snapshot = await build_target_snapshot(
            code,
            name=item.get("name", code),
            quote_source=quote_source,
            market_source=market_source,
            news_source=news_source,
            financial_fetcher=cached_financial_fetcher,
            sentinel=item.get("sentinel") or {},
            serenity=item.get("serenity") or {},
        )
        score = score_target(snapshot, available_cash=available_cash, total_assets=total_assets)
        score["source_status"] = {
            key: (snapshot.get(key) or {}).get("status")
            for key in ("quote", "kline", "fund_flow", "northbound", "news", "financial", "sentinel", "serenity")
        }
        quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
        action = str(score.get("action") or "")
        next_status = {
            "buy": "executable",
            "add": "executable",
            "research_only": "research_reference",
            "remove": "removed",
        }.get(action, "watching")
        store.upsert_target(
            code=code,
            name=score.get("name") or item.get("name", code),
            status=next_status,
            source="target_scoring",
            evidence=item.get("evidence") or {},
            evidence_ids=item.get("evidence_ids") or [],
            sentinel=item.get("sentinel") or {},
            serenity=item.get("serenity") or {},
            current_price=quote.get("price"),
            available_cash=available_cash,
            total_assets=total_assets,
        )
        scores.append(score)
    return sorted(scores, key=lambda row: float(row.get("score", 0) or 0), reverse=True)


async def build_outside_pool_scan_for_report(
    *,
    available_cash: float,
    total_assets: float,
    existing_codes: set[str] | None = None,
) -> list[dict]:
    """Build a small-account outside-pool scan with live quote context."""
    from app.data_sources.tencent_client import TencentDataSource
    from app.services.small_account_discovery import build_small_account_seed_candidates

    seeds = build_small_account_seed_candidates(
        available_cash=available_cash,
        total_assets=total_assets,
        existing_codes=existing_codes or set(),
    )
    if not seeds:
        return []
    quote_source = TencentDataSource()
    quotes = await quote_source.fetch_batch([item["code"] for item in seeds])
    rows: list[dict] = []
    for seed in seeds:
        quote = quotes.get(seed["code"]) or {}
        price = float(quote.get("price") or 0)
        lot_value = round(price * int(seed.get("lot_size") or 100), 2) if price > 0 else 0
        affordable = bool(price > 0 and price <= float(seed.get("max_entry_price") or 0))
        change_pct = float(quote.get("change_pct") or 0)
        vol_ratio = float(quote.get("vol_ratio") or 0)
        amount_wan = float(quote.get("amount_wan") or 0)
        volume_clue = bool(vol_ratio >= 2 and amount_wan >= 10000)
        chasing_risk = bool(change_pct >= 9)
        actionability_rank = 0
        if affordable and volume_clue and not chasing_risk:
            actionability_rank = 3
        elif affordable and not chasing_risk:
            actionability_rank = 2
        elif affordable:
            actionability_rank = 1
        reason = seed["watch_reason"]
        if price <= 0:
            reason = "池外小账户补扫；实时价格缺失，先补 quote。"
        elif not affordable:
            reason = f"池外小账户补扫；现价高于最高观察价，等回落到¥{seed['max_entry_price']:.2f}以内。"
        elif chasing_risk:
            reason = "池外小账户补扫；接近追高区，等回踩确认，不追涨。"
        elif volume_clue:
            reason = "池外小账户补扫；已具备量能线索，明日若资金流转正且不高开追涨，可一手试错复核。"
        trigger_price = _round_price(price if price > 0 and affordable else seed.get("max_entry_price") or 0)
        stop_loss = _round_price(trigger_price * 0.95) if trigger_price > 0 and affordable else None
        target_price = _round_price(trigger_price * 1.12) if trigger_price > 0 and affordable else None
        rows.append({
            **seed,
            "name": quote.get("name") or seed["name"],
            "current_price": price,
            "lot_value": lot_value,
            "affordable": affordable,
            "change_pct": change_pct,
            "vol_ratio": vol_ratio,
            "amount_wan": amount_wan,
            "volume_clue": volume_clue,
            "chasing_risk": chasing_risk,
            "actionability_rank": actionability_rank,
            "trigger_price": trigger_price,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "suggested_amount": lot_value if affordable else 0,
            "executable_budget": round(min(available_cash, total_assets * 0.35 if total_assets else available_cash), 2),
            "watch_reason": reason,
        })
    return sorted(
        rows,
        key=lambda item: (
            -int(item.get("actionability_rank") or 0),
            not item.get("affordable"),
            -float(item.get("amount_wan") or 0),
        ),
    )


async def finalize_daily_report(
    *,
    lines: list[str],
    today: str,
    time_str: str,
    portfolio: dict,
    portfolio_path: str,
) -> str:
    """Persist the markdown report, update portfolio metadata, and push Feishu summary."""
    lines.append("---")
    lines.append(f"*报告生成时间: {today} {time_str}*")
    lines.append("*🤖 恭喜发财 — AI 智能分析 · 仅供参考，不构成投资建议*")

    md_content = '\n'.join(lines)
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

    portfolio["updated_at"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(portfolio_path, 'w', encoding='utf-8') as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)
    print("✅ 持仓数据已更新", flush=True)

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


async def main():
    from app.data_sources.tencent_client import TencentDataSource
    from app.engine.analysis import run_analysis
    from app.engine.workshop import run_debate
    from app.services.evidence_ledger import build_sentinel_evidence_context, upsert_sentinel_evidence_to_target_pool
    from app.services.portfolio_store import recalculate_portfolio, sync_db_from_user_portfolio

    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')

    print(f"📋 每日综合报告 — {today}", flush=True)

    print("=" * 60, flush=True)

    # ===== 1. 读取持仓 =====
    portfolio_path = os.environ.get(
        "CONGXI_PORTFOLIO_PATH",
        os.path.join(PROJECT_ROOT, 'data', 'user_portfolio.json'),
    )
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
    sentinel_package = load_sentinel_research_package(today)
    if sentinel_package:
        market_data["sentinel_evidence"] = build_sentinel_evidence_context(sentinel_package)
        try:
            ingest_result = upsert_sentinel_evidence_to_target_pool(sentinel_package)
            print(
                "   Sentinel evidence 接入: "
                f"{ingest_result.get('evidence_count', 0)} 条证据, "
                f"{ingest_result.get('upserted_targets', 0)} 个标的入池",
                flush=True,
            )
        except Exception as e:
            print(f"   ⚠️ Sentinel evidence 入池失败，降级继续: {e}", flush=True)

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

    print("🎯 生成标的池评分...", flush=True)
    try:
        target_scores = await build_target_scores_for_report(
            available_cash=available_cash,
            total_assets=portfolio.get("total_assets", portfolio.get("total_value", 0) + available_cash),
        )
        if target_scores:
            decision["target_scores"] = target_scores
            print(f"   标的评分完成: {len(target_scores)} 个标的", flush=True)
        else:
            print("   标的池为空或无可评分标的", flush=True)
    except Exception as e:
        print(f"   ⚠️ 标的评分失败，报告降级继续: {e}", flush=True)

    print("🔎 生成池外小账户补扫...", flush=True)
    try:
        existing_codes = {
            str(item.get("code") or "").strip()
            for item in decision.get("target_scores", [])
            if isinstance(item, dict)
        }
        outside_scan = await build_outside_pool_scan_for_report(
            available_cash=available_cash,
            total_assets=portfolio.get("total_assets", portfolio.get("total_value", 0) + available_cash),
            existing_codes=existing_codes,
        )
        decision["outside_pool_scan"] = outside_scan
        print(f"   池外补扫完成: {len(outside_scan)} 个候选", flush=True)
    except Exception as e:
        print(f"   ⚠️ 池外补扫失败，报告降级继续: {e}", flush=True)

    # ===== 4. 构建综合Markdown报告 =====
    lines = []
    try:
        from app.services.schedule_policy import main_report_target_date

        target_date = main_report_target_date(now.date()).isoformat()
    except Exception:
        target_date = today
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

    if os.getenv("CONGXI_REPORT_LEGACY_SECTIONS", "0") != "1":
        return await finalize_daily_report(
            lines=lines,
            today=today,
            time_str=time_str,
            portfolio=portfolio,
            portfolio_path=portfolio_path,
        )

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

    return await finalize_daily_report(
        lines=lines,
        today=today,
        time_str=time_str,
        portfolio=portfolio,
        portfolio_path=portfolio_path,
    )


if __name__ == "__main__":
    result = asyncio.run(main())
    if result:
        print(f"\n🔗 报告路径: {result}")
