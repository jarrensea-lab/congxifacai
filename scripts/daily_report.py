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
    max_affordable_main = int((buy_budget / 100) * 100) / 100 if buy_budget else 0
    max_affordable_star = int((buy_budget / 200) * 100) / 100 if buy_budget else 0

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


def build_role_vote_audit(decision: dict, hidden_codes: set[str] | None = None) -> list[str]:
    """Render per-symbol role votes so strategy influence is auditable."""
    hidden_codes = hidden_codes or set()
    votes = decision.get("role_votes") if isinstance(decision, dict) else {}
    if not isinstance(votes, dict) or not votes:
        return ["- 角色投票审计：本次裁判未返回结构化角色投票。"]

    lines = [
        "### 角色投票审计",
        "",
        "| 标的 | 猎手 | 账房 | 守夜人 | Serenity | 证据编号 |",
        "|---|---:|---:|---|---:|---|",
    ]
    hidden_count = 0
    for code, item in votes.items():
        if str(code) in hidden_codes:
            hidden_count += 1
            continue
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
        guardian_note = f"{veto}；原始理由见本地辩论快照，主报告以结构化评分为准"
        lines.append(
            f"| {code} | 猎手 {hunter.get('score', 0)}分 | 账房 {accountant.get('score', 0)}分 | "
            f"{guardian_note} | Serenity {serenity.get('score', 0)}分 | {evidence_text or '无'} |"
        )
    if hidden_count:
        lines.append(f"| 预算阻断隐藏项 | — | — | 已隐藏 {hidden_count} 只买不起标的的角色投票 | — | 本地审计日志 |")
    if len(lines) == 4:
        return ["- 角色投票审计：角色投票为空或格式不可用。"]
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
        f"| DeepSeek | {_status_label(deepseek_status)} | 四角色/裁判主模型；状态表示配置存在，不等于本次探活成功 |",
        f"| Qwen | {_status_label(qwen_status)} | 研究员/备用裁判；状态表示配置存在，不等于本次探活成功 |",
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


def _status_label(value: str) -> str:
    labels = {
        "ok": "可用",
        "configured": "已配置",
        "missing": "缺失",
        "degraded": "降级",
    }
    return labels.get(str(value or "").lower(), str(value or "—"))


def _missing_data_label(value: str) -> str:
    labels = {
        "quote": "实时行情",
        "kline": "K线",
        "fund_flow": "个股资金流",
        "financial": "财务数据",
        "northbound": "北向资金",
        "news": "新闻/公告",
        "sentinel": "Sentinel证据",
        "serenity": "Serenity研究",
    }
    return labels.get(str(value or ""), str(value or ""))


def _missing_data_text(values) -> str:
    if isinstance(values, list):
        return "、".join(_missing_data_label(str(value)) for value in values) or "无"
    return _humanize_reason(values) if values else "无"


def _action_label(value: str) -> str:
    labels = {
        "buy": "可人工复核买入",
        "add": "可人工复核加仓",
        "actionable": "可人工复核",
        "executable": "可执行候选",
        "watch": "观察等待",
        "watching": "观察等待",
        "hold": "持有",
        "research_only": "研究参照",
        "research_reference": "研究参照",
        "remove": "剔除",
        "removed": "剔除",
        "avoid": "规避",
    }
    return labels.get(str(value or "").lower(), _cell(value, 30))


def _block_reason_label(value: str) -> str:
    labels = {
        "lot_size_exceeded": "买不起最小交易单位",
        "missing_required_data": "关键数据未补齐",
        "price_missing": "实时价格缺失",
        "blocked_chasing": "追高风险",
        "price_not_triggered": "价格/量能/资金未同时触发",
    }
    return labels.get(str(value or "").lower(), _humanize_reason(value) if value else "无")


def _source_label(value: str) -> str:
    labels = {
        "small_account_discovery": "小账户低价候选",
        "target_scoring": "标的池评分",
        "sentinel_serenity": "研究证据入池",
    }
    return labels.get(str(value or "").lower(), _cell(value, 40))


def _humanize_reason(value) -> str:
    text = str(value or "").replace("kline", "K线").replace("fund_flow", "个股资金流")
    text = text.replace("quote", "实时行情").replace("financial", "财务数据")
    text = text.replace("research_only", "研究参照")
    text = text.replace("lot_size_exceeded", "买不起最小交易单位")
    text = text.replace("missing_required_data", "关键数据未补齐")
    text = text.replace("small_account_discovery", "小账户低价候选")
    text = text.replace("缺少结构化数据项", "缺少关键数据")
    text = text.replace("池外小账户补扫", "小账户低价候选扫描")
    return text


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


def _target_code(item: dict) -> str:
    return str(item.get("code") or item.get("stock_code") or "").strip()


def _lot_size_for_code(code: str) -> int:
    return 200 if str(code).startswith(("688", "689")) else 100


def _lot_value(item: dict) -> float:
    try:
        value = float(item.get("lot_value") or 0)
        if value > 0:
            return value
        code = _target_code(item)
        price = float(item.get("current_price") or item.get("entry_price") or item.get("trigger_price") or 0)
        return round(price * _lot_size_for_code(code), 2) if price > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _is_budget_blocked(item: dict, buy_budget: float) -> bool:
    if item.get("affordable") is False:
        return True
    if item.get("block_reason") == "lot_size_exceeded":
        return True
    lot_value = _lot_value(item)
    return bool(lot_value and buy_budget and lot_value > buy_budget)


def _hidden_budget_codes(decision: dict, buy_budget: float) -> set[str]:
    codes: set[str] = set()
    for item in _target_scores(decision) + _outside_pool_scan(decision):
        if _is_budget_blocked(item, buy_budget):
            code = _target_code(item)
            if code:
                codes.add(code)
    return codes


def _visible_target_scores(decision: dict, hidden_codes: set[str]) -> list[dict]:
    return [item for item in _target_scores(decision) if _target_code(item) not in hidden_codes]


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
    for item in rows:
        action = str(item.get("action") or item.get("status") or "watch").lower()
        label = _target_label(item)
        entry = _money(item.get("entry_price") or item.get("trigger_price"))
        amount = _money(item.get("position_amount") or item.get("suggested_amount"))
        stop_loss = _money(item.get("stop_loss"))
        target_price = _money(item.get("target_price"))
        reason = _humanize_reason(item.get("decision_reason") or item.get("reason") or item.get("block_reason"))
        if item.get("lot_value") and "买不起" not in str(reason):
            reason = f"{reason or ''}；一手金额约{_money(item.get('lot_value'))}".strip("；")
        lines.append(
            f"| {_cell(label, 40)} | {_action_label(action)} | "
            f"{entry} | {amount} | {stop_loss} | {target_price} | {_cell(reason, 160)} |"
        )
    lines.append("")
    return lines


def _first_executable_target(decision: dict) -> dict | None:
    for item in _split_target_scores(decision)["executable"]:
        if str(item.get("action") or "").lower() in {"buy", "add", "actionable", "executable"}:
            return item
    return None


def _affordable_outside_targets(decision: dict) -> list[dict]:
    return [
        item for item in _outside_pool_scan(decision)
        if item.get("affordable") is not False and (item.get("suggested_amount") or item.get("lot_value"))
    ]


def _primary_trade_candidate(decision: dict) -> dict | None:
    return _first_executable_target(decision) or (_affordable_outside_targets(decision)[0] if _affordable_outside_targets(decision) else None)


def _candidate_edge(item: dict | None) -> str:
    if not item:
        return "暂无明确做多逻辑；等待标的池给出量价资金共振信号。"
    reason = _humanize_reason(item.get("decision_reason") or item.get("watch_reason") or item.get("reason"))
    if "量能线索" in reason:
        return "已具备量能线索，博弈低价股资金回流。"
    if "资金流" in reason or "放量" in reason:
        return _cell(reason, 90)
    return _cell(reason or "通过账户预算和结构化评分，等待盘前触发确认。", 90)


def _market_effect_line(risk_level: int, analysis_report: dict, market_data: dict) -> str:
    indices = market_data.get("indices", {}) if isinstance(market_data, dict) else {}
    limit_up = analysis_report.get("limit_up_count", indices.get("limit_up_count")) if isinstance(analysis_report, dict) else None
    limit_down = analysis_report.get("limit_down_count", indices.get("limit_down_count")) if isinstance(analysis_report, dict) else None
    bias = (analysis_report or {}).get("overall_bias", "neutral")
    if limit_up is not None and limit_down is not None:
        try:
            up = int(limit_up)
            down = int(limit_down)
            ratio = up / max(down, 1)
            if risk_level >= 4 and ratio < 4:
                effect = "偏低"
            elif ratio >= 4:
                effect = "较好"
            else:
                effect = "一般"
            return f"风险等级：R{risk_level} | 市场倾向：{bias} | 赚钱效应：{effect}（涨停{up} / 跌停{down}，建议控仓）"
        except (TypeError, ValueError):
            pass
    if risk_level >= 4:
        return f"风险等级：R{risk_level} | 市场倾向：{bias} | 赚钱效应：偏低（缺涨跌停家数，按高风险环境控仓）"
    return f"风险等级：R{risk_level} | 市场倾向：{bias} | 赚钱效应：待确认（缺涨跌停家数）"


def _render_core_dashboard(
    *,
    target_date: str,
    risk_level: int,
    final_view: str,
    positions: list[dict],
    available_cash: float,
    total_assets: float,
    decision: dict,
    analysis_report: dict,
    market_data: dict,
    profile: dict,
) -> list[str]:
    primary = _primary_trade_candidate(decision)
    buy_budget, _ = _buy_budget_for_profile(available_cash, total_assets, profile)
    lines = [
        "## 一、明日【唯一】实盘狙击标的（可执行）",
        "",
        f"- 服务交易日：{target_date}",
        f"- 大盘环境：{_market_effect_line(risk_level, analysis_report, market_data)}",
        f"- 账户现状：现金/总资产 {_money(available_cash)} / {_money(total_assets)}；当前持仓 {len(positions)} 只；单票预算 {_money(buy_budget)}。",
        f"- 裁判最终结论：{final_view}",
    ]
    if not primary:
        lines.extend([
            "- 核心主攻：暂无。",
            "- 执行动作：不下单，不预挂单；等待条件触发池出现买得起且数据完整的标的。",
        ])
        return lines + [""]

    label = _target_label(primary)
    amount = primary.get("suggested_amount") or primary.get("position_amount") or primary.get("lot_value")
    trigger = primary.get("trigger_price") or primary.get("entry_price") or primary.get("current_price")
    stop_loss = primary.get("stop_loss")
    target_price = primary.get("target_price")
    lines.extend([
        f"- 核心主攻：{label}",
        f"- 买入逻辑：{_candidate_edge(primary)}",
        f"- 执行条件：明日个股资金流转正，量能延续，且不高开追涨；触发价参考 {_money(trigger)}。",
        f"- 资金配置：一手约{_money(amount)}，不得超过单票预算 {_money(buy_budget)}。",
        f"- 风控密码：止损位：{_money(stop_loss)}；第一目标位：{_money(target_price)}。",
    ])
    lines.append("")
    return lines


def _same_symbol(left: dict, right: dict | None) -> bool:
    if not right:
        return False
    left_code = str(left.get("code") or left.get("stock_code") or "")
    right_code = str(right.get("code") or right.get("stock_code") or "")
    return bool(left_code and left_code == right_code)


def _render_trigger_pool(decision: dict, primary: dict | None, hidden_codes: set[str]) -> list[str]:
    rows = [
        item for item in _split_target_scores(decision)["executable"] + _affordable_outside_targets(decision)
        if not _same_symbol(item, primary) and _target_code(item) not in hidden_codes
    ]
    missing_rows = [
        item for item in _target_scores(decision)
        if _target_code(item) not in hidden_codes
        and (item.get("block_reason") == "missing_required_data" or item.get("missing_data"))
    ]
    lines = ["## 二、明日盘中雷达触发池", ""]
    lines.extend([
        "> 警报：这里只保留账户预算通过、明天值得抓取/比对的数据标的；买不起的高价股不进入主报告视野。",
        "",
        "### 1. 强观察触发待定股（预算通过，数据缺失）",
        "",
    ])
    if missing_rows:
        lines.extend([
            "| 标的 | 现价/触发价 | 一手门槛 | 缺口数据 | 迁移可执行条件 |",
            "|---|---:|---:|---|---|",
        ])
        for item in missing_rows[:8]:
            trigger = item.get("entry_price") or item.get("trigger_price") or item.get("current_price")
            lines.append(
                f"| {_cell(_target_label(item), 40)} | {_money(trigger)} | {_money(_lot_value(item))} | "
                f"{_cell(_missing_data_text(item.get('missing_data') or []), 80)} | "
                "补齐数据后评分>60分，且盘中量能/个股资金流触发 |"
            )
    else:
        lines.append("- 暂无预算通过但数据缺失的强观察标的。")
    lines.extend(["", "### 2. 备选跟踪小票（预算通过，等待盘中四合一触发）", ""])
    if not rows:
        return lines + ["- 暂无其他小账户可买的盘中触发标的；明日重点只看第一部分主攻标的。", ""]

    lines.extend([
        "| 标的 | 现价 | 一手门槛 | 明日等待抓取/比对的信号 |",
        "|---|---:|---:|---|",
    ])
    for item in rows[:8]:
        trigger = item.get("trigger_price") or item.get("entry_price") or item.get("current_price")
        amount = item.get("suggested_amount") or item.get("position_amount") or item.get("lot_value")
        lines.append(
            f"| {_cell(_target_label(item), 40)} | {_money(trigger)} | {_money(amount)} | "
            "盘中比对：实时价格、量能、成交额、个股资金流是否四合一触发 |"
        )
    lines.append("")
    return lines


def _render_budget_blocks(
    *,
    decision: dict,
    target_buckets: dict[str, list[dict]],
    hidden_codes: set[str],
    available_cash: float,
    total_assets: float,
    profile: dict,
) -> list[str]:
    buy_budget, _ = _buy_budget_for_profile(available_cash, total_assets, profile)
    max_main_price = int((buy_budget / 100) * 100) / 100 if buy_budget else 0
    lines = [
        f"### 账户预算不足阻断（当前可用单票上限 {_money(buy_budget)}；主板账户可买上限价 {_money(max_main_price)}）",
        "",
    ]
    if hidden_codes:
        lines.append(
            f"- 预算阻断 {len(hidden_codes)} 只：已从主报告正文隐藏，不进入 AI 辩论输入、不进入雷达池、不占用盘前视线。"
        )
        lines.append("- 明细保留在本地结构化审计日志；主报告只展示可执行标的、预算通过的雷达候选和补数据任务。")
    else:
        lines.append("- 暂无因账户预算不足被阻断的标的。")
    lines.append("")

    missing_rows = [
        item for item in _target_scores(decision)
        if _target_code(item) not in hidden_codes
        and (
            item.get("block_reason") == "missing_required_data" or item.get("missing_data")
        )
    ]
    lines.extend(["### 明日必须补齐数据进行比对的标的", ""])
    if missing_rows:
        lines.extend([
            "| 标的 | 缺失数据 | 处理方式 |",
            "|---|---|---|",
        ])
        for item in missing_rows[:10]:
            lines.append(
                f"| {_cell(_target_label(item), 40)} | {_cell(_missing_data_text(item.get('missing_data') or []), 80)} | "
                "先补数据，补齐前不进入可执行买入 |"
            )
    else:
        lines.append("- 暂无关键数据缺失阻断。")
    lines.append("")
    return lines


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
            reason = _humanize_reason(top.get("watch_reason") or "等实时价格、量能、成交额、资金流同时触发。")
            return [
                "- 新开仓结论：今天不主动买入；明天只做条件触发，不预挂单。",
                f"- 候选标的：优先复核 {label}。",
                f"- 建议试仓金额：一手试错约{_money(amount)}，不得超过单票预算{_money(budget)}。",
                f"- 触发价：{_money(trigger)}以内观察，个股资金流未转正或高开追涨不买。",
                f"- 止损/目标：跌破{_money(stop_loss)}止损；第一目标看{_money(target)}。",
                f"- 盘前复核信号：{reason}",
            ]
        if outside_scan:
            top = outside_scan[0]
            reason = _humanize_reason(top.get("watch_reason") or "等回落到账户可买上限价以内，并补齐实时量能和资金流。")
            return [
                "- 新开仓结论：今天不主动买入。",
                f"- 候选标的：暂无可执行买入；先观察 {_target_label(top)}。",
                "- 建议试仓金额：不下单。",
                f"- 触发价：等回落到{_money(top.get('max_entry_price') or top.get('trigger_price'))}以内并重新评分。",
                "- 止损/目标：无新仓，不设置新止损；已有持仓按持仓策略执行。",
                f"- 盘前复核信号：{reason}",
            ]
        return [
            "- 新开仓结论：今天不主动买入。",
            "- 候选标的：暂无通过账户预算、最小交易单位、价格触发和风控过滤的标的。",
            "- 建议试仓金额：不下单。",
            "- 触发价：等待标的池给出明确触发价。",
            "- 止损/目标：无新仓，不设置新止损；已有持仓按持仓策略执行。",
            "- 盘前复核信号：等可执行标的同时满足放量、价格触发、风险未否决、且一手金额买得起。",
        ]
    label = _target_label(buy)
    amount = _money(buy.get("position_amount") or buy.get("suggested_amount"))
    entry = _money(buy.get("entry_price") or buy.get("trigger_price"))
    stop_loss = _money(buy.get("stop_loss"))
    target = _money(buy.get("target_price"))
    reason = _humanize_reason(buy.get("decision_reason") or "通过结构化评分和账户可执行性校验。")
    return [
        "- 新开仓结论：可以进入人工复核买入。",
        f"- 候选标的：{label}。",
        f"- 建议试仓金额：{amount}，不得超过报告给出的单票预算。",
        f"- 触发价：{entry} 附近或触发价内，不追高。",
        f"- 止损/目标：{stop_loss} 硬止损；目标位 {target}。",
        f"- 依据：{reason}",
        "- 盘前复核信号：若未成交，继续等放量延续、回踩不破触发位、个股资金流未转弱。",
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
        lines.append("- 角色投票明细：见“数据覆盖与评分”中的角色投票审计表。")
    return lines


def _render_outside_pool_scan(rows: list[dict]) -> list[str]:
    lines = ["### 池外小账户补扫", ""]
    if not rows:
        return lines + ["- 本次未生成池外补扫候选；需要扩展可执行候选源。", ""]
    lines.extend([
        "| 标的 | 现价 | 一手金额 | 账户可买上限价 | 触发/观察价 | 止损 | 目标 | 来源 | 明日等待信号 |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ])
    for item in rows[:8]:
        lines.append(
            f"| {_cell(_target_label(item), 40)} | {_money(item.get('current_price'))} | "
            f"{_money(item.get('lot_value'))} | {_money(item.get('max_entry_price'))} | "
            f"{_money(item.get('trigger_price') or item.get('max_entry_price'))} | "
            f"{_money(item.get('stop_loss'))} | {_money(item.get('target_price'))} | "
            f"{_source_label(item.get('source'))} | {_cell(_humanize_reason(item.get('watch_reason')), 160)} |"
        )
    lines.append("")
    return lines


def _buy_budget_for_profile(available_cash: float, total_assets: float, profile: dict) -> tuple[float, float]:
    assets = float(total_assets or available_cash or 0)
    cash = float(available_cash or 0)
    single_pct = float(profile.get("single_position_limit_pct", 50) or 50)
    reserve_pct = float(profile.get("cash_reserve_pct", 10) or 10)
    reserve_cash = assets * reserve_pct / 100 if assets else 0
    single_limit = assets * single_pct / 100 if assets else cash
    return round(max(0.0, min(cash - reserve_cash, single_limit)), 2), single_pct


def _render_mid_frequency_strategy(
    rows: list[dict],
    *,
    available_cash: float,
    total_assets: float,
    profile: dict,
) -> list[str]:
    lines = ["### 中低频观察/配置线", ""]
    if not rows:
        return lines + [
            "- 当前没有形成中低频观察/配置候选；后续需要从 Sentinel/Serenity 研究池和财务评分中补充。",
            "",
        ]

    buy_budget, single_pct = _buy_budget_for_profile(available_cash, total_assets, profile)
    lines.append("- 当前没有可执行中低频买入；以下标的只给观察/配置条件，不触发下单。")
    lines.extend([
        "",
        "| 标的 | 当前结论 | 一手门槛 | 账户缺口 | 迁移条件 |",
        "|---|---|---:|---|---|",
    ])
    for item in rows[:6]:
        code = str(item.get("code") or item.get("stock_code") or "")
        lot_size = int(item.get("lot_size") or (200 if code.startswith(("688", "689")) else 100))
        lot_value = float(item.get("lot_value") or 0)
        entry_price = float(item.get("entry_price") or item.get("trigger_price") or 0)
        if lot_value <= 0 and entry_price > 0:
            lot_value = round(entry_price * lot_size, 2)
        max_price = int((buy_budget / lot_size) * 100) / 100 if lot_size else 0
        required_assets = round(lot_value / (single_pct / 100), 2) if single_pct and lot_value else 0
        if lot_value > buy_budget:
            gap = f"当前单票预算{_money(buy_budget)}，差{_money(max(0, lot_value - buy_budget))}"
            migration = (
                f"价格回落至{_money(max_price)}以内，或账户总资产至少{_money(required_assets)}"
                f"且现金不少于{_money(lot_value)}；再补齐K线/个股资金流后重新评分"
            )
        else:
            missing = _missing_data_text(item.get("missing_data") or [])
            gap = f"预算可覆盖一手；待补数据：{missing}"
            migration = "补齐K线/个股资金流并通过趋势、估值和风控评分后，才可迁移到可执行候选"
        lines.append(
            f"| {_cell(_target_label(item), 40)} | 中低频研究参照 | {_money(lot_value)} | "
            f"{_cell(gap, 90)} | {_cell(migration, 180)} |"
        )
    lines.append("")
    return lines


def _structured_review_summary(target_buckets: dict[str, list[dict]], decision: dict) -> str:
    scores = _target_scores(decision)
    if not scores:
        return _cell(_humanize_reason(decision.get("reasoning", decision.get("debate_summary", "暂无结构化裁决说明。"))), 500)

    parts = [
        "结构化评分摘要：",
        f"可执行 {len(target_buckets['executable'])} 只",
        f"观察等待 {len(target_buckets['watching'])} 只",
        f"研究参照 {len(target_buckets['research_reference'])} 只",
        f"剔除 {len(target_buckets['removed'])} 只。",
    ]
    blockers: list[str] = []
    for item in scores:
        reason = _block_reason_label(item.get("block_reason"))
        if reason and reason != "无" and reason not in blockers:
            blockers.append(reason)
    if blockers:
        parts.append("主要阻断：" + "、".join(blockers[:4]) + "。")
    missing = []
    for item in scores:
        for value in item.get("missing_data") or []:
            label = _missing_data_label(str(value))
            if label and label not in missing:
                missing.append(label)
    if missing:
        parts.append("待补数据：" + "、".join(missing[:4]) + "。")
    parts.append("AI裁判原文保留在本地辩论快照；主报告以账户预算、最小交易单位和结构化评分为准。")
    return "".join(parts)


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
    """Build the profit-first opening sections of the main report."""
    profile = strategy_profile or get_strategy_profile()
    total_assets = total_assets or available_cash
    buy_budget, _ = _buy_budget_for_profile(available_cash, total_assets, profile)
    hidden_codes = _hidden_budget_codes(decision, buy_budget)
    visible_decision = dict(decision)
    visible_decision["target_scores"] = _visible_target_scores(decision, hidden_codes)
    visible_decision["outside_pool_scan"] = [
        item for item in _outside_pool_scan(decision)
        if _target_code(item) not in hidden_codes
    ]
    target_buckets = _split_target_scores(visible_decision)
    primary = _primary_trade_candidate(visible_decision)
    lines = _render_core_dashboard(
        target_date=target_date,
        risk_level=risk_level,
        final_view=final_view,
        positions=positions,
        available_cash=available_cash,
        total_assets=total_assets,
        decision=visible_decision,
        analysis_report=analysis_report,
        market_data=market_data,
        profile=profile,
    )
    lines.extend(_render_trigger_pool(visible_decision, primary, hidden_codes))
    lines.extend([
        "## 三、持仓与市场风控",
        "",
        f"- 策略模式：{profile['title']}；目标：{profile['target']}。",
        "- 验收口径：不承诺收益，只验证报告、风控、人工复核和后续复盘是否按规则执行。",
        f"- 置信度：{confidence}/10。",
        f"- 账户处理：{build_final_action_summary(positions, available_cash, total_assets, profile)}",
        "",
        "### 持仓处理",
        "",
    ])
    lines.extend(_holding_action_lines(positions, total_assets))
    lines.extend([
        "",
        "### 机器可执行校验",
        "",
    ])
    lines.extend(build_execution_guard(positions, available_cash, total_assets, profile).splitlines())
    lines.extend([
        "",
        "## 四、后台风控与策略审计",
        "",
    ])
    lines.extend(_render_budget_blocks(
        decision=decision,
        target_buckets=target_buckets,
        hidden_codes=hidden_codes,
        available_cash=available_cash,
        total_assets=total_assets,
        profile=profile,
    ))
    lines.extend(_render_mid_frequency_strategy(
        target_buckets["research_reference"],
        available_cash=available_cash,
        total_assets=total_assets,
        profile=profile,
    ))
    lines.extend(_render_target_bucket(
        "今日可执行标的明细",
        target_buckets["executable"],
        "暂无通过账户预算、最小交易单位、行情触发和风控过滤的标的。",
    ))
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
        "## 五、数据覆盖与评分审计",
        "",
        "- 数据源审计：",
    ])
    lines.extend(f"  {line}" for line in build_data_source_audit(market_data=market_data, sentinel_package=sentinel_package))
    target_scores = _target_scores(visible_decision)
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
            lines.append(
                f"| {_cell(_target_label(item), 40)} | {item.get('score', 0)} | "
                f"{_action_label(item.get('action') or item.get('status'))} | {_cell(_missing_data_text(missing), 80)} | "
                f"{_cell(_block_reason_label(item.get('block_reason')), 80)} |"
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
    lines.extend(build_role_vote_audit(decision, hidden_codes=hidden_codes))
    if target_scores or hidden_codes:
        review_summary = _structured_review_summary(target_buckets, visible_decision)
        if hidden_codes and not target_scores:
            review_summary = (
                f"结构化评分摘要：主报告可见标的 0 只；预算阻断 {len(hidden_codes)} 只已隐藏。"
                "AI裁判原文保留在本地辩论快照；主报告不展示买不起标的或其幻觉价格。"
            )
    else:
        review_summary = _structured_review_summary(target_buckets, visible_decision)
    lines.extend([
        "",
        "## 六、复盘与自迭代",
        "",
        f"- 本报告生成日：{report_date}",
        f"- 裁判采用/否决说明：{review_summary}",
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
    profile = get_strategy_profile()
    single_pct = float(profile.get("single_position_limit_pct", 50) or 50)
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
            reason = f"池外小账户补扫；现价高于账户可买上限价，等回落到¥{seed['max_entry_price']:.2f}以内。"
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
            "executable_budget": round(min(available_cash, total_assets * single_pct / 100 if total_assets else available_cash), 2),
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
