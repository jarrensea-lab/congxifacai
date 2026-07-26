#!/usr/bin/env python3
"""📋 每日综合报告 — 聚合持仓策略 + 市场数据 + AI分析，生成一份完整MD报告"""
import sys
import os
import json
import asyncio
import math
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
FEISHU_SUMMARY_END_MARKER = "<!-- FEISHU_SUMMARY_END -->"


from app.services.strategy_profile import (
    calculate_stop_loss_price,
    calculate_target_price,
    get_strategy_profile,
)
from app.services.visible_decision_gate import (
    ENTRY_ACTIONS,
    apply_visible_decision_gate,
    build_visible_decision_gate,
    format_visible_decision_reasons,
    write_visible_decision_gate,
)


def _load_yitaojin_quote_validation_for_decision(
    decision: dict,
    *,
    now: datetime | None = None,
) -> dict:
    """Load the runtime quote snapshot and apply the 30-second action limit."""
    critical_codes: set[str] = set()
    for field in ("target_scores", "outside_pool_scan"):
        rows = decision.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            action = str(row.get("action") or row.get("status") or "").strip().lower()
            is_entry = action in ENTRY_ACTIONS or row.get("actionable") is True
            if field == "outside_pool_scan" and not action:
                is_entry = bool(
                    row.get("suggested_amount")
                    or row.get("position_amount")
                    or row.get("lot_value")
                )
            code = str(row.get("code") or row.get("stock_code") or "").strip()
            if is_entry and len(code) == 6 and code.isdigit():
                critical_codes.add(code)
    try:
        from app.config import resolve_runtime_yitaojin_paths
        from app.integrations.yitaojin.quotes import (
            load_quote_validation_summary,
        )

        return load_quote_validation_summary(
            resolve_runtime_yitaojin_paths().quote_snapshot,
            now=now,
            critical_codes=critical_codes,
        )
    except Exception:
        enabled = (
            os.getenv("CONGXI_YITAOJIN_ENABLED", "").strip().lower()
            == "true"
        )
        return {
            "enabled": enabled,
            "status": "unavailable" if enabled else "not_enabled",
            "as_of": None,
            "requested_codes": [],
            "validations": {},
            "reasons": ["quote_snapshot_unavailable"],
        }


def _read_iso_date_env(name: str):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD, got {raw!r}") from exc


def build_feishu_summary(md_content: str, limit: int = 3000) -> str:
    """Build the execution summary while keeping the audit appendix in Obsidian."""
    summary = md_content.split(FEISHU_SUMMARY_END_MARKER, 1)[0].rstrip()
    if len(summary) <= limit:
        return summary
    return summary[:limit].rstrip() + "\n\n...*(完整报告已保存至 Obsidian 报告目录)*"


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
        f"单票上限 {single_pct:.0f}%，单笔账户风险 {profile['risk_per_trade_pct']}%，"
        f"止损 {profile['stop_loss_pct']}%，最低目标 {profile.get('target_profit_pct', 12)}%。"
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


def _sentinel_package_age_days(package: dict | None, report_date: str | None) -> int | None:
    if not package or not report_date or not package.get("date"):
        return None
    try:
        current = datetime.strptime(str(report_date), "%Y-%m-%d").date()
        package_date = datetime.strptime(str(package.get("date")), "%Y-%m-%d").date()
    except ValueError:
        return None
    return max(0, (current - package_date).days)


def build_sentinel_research_section(package: dict | None, report_date: str | None = None) -> list[str]:
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
    age_days = _sentinel_package_age_days(
        package,
        report_date or package.get("requested_date"),
    )
    if age_days is not None and age_days > 2:
        lines.extend([
            f"- 研究包日期：{package.get('date')}，已过期 {age_days} 天。",
            "- Serenity：旧深挖不参与当前候选判断；等待新的研究包生成。",
            "- 边界：旧报告只保留作历史复盘。",
        ])
        return lines
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


MARKET_DATA_MAX_AGE_SECONDS = 15 * 60


def _is_recent_market_cutoff(value) -> bool:
    if not value:
        return False
    cutoff_text = value.isoformat() if isinstance(value, datetime) else str(value).strip()
    try:
        parsed = datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    age_seconds = (now - parsed).total_seconds()
    return -60 <= age_seconds <= MARKET_DATA_MAX_AGE_SECONDS


def _fresh_market_quote_truth(quote: dict) -> tuple[str, str] | None:
    freshness = str(
        quote.get("freshness_status") or quote.get("freshness") or ""
    ).strip().lower()
    if freshness not in {"fresh", "ok"}:
        return None
    raw_cutoff = (
        quote.get("data_cutoff")
        or quote.get("quote_timestamp")
        or quote.get("timestamp")
    )
    if not _is_recent_market_cutoff(raw_cutoff):
        return None
    cutoff_text = (
        raw_cutoff.isoformat()
        if isinstance(raw_cutoff, datetime)
        else str(raw_cutoff).strip()
    )
    return cutoff_text, freshness


def build_data_source_audit(
    *,
    market_data: dict,
    sentinel_package: dict | None,
    portfolio_loaded: bool = True,
    sqlite_ok: bool | None = None,
    deepseek_ok: bool | None = None,
    qwen_ok: bool | None = None,
) -> list[str]:
    """Render data-source audit rows for the main report."""
    indices = market_data.get("indices", {}) if isinstance(market_data, dict) else {}
    market_status = market_data.get("market_source_status", {}) if isinstance(market_data, dict) else {}
    market_status = market_status if isinstance(market_status, dict) else {}
    data_cutoff = market_status.get("data_cutoff")
    freshness_status = str(
        market_status.get("freshness_status") or market_status.get("freshness") or ""
    ).strip().lower()
    market_ok = (
        market_status.get("status") == "ok"
        and bool(indices)
        and freshness_status in {"fresh", "ok"}
        and _is_recent_market_cutoff(data_cutoff)
    )
    if market_ok:
        market_detail = (
            f"指数 {len(indices)} 项；provider={market_status.get('provider') or 'unknown'}；"
            f"data_cutoff={data_cutoff}；freshness={freshness_status}"
        )
    else:
        failure_reasons = []
        if market_status.get("status") != "ok":
            failure_reasons.append(
                str(market_status.get("error") or "market_status_not_ok")
            )
        if not indices:
            failure_reasons.append("indices_missing")
        if not data_cutoff:
            failure_reasons.append("data_cutoff_missing")
        elif not _is_recent_market_cutoff(data_cutoff):
            failure_reasons.append("data_cutoff_stale_or_invalid")
        if freshness_status not in {"fresh", "ok"}:
            failure_reasons.append(
                f"freshness_unproven:{freshness_status or 'missing'}"
            )
        market_detail = (
            f"指数 {len(indices)} 项；provider={market_status.get('provider') or 'unknown'}；"
            f"data_cutoff={data_cutoff or 'unknown'}；"
            f"失败原因={','.join(failure_reasons)}"
        )
    sentinel_status = (sentinel_package or {}).get("source_status") or {}
    deepseek_status = "configured" if os.getenv("DEEPSEEK_API_KEY") else "missing"
    qwen_status = "configured" if (os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")) else "missing"
    if deepseek_ok is not None:
        deepseek_status = "ok" if deepseek_ok else "degraded"
    if qwen_ok is not None:
        qwen_status = "ok" if qwen_ok else "degraded"
    if sqlite_ok is None:
        sqlite_ok = not (
            market_data.get("portfolio_sync_failed") is True
            or str(market_data.get("portfolio_sync_status") or "").strip().lower()
            in {"failed", "error", "degraded", "unavailable"}
        )
    rows = [
        "| 数据源 | 状态 | 覆盖/说明 |",
        "|---|---|---|",
        f"| 行情数据 | {'ok' if market_ok else 'degraded'} | {market_detail} |",
        f"| Tushare 高频新闻 | {sentinel_status.get('status', 'missing')} | 新闻 {(sentinel_package or {}).get('event_count', 0)} 条 |",
        f"| Sentinel 研究包 | {'ok' if sentinel_package else 'missing'} | 研究输入，不产生交易指令 |",
        f"| DeepSeek | {_status_label(deepseek_status)} | 四角色/裁判主模型；状态表示配置存在，不等于本次探活成功 |",
        f"| Qwen | {_status_label(qwen_status)} | 研究员/备用裁判；状态表示配置存在，不等于本次探活成功 |",
        f"| 本地持仓 | {'ok' if portfolio_loaded else 'missing'} | 账户约束优先生效 |",
        f"| SQLite | {'ok' if sqlite_ok else 'degraded'} | 辩论快照与持仓同步 |",
    ]
    return rows


def sync_portfolio_database_truth(
    portfolio: dict,
    portfolio_path: str,
    *,
    session_factory=None,
    sync_fn=None,
) -> dict[str, object]:
    """Sync portfolio truth without persisting exception details into report inputs."""
    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal
    if sync_fn is None:
        from app.services.portfolio_store import sync_db_from_user_portfolio

        sync_fn = sync_db_from_user_portfolio

    db = None
    try:
        db = session_factory()
        sync_result = sync_fn(db, portfolio_path)
        if isinstance(sync_result, dict):
            portfolio.setdefault("available_cash", sync_result.get("available_cash", 0))
        portfolio["portfolio_sync_failed"] = False
        portfolio["portfolio_sync_status"] = "ok"
        return {"ok": True, "status": "ok"}
    except Exception:
        portfolio["portfolio_sync_failed"] = True
        portfolio["portfolio_sync_status"] = "failed"
        return {"ok": False, "status": "failed"}
    finally:
        if db is not None:
            db.close()


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
        "risk_budget_too_small": "风险预算不足",
        "regime_blocks_dip": "大盘环境阻断低吸",
        "blocked_high_position": "区间高位观察",
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
        "blocked_high_position": "近20日区间高位",
        "risk_budget_too_small": "一手风险超过预算",
        "regime_blocks_dip": "大盘/板块环境阻断低吸",
        "price_not_triggered": "价格/量能/资金未同时触发",
    }
    return labels.get(str(value or "").lower(), _humanize_reason(value) if value else "无")


def _source_label(value: str) -> str:
    labels = {
        "small_account_discovery": "小账户低价候选",
        "dynamic_fund_flow_discovery": "动态资金流研究候选",
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
    text = text.replace("breakout_entry", "放量突破买点")
    text = text.replace("dip_entry", "回踩买点")
    text = text.replace("缺少结构化数据项", "缺少关键数据")
    text = text.replace("池外小账户补扫", "小账户低价候选扫描")
    text = text.replace("。，", "，").replace("。。", "。")
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


def _positive_float(value):
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _round_price(value: float) -> float:
    return round(max(0.0, float(value or 0)), 2)


def _effective_target_action(item: dict) -> str:
    production_eligibility = (
        item.get("production_eligibility")
        if isinstance(item.get("production_eligibility"), dict)
        else {}
    )
    if production_eligibility.get("eligible") is False:
        return "research_only"
    return str(item.get("action") or item.get("status") or "watch")


def _split_target_scores(decision: dict) -> dict[str, list[dict]]:
    buckets = {
        "executable": [],
        "watching": [],
        "research_reference": [],
        "removed": [],
    }
    for item in _target_scores(decision):
        action = _effective_target_action(item).lower()
        block_reason = str(item.get("block_reason") or "")
        production_eligibility = (
            item.get("production_eligibility")
            if isinstance(item.get("production_eligibility"), dict)
            else {}
        )
        if production_eligibility.get("eligible") is False:
            buckets["research_reference"].append(item)
        elif action in {"buy", "add", "actionable", "executable"}:
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


def _affordable_outside_targets(decision: dict, *, include_research: bool = False) -> list[dict]:
    return [
        item for item in _outside_pool_scan(decision)
        if item.get("affordable") is not False
        and (item.get("suggested_amount") or item.get("lot_value"))
        and (
            include_research
            or (not item.get("research_only") and item.get("entry_allowed") is not False)
        )
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


def _position_watch_items(decision: dict) -> dict:
    watch = decision.get("position_watch") if isinstance(decision, dict) else None
    if isinstance(watch, dict):
        items = watch.get("items", watch)
        if isinstance(items, dict):
            return items
    return {}


def _watch_for_position(pos: dict, watch_items: dict) -> dict:
    code = _target_code(pos)
    item = watch_items.get(code) if isinstance(watch_items, dict) else None
    return item if isinstance(item, dict) else {}


def _quote_freshness_for_service_date(pos: dict, target_date: str | None) -> str:
    freshness = str(pos.get("quote_freshness") or pos.get("freshness") or "").strip().lower()
    if freshness not in {"valid_close", "fresh_close"}:
        return freshness
    if not target_date:
        return "stale"
    try:
        from app.utils.trading_calendar import prev_trading_day

        service_day = datetime.strptime(target_date, "%Y-%m-%d").date()
        expected_close_date = prev_trading_day(service_day).isoformat()
    except (TypeError, ValueError):
        return "stale"
    quote_date = str(pos.get("quote_trading_date") or pos.get("trading_date") or "")
    return "fresh_close" if quote_date == expected_close_date else "stale"


def _holding_quote_alarm(pos: dict, target_date: str | None = None) -> str | None:
    status = str(pos.get("quote_status") or "").strip().lower()
    freshness = _quote_freshness_for_service_date(pos, target_date)
    source = pos.get("quote_source") or pos.get("source")
    quote_time = pos.get("quote_timestamp")
    trading_date = pos.get("quote_trading_date") or pos.get("trading_date")
    status_alarms = {
        "missing": "行情缺失",
        "fetch_failed": "行情获取失败",
        "suspended": "行情停牌",
        "conflict": "行情来源冲突",
        "source_conflict": "行情来源冲突",
        "adjustment_anomaly": "行情异常复权",
    }
    if status in status_alarms:
        return status_alarms[status]
    if freshness == "stale":
        return "行情陈旧"
    if freshness == "conflict":
        return "行情来源冲突"
    if freshness in {"unknown", "missing"}:
        return "行情新鲜度未知"
    if freshness not in {"fresh", "fresh_close"}:
        return "行情新鲜度未知"
    if not source or not (quote_time or trading_date):
        return "行情来源或时间不可审计"
    return None


def _holding_stop_breaches(
    positions: list[dict],
    decision: dict,
    target_date: str | None = None,
) -> list[dict]:
    watch_items = _position_watch_items(decision)
    breaches: list[dict] = []
    for pos in positions:
        watch = _watch_for_position(pos, watch_items)
        stop_loss = watch.get("stop_loss_price") or watch.get("stop_loss")
        try:
            stop_price = float(stop_loss or 0)
            price = float(pos.get("current_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not price or not stop_price or price > stop_price or _holding_quote_alarm(pos, target_date):
            continue
        freshness = _quote_freshness_for_service_date(pos, target_date)
        breaches.append({
            "label": _target_label(pos),
            "shares": int(pos.get("shares", pos.get("position", 0)) or 0),
            "price": price,
            "stop_loss": stop_price,
            "price_label": "有效收盘价" if freshness == "fresh_close" else "现价",
        })
    return breaches


def _holding_quote_alarms(
    positions: list[dict],
    decision: dict,
    target_date: str | None = None,
) -> list[dict]:
    watch_items = _position_watch_items(decision)
    alarms: list[dict] = []
    for pos in positions:
        watch = _watch_for_position(pos, watch_items)
        stop_loss = watch.get("stop_loss_price") or watch.get("stop_loss")
        try:
            stop_price = float(stop_loss or 0)
            price = float(pos.get("current_price", 0) or 0)
        except (TypeError, ValueError):
            stop_price = 0
            price = 0
        alarm = _holding_quote_alarm(pos, target_date)
        has_explicit_quote_state = any(
            key in pos
            for key in (
                "quote_status", "quote_freshness", "freshness", "quote_source",
                "quote_timestamp", "quote_trading_date", "trading_date",
            )
        )
        if alarm and stop_price and (has_explicit_quote_state or (price and price <= stop_price)):
            alarms.append({
                "label": _target_label(pos),
                "alarm": alarm,
                "stop_loss": stop_price,
            })
    return alarms


def _apply_position_quote(position: dict, quote: dict, *, service_date: str) -> bool:
    """Apply only actionable quotes to account valuation; retain invalid quotes for audit."""
    has_price = bool(quote and quote.get("price"))
    status = str(
        quote.get("quote_status")
        or quote.get("status")
        or ("valid" if has_price else "missing")
    ).strip().lower()
    position["quote_source"] = quote.get("source")
    position["quote_timestamp"] = quote.get("quote_timestamp")
    position["quote_trading_date"] = quote.get("trading_date")
    position["quote_captured_at"] = quote.get("captured_at")
    position["quote_freshness"] = quote.get("freshness", "unknown")
    position["quote_status"] = status
    if has_price:
        position["last_quote_price"] = quote.get("price")
        position["last_quote_timestamp"] = quote.get("quote_timestamp")

    effective_freshness = _quote_freshness_for_service_date(position, service_date)
    position["quote_freshness"] = effective_freshness
    valid_status = status in {"valid", "ok", "success"}
    actionable = has_price and valid_status and effective_freshness in {"fresh", "fresh_close"}
    if not actionable:
        return False

    price = float(quote["price"])
    shares = int(position.get("shares", position.get("position", 0)) or 0)
    total_cost = float(position.get("total_cost", 0) or 0)
    position["current_price"] = price
    position["change_pct"] = quote.get("change_pct", 0)
    position["current_value"] = shares * price
    position["pnl"] = position["current_value"] - total_cost
    position["pnl_pct"] = (position["pnl"] / total_cost) * 100 if total_cost else 0
    return True


def _price_from_item(item: dict):
    for key in ("current_price", "price", "entry_price", "trigger_price"):
        value = item.get(key)
        try:
            price = float(value or 0)
        except (TypeError, ValueError):
            price = 0
        if price > 0:
            return price
    return None


def _trigger_from_item(item: dict):
    return item.get("trigger_price") or item.get("entry_price") or item.get("current_price") or item.get("max_entry_price")


def _stop_from_item(item: dict):
    return item.get("stop_loss") or item.get("stop_loss_price")


def _target_from_item(item: dict):
    return item.get("target_price") or item.get("take_profit") or item.get("take_profit_price")


def _planned_holding_period(item: dict) -> str:
    for key in ("holding_period", "planned_holding_period", "period"):
        value = item.get(key)
        if value:
            return _cell(value, 36)
    if item.get("thesis_status") or item.get("long_quality_score"):
        return "1-3个月跟踪，5/20日复核"
    return "5-20个交易日观察"


def _trend_text(item: dict) -> str:
    parts = []
    if item.get("thesis_status"):
        parts.append(_thesis_status_label(item.get("thesis_status")))
    if item.get("valuation_zone"):
        parts.append(_valuation_zone_label(item.get("valuation_zone")))
    if item.get("long_quality_score") is not None:
        parts.append(f"长期分{item.get('long_quality_score')}")
    return " / ".join(parts) if parts else "趋势待验证"


def _project_status_section(
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
    sentinel_package: dict | None,
    profile: dict,
    budget_blocked_count: int,
    stop_breach_alerts: list[dict] | None = None,
    quote_data_alarms: list[dict] | None = None,
    visible_decision_gate: dict | None = None,
) -> list[str]:
    lines = [
        "## 一、系统和项目工作状态",
        "",
        f"- 系统结论：主报告已生成；服务交易日 {target_date}；报告日 {report_date}；R{risk_level}；置信度 {confidence}/10。",
        f"- 策略模式：{profile['title']}；目标：{profile['target']}。",
        "- 策略结论：执行动作以下方“明日持仓策略/短线关注/中长线关注”三张表为准；AI裁判原文已归档。",
        f"- 账户状态：持仓 {len(positions)} 只；可用现金 {_money(available_cash)}；总资产 {_money(total_assets)}。",
        f"- 行情/评分：{_market_effect_line(risk_level, analysis_report, market_data)}。",
        f"- 风控校验：预算阻断 {budget_blocked_count} 只；主报告以结构化评分为准，角色投票和裁判原文只留在 Obsidian。",
    ]
    if stop_breach_alerts:
        breach_text = "；".join(
            f"{item['label']} 已跌破止损 {_money(item['stop_loss'])}"
            f"（{item['price_label']} {_money(item['price'])}，{item['shares']}股）"
            for item in stop_breach_alerts[:3]
        )
        lines.append(
            f"- 开盘前硬风控：{breach_text}；新开仓暂停，优先处理风险仓；"
            "立即执行减仓/退出，禁止补仓/抢反弹。"
        )
    if visible_decision_gate:
        gate_state = "允许入场" if visible_decision_gate.get("entry_allowed") else "阻断"
        gate_reasons = format_visible_decision_reasons(visible_decision_gate) or "无硬否决"
        lines.append(
            f"- 统一入场闸门：{gate_state}；原因：{gate_reasons}；"
            "闸门只限制买入/加仓，卖出、止损和入场取消继续执行。"
        )
        quote_validation = visible_decision_gate.get("quote_validation")
        if isinstance(quote_validation, dict):
            quote_status = str(
                quote_validation.get("status") or "unavailable"
            ).strip().lower()
            quote_as_of = quote_validation.get("as_of") or "无有效时间"
            if quote_validation.get("enabled") is not True:
                lines.append(
                    "- 易淘金行情校验：未启用（quote_status=not_enabled）；"
                    "当前报告沿用原有行情链路，未声称已完成易淘金核价。"
                )
            elif quote_status == "ok":
                lines.append(
                    "- 易淘金行情校验：当前有限范围快照有效"
                    f"（quote_status=ok；截至 {quote_as_of}）。"
                )
            elif quote_status == "blocked":
                lines.append(
                    "- 易淘金行情校验：存在过期、冲突、缺失或交易状态阻断"
                    f"（quote_status=blocked；截至 {quote_as_of}）；"
                    "新开仓关闭，持仓风险动作保留并需人工核价。"
                )
            else:
                lines.append(
                    "- 易淘金行情校验：读取不可用"
                    f"（quote_status=unavailable；截至 {quote_as_of}）；"
                    "新开仓关闭，持仓风险动作保留并需人工核价。"
                )
    if quote_data_alarms:
        alarm_text = "；".join(
            f"{item['label']} {item['alarm']}，必须人工核验止损 {_money(item['stop_loss'])}"
            for item in quote_data_alarms[:3]
        )
        lines.append(f"- 行情数据报警：{alarm_text}；核验前禁止补仓/抢反弹。")
    if sentinel_package:
        status = (sentinel_package.get("source_status") or {}).get("status", "unknown")
        themes = sentinel_package.get("top_themes") or []
        theme_text = "、".join(
            f"{item.get('name')}({item.get('count')})" for item in themes[:3] if isinstance(item, dict)
        ) or "无明确主题"
        age_days = _sentinel_package_age_days(sentinel_package, report_date)
        dives = sentinel_package.get("serenity_deep_dives") or []
        if age_days is not None and age_days > 2:
            lines.append(
                f"- Sentinel：研究包日期 {sentinel_package.get('date')}，已过期 {age_days} 天；"
                "只保留历史复盘，不作为当前候选证据。"
            )
            lines.append("- Serenity：旧深挖不参与当前候选判断；等待20:00研究任务刷新。")
            lines.append("")
            return lines
        dive_names = []
        for dive in dives[:3]:
            path = str(dive.get("learning_report_path") or "")
            name = os.path.basename(path) if path else str(dive.get("theme") or "未记录路径")
            dive_names.append(name)
        lines.append(
            f"- Sentinel：状态 {status}；抓取 {sentinel_package.get('event_count', 0)} 条，关键 {sentinel_package.get('key_event_count', 0)} 条；主题 {theme_text}。"
        )
        lines.append(f"- Serenity：研究 {len(dives)} 个主题；" + ("、".join(dive_names) if dive_names else "本次无深挖文件。"))
    else:
        lines.append("- Sentinel/Serenity：本次无可用研究包；主报告只使用行情、持仓和结构化评分。")
    lines.append("")
    return lines


def _holding_strategy_section(
    *,
    positions: list[dict],
    total_assets: float,
    decision: dict,
    target_date: str | None = None,
) -> list[str]:
    lines = [
        "## 二、明日持仓策略",
        "",
    ]
    if not positions:
        return lines + [
            "- 当前无持仓：明天没有卖出动作；只按短线池触发条件人工复核。",
            "",
        ]

    watch_items = _position_watch_items(decision)
    lines.extend([
        "| 持仓 | 现价/成本 | 止损 | 止盈 | 明日动作 | 触发条件 |",
        "|---|---:|---:|---:|---|---|",
    ])
    for pos in positions:
        shares = int(pos.get("shares", pos.get("position", 0)) or 0)
        price = float(pos.get("current_price", 0) or 0)
        cost = pos.get("avg_cost") or pos.get("cost_price") or pos.get("average_cost")
        value = float(pos.get("current_value", shares * price) or 0)
        ratio = value / total_assets * 100 if total_assets else 0
        watch = _watch_for_position(pos, watch_items)
        stop_loss = watch.get("stop_loss_price") or watch.get("stop_loss")
        target_price = watch.get("target_price") or watch.get("take_profit_price")
        try:
            risk_reference_price = float(cost or 0) or price
        except (TypeError, ValueError):
            risk_reference_price = price
        if not stop_loss and risk_reference_price:
            stop_loss = calculate_stop_loss_price(risk_reference_price)
        if not target_price and risk_reference_price:
            target_price = calculate_target_price(risk_reference_price)
        action = "持有观察"
        trigger = f"跌破 {_money(stop_loss)} 卖；接近 {_money(target_price)} 止盈；不加仓。"
        quote_alarm = _holding_quote_alarm(pos, target_date)
        quote_freshness = _quote_freshness_for_service_date(pos, target_date)
        has_explicit_quote_state = any(
            key in pos
            for key in ("quote_status", "quote_freshness", "freshness", "quote_source", "quote_timestamp")
        )
        possible_stop_breach = False
        try:
            possible_stop_breach = price and stop_loss and price <= float(stop_loss)
            if quote_alarm and (has_explicit_quote_state or possible_stop_breach):
                action = "人工核验止损"
                trigger = f"{quote_alarm}，必须人工核验止损；禁止补仓/抢反弹。"
            elif possible_stop_breach:
                action = "立即退出止损"
                trigger = (
                    f"新鲜行情已跌破 {_money(stop_loss)}，立即卖出{shares}股/退出；"
                    "禁止补仓/抢反弹。"
                )
            elif price and target_price and price >= float(target_price):
                action = "止盈优先"
                trigger = f"接近/突破 {_money(target_price)}，卖出或至少减仓。"
        except (TypeError, ValueError):
            pass
        if ratio >= 40:
            trigger += " 仓位偏高，不加仓。"
        price_display = "待核验" if quote_alarm and (has_explicit_quote_state or possible_stop_breach) else _money(price)
        if quote_freshness == "fresh_close" and price_display != "待核验":
            price_display = f"有效收盘价 {_money(price)}"
        lines.append(
            f"| {_cell(_target_label(pos), 32)} | {price_display} / {_money(cost)} | {_money(stop_loss)} | "
            f"{_money(target_price)} | {action} | {_cell(trigger, 96)} |"
        )
    lines.append("")
    return lines


def _short_pool_rows(decision: dict, *, excluded_codes: set[str] | None = None) -> list[dict]:
    excluded = excluded_codes or set()
    rows = []
    seen: set[str] = set()
    for item in _split_target_scores(decision)["executable"] + _split_target_scores(decision)["watching"]:
        if item.get("thesis_status") or item.get("long_quality_score"):
            continue
        code = _target_code(item)
        if not code or code in excluded or code in seen:
            continue
        rows.append(item)
        seen.add(code)
    outside_rows = _affordable_outside_targets(decision, include_research=True)
    outside_rows.extend(
        item
        for item in _outside_pool_scan(decision)
        if item.get("entry_allowed") is False
        and str(item.get("action") or item.get("status") or "").lower() == "watching"
        and item not in outside_rows
    )
    for item in outside_rows:
        code = _target_code(item)
        if not code or code in excluded or code in seen:
            continue
        rows.append(item)
        seen.add(code)
    return rows


def _short_pool_section(decision: dict, *, excluded_codes: set[str] | None = None) -> list[str]:
    rows = _short_pool_rows(decision, excluded_codes=excluded_codes)
    lines = [
        "## 三、短线关注标的池",
        "",
        "| 标的 | 状态 | 现价 | 触发价格 | 止损 | 止盈 | 入选原因 | 重点 |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    if not rows:
        lines.append("| 暂无 | 不下单 | - | - | - | - | 无短线可执行候选 | - |")
        lines.append("")
        return lines
    for idx, item in enumerate(rows[:6]):
        reason = _humanize_reason(item.get("decision_reason") or item.get("watch_reason") or item.get("reason"))
        focus = "重点关注" if idx < 3 else "一般关注"
        action = str(item.get("action") or item.get("status") or "")
        research_only = item.get("research_only") is True
        if research_only:
            state = "研究候选（未晋级，不买）"
            trigger_price = stop_loss = target_price = "待完整评分"
        else:
            state = "可执行" if action in {"buy", "add", "increase"} else "等待触发（未触发不买）"
            trigger_price = _money(_trigger_from_item(item))
            stop_loss = _money(_stop_from_item(item))
            target_price = _money(_target_from_item(item))
        lines.append(
            f"| {_cell(_target_label(item), 28)} | {state} | {_money(_price_from_item(item))} | {trigger_price} | "
            f"{stop_loss} | {target_price} | {_cell(reason or '等待量价资金触发', 60)} | {focus} |"
        )
    lines.append("")
    return lines


def _long_pool_rows(decision: dict) -> list[dict]:
    long_rows = []
    for item in _target_scores(decision):
        if item.get("thesis_status") or item.get("long_quality_score") or str(item.get("action") or "") in {"research_only", "research_reference"}:
            long_rows.append(item)
    return long_rows


def _long_pool_section(decision: dict, *, budget_blocked_count: int = 0) -> list[str]:
    rows = _long_pool_rows(decision)
    lines = [
        "## 四、中长线关注标的池",
        "",
        "| 标的 | 现价 | 触发价格 | 止损 | 止盈 | 发展趋势 | 计划持有周期 | 入选原因 |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    if not rows:
        if budget_blocked_count:
            lines.append(
                f"| 暂无 | - | - | - | - | 中长线研究不是没有；研究层仍有 {budget_blocked_count} 只预算阻断标的 | - | 明细在 Obsidian，不作为当前账户策略 |"
            )
        else:
            lines.append("| 暂无 | - | - | - | - | Serenity/长线研究未形成可跟踪配置 | - | 只在 Obsidian 查看研究细节 |")
        lines.append("")
        return lines
    for item in rows[:6]:
        reason = _humanize_reason(
            item.get("combined_decision_reason")
            or item.get("decision_reason")
            or item.get("long_horizon_reason")
            or item.get("reason")
        )
        lines.append(
            f"| {_cell(_target_label(item), 28)} | {_money(_price_from_item(item))} | {_money(_trigger_from_item(item))} | "
            f"{_money(_stop_from_item(item))} | {_money(_target_from_item(item))} | {_cell(_trend_text(item), 36)} | "
            f"{_planned_holding_period(item)} | {_cell(reason or '等待趋势和估值复核', 60)} |"
        )
    lines.append("")
    return lines


def _new_entry_action_lines(decision: dict) -> list[str]:
    buy = _first_executable_target(decision)
    if not buy:
        outside_scan = _outside_pool_scan(decision)
        actionable_scan = [
            item for item in outside_scan
            if item.get("affordable") is not False
            and (item.get("suggested_amount") or item.get("lot_value"))
            and not item.get("research_only")
            and item.get("entry_allowed") is not False
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
        f"- 本次动作性质：{_action_nature(buy)}。",
        f"- 候选标的：{label}。",
        f"- 建议试仓金额：{amount}，不得超过报告给出的单票预算。",
        f"- 触发价：{entry} 附近或触发价内，不追高。",
        f"- 止损/目标：{stop_loss} 硬止损；目标位 {target}。",
        f"- 依据：{reason}",
        "- 盘前复核信号：若未成交，继续等放量延续、回踩不破触发位、个股资金流未转弱。",
    ]


def _research_archive_lines(
    sentinel_package: dict | None,
    roles: dict,
    decision: dict,
    report_date: str | None = None,
) -> list[str]:
    lines = [
        "- 完整辩论记录：保留在本地辩论/报告归档，主报告只展示可执行结论。",
        "- Sentinel 归一数据包：保留高频新闻、主题热度、风险事件和证据编号。",
        "- Serenity 深挖：保留产业链瓶颈、候选锚点和验证问题；进入策略前必须再过账户与行情评分。",
    ]
    if sentinel_package:
        age_days = _sentinel_package_age_days(sentinel_package, report_date)
        if age_days is not None and age_days > 2:
            lines.append(
                f"- Serenity 深挖归档：最新研究包已过期 {age_days} 天；"
                "旧文件只作历史复盘，不参与当前候选判断。"
            )
            return lines
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


def _thesis_status_label(status: str) -> str:
    return {
        "healthy": "论文成立",
        "weakened": "边际弱化",
        "broken": "红线触发",
        "stale": "论文过期",
    }.get(str(status or ""), "未建论文")


def _valuation_zone_label(zone: str) -> str:
    return {
        "accumulation_zone": "积累区",
        "fair_zone": "合理区",
        "above_fair_zone": "偏贵区",
        "overpriced_zone": "高估区",
        "unknown": "待估值",
    }.get(str(zone or ""), "待估值")


def _action_nature(item: dict) -> str:
    action = str(item.get("action") or item.get("status") or "")
    thesis_status = str(item.get("thesis_status") or "")
    valuation_zone = str(item.get("valuation_zone") or "")
    block_reason = str(item.get("block_reason") or "")
    if thesis_status == "broken" or block_reason == "long_thesis_broken":
        return "风险退出"
    if action in {"add", "increase"}:
        return "长期加仓"
    if action == "buy" and thesis_status == "healthy" and valuation_zone == "accumulation_zone":
        return "长期建仓"
    if action == "buy":
        return "短线试错"
    return "观察复核"


def _render_long_horizon_summary(rows: list[dict]) -> list[str]:
    long_rows = [
        item for item in rows
        if item.get("thesis_status") or item.get("long_quality_score") or item.get("red_line_status")
    ]
    if not long_rows:
        return []
    lines = [
        "### 长期依据摘要",
        "",
        "- 这里只解释中长期 thesis 状态，不单独触发交易动作；可执行动作仍以第一屏和账户风控为准。",
        "",
        "| 标的 | 论文状态 | 估值区间 | 长期分 | 红线 | 本次动作性质 | 下一步 |",
        "|---|---|---|---:|---|---|---|",
    ]
    for item in long_rows[:8]:
        next_step = _humanize_reason(
            item.get("combined_decision_reason")
            or item.get("decision_reason")
            or item.get("long_horizon_reason")
            or "等待 thesis review 或交易触发信号。"
        )
        lines.append(
            f"| {_cell(_target_label(item), 40)} | {_thesis_status_label(item.get('thesis_status'))} | "
            f"{_valuation_zone_label(item.get('valuation_zone'))} | {item.get('long_quality_score', 0)} | "
            f"{'触发' if item.get('red_line_status') == 'triggered' else '未触发'} | "
            f"{_action_nature(item)} | {_cell(next_step, 140)} |"
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
    portfolio_truth: dict | None = None,
    visible_decision_gate: dict | None = None,
) -> list[str]:
    """Build the concise Feishu-first next-day strategy sections."""
    profile = strategy_profile or get_strategy_profile()
    total_assets = total_assets or available_cash
    buy_budget, _ = _buy_budget_for_profile(available_cash, total_assets, profile)
    hidden_codes = _hidden_budget_codes(decision, buy_budget)
    budget_visible_decision = dict(decision)
    budget_visible_decision["target_scores"] = _visible_target_scores(decision, hidden_codes)
    budget_visible_decision["outside_pool_scan"] = [
        item for item in _outside_pool_scan(decision)
        if _target_code(item) not in hidden_codes
    ]
    stop_breach_alerts = _holding_stop_breaches(positions, budget_visible_decision, target_date)
    quote_data_alarms = _holding_quote_alarms(positions, budget_visible_decision, target_date)
    gate_decision = dict(decision)
    gate_decision.setdefault("final_view", final_view)
    gate = visible_decision_gate or build_visible_decision_gate(
        report_date=report_date,
        target_date=target_date,
        decision=gate_decision,
        portfolio_truth=portfolio_truth,
        stop_breaches=stop_breach_alerts,
    )
    visible_decision = apply_visible_decision_gate(budget_visible_decision, gate)
    lines: list[str] = []
    lines.extend(_project_status_section(
        report_date=report_date,
        target_date=target_date,
        risk_level=risk_level,
        final_view=final_view,
        confidence=confidence,
        positions=positions,
        available_cash=available_cash,
        total_assets=total_assets,
        market_data=market_data,
        analysis_report=analysis_report,
        sentinel_package=sentinel_package,
        profile=profile,
        budget_blocked_count=len(hidden_codes),
        stop_breach_alerts=stop_breach_alerts,
        quote_data_alarms=quote_data_alarms,
        visible_decision_gate=gate,
    ))
    lines.extend(_holding_strategy_section(
        positions=positions,
        total_assets=total_assets,
        decision=visible_decision,
        target_date=target_date,
    ))
    holding_codes = {_target_code(item) for item in positions if _target_code(item)}
    lines.extend(_short_pool_section(visible_decision, excluded_codes=holding_codes))
    lines.extend(_long_pool_section(visible_decision, budget_blocked_count=len(hidden_codes)))
    lines.append("- 说明：Sentinel/Serenity 的原始抓取、深挖全文和角色辩论细节保留在 Obsidian，不在飞书主报告展开。")
    lines.append("")
    target_scores = _target_scores(visible_decision)
    target_buckets = _split_target_scores(visible_decision)
    lines.extend([
        FEISHU_SUMMARY_END_MARKER,
        "",
        "## 五、后台风控与策略审计",
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
    lines.extend([
        "## 六、数据覆盖与评分审计",
        "",
        "- 数据源审计：",
    ])
    lines.extend(
        f"  {line}"
        for line in build_data_source_audit(
            market_data=market_data,
            sentinel_package=sentinel_package,
        )
    )
    if target_scores:
        lines.extend([
            "",
            "### 标的评分",
            "",
            "| 标的 | 分数 | 动作 | 数据缺口 | 阻断原因 |",
            "|---|---:|---|---|---|",
        ])
        for item in target_scores:
            lines.append(
                f"| {_cell(_target_label(item), 40)} | {item.get('score', 0)} | "
                f"{_action_label(_effective_target_action(item))} | "
                f"{_cell(_missing_data_text(item.get('missing_data') or []), 80)} | "
                f"{_cell(_block_reason_label(item.get('block_reason')), 80)} |"
            )
    else:
        lines.extend([
            "",
            "- 标的评分：本次未生成结构化 target_scores；不能把研究线索直接当成买入建议。",
        ])
    lines.extend(["", *build_role_vote_audit(decision, hidden_codes=hidden_codes), ""])
    review_summary = _structured_review_summary(target_buckets, visible_decision)
    if hidden_codes and not target_scores:
        review_summary = (
            f"结构化评分摘要：飞书可见标的 0 只；预算阻断 {len(hidden_codes)} 只。"
            "AI裁判原文保留在本地辩论快照；主报告不展示买不起标的或其幻觉价格。"
        )
    lines.extend([
        "## 七、复盘与自迭代",
        "",
        f"- 本报告生成日：{report_date}",
        f"- 裁判采用/否决说明：{review_summary}",
        "- 可执行标的必须留存触发价、止损、目标位、账户预算和证据编号。",
        "- 观察标的按 1/3/5/20 日回看；符合预期才允许迁移到可执行池。",
        "",
        "### 研究归档",
        "",
    ])
    lines.extend(_research_archive_lines(sentinel_package, roles, decision, report_date))
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
    if os.getenv("CONGXI_DISABLE_FEISHU_PUSH", "0") == "1":
        return {
            "feishu_api": False,
            "feishu_webhook": False,
            "disabled": True,
            "error": "Feishu push disabled for local reconciliation run",
        }
    try:
        from app.config import settings
        from app.services.feishu_pusher import send_feishu_card

        result = await send_feishu_card(
            title=title,
            content=build_feishu_summary(md_content),
            webhook_url=os.environ.get("FEISHU_WEBHOOK_URL") or settings.FEISHU_WEBHOOK_URL,
            app_id=settings.FEISHU_APP_ID,
            app_secret=settings.FEISHU_APP_SECRET,
            chat_id=settings.FEISHU_CHAT_ID,
            api_base=settings.FEISHU_API_BASE,
        )
        if result.get("feishu_api") or result.get("feishu_webhook"):
            return result
        return {**result, "error": result.get("error") or "飞书推送失败"}
    except Exception as e:
        return {"feishu_api": False, "feishu_webhook": False, "error": f"飞书推送异常: {e}"}


async def build_target_scores_for_report(
    *,
    available_cash: float,
    total_assets: float,
    limit: int | None = None,
    market_source=None,
) -> list[dict]:
    """Score current target-pool items with normalized data snapshots."""
    from app.ai.serenity_financial_evidence import fetch_financial_evidence
    from app.data_sources.akshare_market import AKShareMarketClient
    from app.data_sources.akshare_news import AKShareNewsClient
    from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource
    from app.services.quant_lifecycle import TargetPoolStore, target_production_eligibility
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
        if isinstance(item, dict)
        and item.get("status") not in {"removed", "expired", "cooldown_after_loss"}
    ]
    status_priority = {
        "executable": 0,
        "watching": 1,
        "blocked_chasing": 2,
        "research_reference": 3,
    }
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    items.sort(key=lambda item: status_priority.get(str(item.get("status") or "watching"), 2))
    max_items = limit or int(os.getenv("CONGXI_TARGET_SCORE_LIMIT", "12"))
    items = items[:max_items]
    if not items:
        return []

    quote_source = FastRealtimeMarketDataSource()
    resolved_market_source = market_source or CachedMarketSource()
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
            market_source=resolved_market_source,
            news_source=news_source,
            financial_fetcher=cached_financial_fetcher,
            sentinel=item.get("sentinel") or {},
            serenity=item.get("serenity") or {},
        )
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        snapshot["trigger_price"] = next(
            (
                value
                for candidate in (item.get("trigger_price"), evidence.get("trigger_price"))
                if (value := _positive_float(candidate)) is not None
            ),
            None,
        )
        snapshot["production_eligibility"] = target_production_eligibility(item)
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
            evidence=evidence,
            evidence_ids=item.get("evidence_ids") or [],
            sentinel=item.get("sentinel") or {},
            serenity=item.get("serenity") or {},
            scoring_decision={
                "action": action,
                "score": score.get("score", 0),
                "block_reason": score.get("block_reason", ""),
                "decision_reason": score.get("decision_reason", ""),
                "missing_data": score.get("missing_data") or [],
                "playbook": score.get("playbook", "watch"),
                "source_status": score.get("source_status") or {},
                "evaluated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            current_price=quote.get("price"),
            available_cash=available_cash,
            total_assets=total_assets,
        )
        scores.append(score)
    return sorted(scores, key=lambda row: float(row.get("score", 0) or 0), reverse=True)


def collect_outside_pool_exclusions(
    target_scores: list[dict] | None,
    *,
    store=None,
) -> set[str]:
    """Protect every lifecycle item, including removed names, from outside-pool re-entry."""
    from app.services.quant_lifecycle import TargetPoolStore

    target_pool = store or TargetPoolStore()
    pool_items = target_pool.load().get("items", {})
    dynamic_codes = {
        str(code).strip()
        for code, item in pool_items.items()
        if isinstance(item, dict)
        and str(
            ((item.get("provenance") or {}).get("original_source") if isinstance(item.get("provenance"), dict) else "")
            or item.get("source")
            or ""
        ) == "dynamic_fund_flow_discovery"
    }
    codes = {
        str(code).strip()
        for code in pool_items
        if str(code).strip() and str(code).strip() not in dynamic_codes
    }
    codes.update(
        str(item.get("code") or "").strip()
        for item in target_scores or []
        if isinstance(item, dict)
        and str(item.get("code") or "").strip()
        and str(item.get("code") or "").strip() not in dynamic_codes
    )
    return codes


async def discover_small_account_candidates_for_report(
    *,
    available_cash: float,
    total_assets: float,
    existing_codes: set[str] | None = None,
    market_source=None,
) -> list[dict]:
    """Prefer a rotating live-market universe, with static seeds as a degraded fallback."""
    from app.data_sources.akshare_market import AKShareMarketClient
    from app.services.small_account_discovery import (
        build_dynamic_small_account_candidates,
        build_small_account_seed_candidates,
    )

    source = market_source or AKShareMarketClient()
    market_rows = await source.fetch_fund_flow_individual()
    dynamic_rows = build_dynamic_small_account_candidates(
        market_rows=market_rows,
        available_cash=available_cash,
        total_assets=total_assets,
        existing_codes=existing_codes,
        max_candidates=12,
    )
    if dynamic_rows:
        return dynamic_rows
    return build_small_account_seed_candidates(
        available_cash=available_cash,
        total_assets=total_assets,
        existing_codes=existing_codes,
    )


async def build_outside_pool_scan_for_report(
    *,
    available_cash: float,
    total_assets: float,
    existing_codes: set[str] | None = None,
    seed_candidates: list[dict] | None = None,
) -> list[dict]:
    """Build a small-account outside-pool scan with live quote context."""
    from app.data_sources.tencent_client import TencentDataSource
    from app.services.small_account_discovery import build_small_account_seed_candidates

    seeds = seed_candidates if seed_candidates is not None else build_small_account_seed_candidates(
        available_cash=available_cash,
        total_assets=total_assets,
        existing_codes=existing_codes or set(),
    )
    if not seeds:
        return []
    profile = get_strategy_profile()
    single_pct = float(profile.get("single_position_limit_pct", 50) or 50)
    reserve_pct = float(profile.get("cash_reserve_pct", 10) or 10)
    reserve_cash = total_assets * reserve_pct / 100 if total_assets else 0
    executable_budget = round(
        max(0.0, min(available_cash - reserve_cash, total_assets * single_pct / 100 if total_assets else available_cash)),
        2,
    )
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
        if seed.get("research_only"):
            reason = str(seed.get("watch_reason") or reason)
        trigger_price = _round_price(price if price > 0 and affordable else seed.get("max_entry_price") or 0)
        stop_loss = calculate_stop_loss_price(trigger_price, profile) if trigger_price > 0 and affordable else None
        target_price = calculate_target_price(trigger_price, profile) if trigger_price > 0 and affordable else None
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
            "executable_budget": executable_budget,
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


async def build_refreshed_outside_pool_scan_for_report(
    *,
    available_cash: float,
    total_assets: float,
    existing_codes: set[str] | None = None,
    market_source=None,
) -> list[dict]:
    seeds = await discover_small_account_candidates_for_report(
        available_cash=available_cash,
        total_assets=total_assets,
        existing_codes=existing_codes,
        market_source=market_source,
    )
    return await build_outside_pool_scan_for_report(
        available_cash=available_cash,
        total_assets=total_assets,
        existing_codes=existing_codes,
        seed_candidates=seeds,
    )


def persist_outside_pool_scan_to_target_pool(
    rows: list[dict],
    *,
    available_cash: float,
    total_assets: float,
    store=None,
) -> int:
    """Promote affordable outside-pool watch names into the lifecycle pool."""
    from app.services.quant_lifecycle import TargetPoolStore

    target_pool = store or TargetPoolStore()
    upserted = 0
    for row in rows or []:
        if not row.get("affordable") or row.get("chasing_risk"):
            continue
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        ok = target_pool.upsert_target(
            code=code,
            name=row.get("name") or code,
            status="research_reference" if row.get("research_only") else "watching",
            source=row.get("source", "small_account_discovery"),
            evidence={
                "reason": row.get("watch_reason", ""),
                "trigger_price": row.get("trigger_price"),
                "stop_loss": row.get("stop_loss"),
                "target_price": row.get("target_price"),
                "theme": row.get("theme", ""),
                "source": row.get("source", "small_account_discovery"),
                "stage": "hypothesis" if row.get("research_only") else "watching",
                "market_evidence": row.get("market_evidence") or {},
            },
            current_price=row.get("current_price"),
            available_cash=available_cash,
            total_assets=total_assets,
        )
        if ok:
            upserted += 1
    return upserted


def rotate_dynamic_discovery_targets(
    selected_codes: set[str],
    *,
    store=None,
) -> int:
    """Expire prior dynamic hypotheses that disappeared from the latest market scan."""
    from app.services.quant_lifecycle import TargetPoolStore

    target_pool = store or TargetPoolStore()
    selected = {str(code).strip() for code in selected_codes if str(code).strip()}

    def expire_missing(payload: dict) -> int:
        expired = 0
        for code, item in payload.get("items", {}).items():
            if not isinstance(item, dict) or code in selected:
                continue
            provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
            origin = str(provenance.get("original_source") or item.get("source") or "")
            if origin != "dynamic_fund_flow_discovery" or item.get("status") in {"removed", "expired"}:
                continue
            item["status"] = "expired"
            item["watch_reason"] = "missing_from_latest_dynamic_scan"
            item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            expired += 1
        return expired

    return int(target_pool.update(expire_missing) or 0)


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

    from app.services.portfolio_store import merge_report_market_snapshot

    merge_report_market_snapshot(portfolio, portfolio_path)
    print("✅ 持仓行情已合并到最新账户真值", flush=True)

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
    from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource
    from app.engine.analysis import run_analysis
    from app.engine.workshop import run_debate
    from app.services.evidence_ledger import build_sentinel_evidence_context, upsert_sentinel_evidence_to_target_pool
    from app.services.portfolio_store import recalculate_portfolio, sync_db_from_user_portfolio

    now = datetime.now()
    report_date_override = _read_iso_date_env("CONGXI_REPORT_DATE")
    target_date_override = _read_iso_date_env("CONGXI_TARGET_DATE")
    report_date = report_date_override or now.date()
    today = report_date.isoformat()
    time_str = now.strftime('%H:%M')
    try:
        from app.services.schedule_policy import main_report_target_date

        target_date = main_report_target_date(report_date, target_date_override).isoformat()
    except Exception:
        target_date = today

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
    sync_truth = sync_portfolio_database_truth(
        portfolio,
        portfolio_path,
        sync_fn=sync_db_from_user_portfolio,
    )
    if not sync_truth["ok"]:
        print("   ⚠️ 持仓同步数据库失败，已禁止新开仓", flush=True)

    positions = portfolio.get("positions", [])
    closed = portfolio.get("closed_positions", [])

    # ===== 2. 获取行情 =====
    print("📊 获取实时行情...", flush=True)
    tc = FastRealtimeMarketDataSource()
    market_provider = str(getattr(tc, "name", "fast_realtime_market_data"))
    strategy_profile = get_strategy_profile()
    available_cash = float(portfolio.get("available_cash", portfolio.get("cash", 0)) or 0)
    market_data = {
        "indices": {},
        "market_source_status": {
            "status": "failed",
            "provider": "fast_realtime_market_data",
            "data_cutoff": None,
            "freshness_status": "failed",
            "error": "index_quotes_not_fetched",
        },
        "sectors": [],
        "holdings": [],
        "holdings_str": "空仓",
        "news": [],
        "available_cash": available_cash,
        "strategy_profile": strategy_profile,
        "portfolio_sync_failed": portfolio.get("portfolio_sync_failed") is True,
        "portfolio_sync_status": portfolio.get("portfolio_sync_status", "unknown"),
    }

    try:
        indices = await tc.fetch_batch(["sh000001", "sz399001", "sz399006"])
        sh = indices.get("sh000001", {})
        sz = indices.get("sz399001", {})
        cy = indices.get("sz399006", {})
        normalized_indices = {}
        cutoffs = []
        providers = set()
        observed_freshness = set()
        rejected_quote_truth = False
        for quote, price_key, change_key in (
            (sh, "shanghai", "sh_change"),
            (sz, "shenzhen", "sz_change"),
            (cy, "cyb", "cy_change"),
        ):
            observed_freshness.add(str(
                quote.get("freshness_status") or quote.get("freshness") or "unknown"
            ).strip().lower())
            quote_truth = _fresh_market_quote_truth(quote)
            if quote.get("price") and not quote_truth:
                rejected_quote_truth = True
            if not quote.get("price") or not quote_truth:
                continue
            cutoff, _freshness = quote_truth
            normalized_indices[price_key] = quote["price"]
            normalized_indices[change_key] = quote.get("change_pct", 0)
            providers.add(str(quote.get("source") or market_provider))
            cutoffs.append(cutoff)
        if normalized_indices and rejected_quote_truth:
            source_status = "degraded"
            freshness_status = "degraded"
            source_error = "partial_or_unfresh_market_indices"
        elif normalized_indices:
            source_status = "ok"
            freshness_status = "fresh" if observed_freshness == {"fresh"} else "ok"
            source_error = ""
        elif len(observed_freshness) == 1:
            source_status = "failed"
            freshness_status = next(iter(observed_freshness))
            source_error = "fresh_market_indices_unavailable"
        else:
            source_status = "failed"
            freshness_status = "failed"
            source_error = "fresh_market_indices_unavailable"
        market_data["indices"] = normalized_indices
        market_data["market_source_status"] = {
            "status": source_status,
            "provider": "+".join(sorted(providers)) if normalized_indices else market_provider,
            "data_cutoff": min(cutoffs) if cutoffs else None,
            "freshness_status": freshness_status,
            "error": source_error,
        }
        print(f"   上证: {sh.get('price','?')} ({sh.get('change_pct',0):+.2f}%) | "
              f"深证: {sz.get('price','?')} ({sz.get('change_pct',0):+.2f}%)", flush=True)
    except Exception as e:
        market_data["indices"] = {}
        market_data["market_source_status"] = {
            "status": "failed",
            "provider": market_provider,
            "data_cutoff": None,
            "freshness_status": "failed",
            "error": "fresh_market_indices_unavailable",
        }
        print(f"   ⚠️ 指数获取失败: {e}", flush=True)

    if positions:
        codes = [p["code"] for p in positions]
        formatted = [f"sh{c}" if c.startswith("6") else f"sz{c}" for c in codes]
        try:
            quotes = await tc.fetch_batch(formatted)
            for p in positions:
                q = quotes.get(formatted[codes.index(p["code"])], {})
                _apply_position_quote(p, q, service_date=target_date)
        except Exception as e:
            for p in positions:
                p["quote_status"] = "fetch_failed"
                p["quote_freshness"] = "unknown"
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
    from app.data_sources.akshare_market import AKShareMarketClient

    shared_market_source = AKShareMarketClient()
    try:
        target_scores = await build_target_scores_for_report(
            available_cash=available_cash,
            total_assets=portfolio.get("total_assets", portfolio.get("total_value", 0) + available_cash),
            market_source=shared_market_source,
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
        existing_codes = collect_outside_pool_exclusions(decision.get("target_scores", []))
        outside_scan = await build_refreshed_outside_pool_scan_for_report(
            available_cash=available_cash,
            total_assets=portfolio.get("total_assets", portfolio.get("total_value", 0) + available_cash),
            existing_codes=existing_codes,
            market_source=shared_market_source,
        )
        decision["outside_pool_scan"] = outside_scan
        promoted = persist_outside_pool_scan_to_target_pool(
            outside_scan,
            available_cash=available_cash,
            total_assets=portfolio.get("total_assets", portfolio.get("total_value", 0) + available_cash),
        )
        dynamic_codes = {
            str(row.get("code") or "").strip()
            for row in outside_scan
            if row.get("source") == "dynamic_fund_flow_discovery"
        }
        expired = rotate_dynamic_discovery_targets(dynamic_codes) if dynamic_codes else 0
        print(
            f"   池外补扫完成: {len(outside_scan)} 个候选, {promoted} 个入池预警, {expired} 个旧动态候选过期",
            flush=True,
        )
    except Exception as e:
        print(f"   ⚠️ 池外补扫失败，报告降级继续: {e}", flush=True)

    try:
        from app.services.quant_lifecycle import PositionWatchStore

        decision["position_watch"] = PositionWatchStore().load()
    except Exception as e:
        print(f"   ⚠️ 持仓止损/止盈计划读取失败，报告降级继续: {e}", flush=True)

    stop_breaches = _holding_stop_breaches(positions, decision, target_date)
    gate_decision = dict(decision)
    gate_decision.setdefault("final_view", final_view)
    quote_validation = _load_yitaojin_quote_validation_for_decision(
        decision
    )
    visible_gate = build_visible_decision_gate(
        report_date=today,
        target_date=target_date,
        decision=gate_decision,
        portfolio_truth=portfolio,
        stop_breaches=stop_breaches,
        quote_validation=quote_validation,
    )

    # ===== 4. 构建综合Markdown报告 =====
    lines = []
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
        portfolio_truth=portfolio,
        visible_decision_gate=visible_gate,
    ))
    gate_path = write_visible_decision_gate(visible_gate)
    print(f"✅ 统一入场闸门已保存: {gate_path}", flush=True)

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
