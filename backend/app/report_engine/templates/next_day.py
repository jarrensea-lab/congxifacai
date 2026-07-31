"""Pure next-day strategy report renderer.

The caller owns all data access, freshness checks, account gating and enum
translation.  This module only turns an explicit view model into Markdown.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


FEISHU_SUMMARY_END_MARKER = "<!-- FEISHU_SUMMARY_END -->"


def _clean(value: Any, *, limit: int = 180) -> str:
    text = str(value or "—").replace("\n", " ").replace("|", "/").strip()
    return text[:limit] if len(text) > limit else text


def _extend_text_lines(lines: list[str], values: Any) -> None:
    if not isinstance(values, list):
        return
    lines.extend(str(value) for value in values if isinstance(value, str))


def _render_holdings(view: Mapping[str, Any]) -> list[str]:
    rows = view.get("holdings")
    holdings = [item for item in rows if isinstance(item, Mapping)] if isinstance(rows, list) else []
    lines = ["### 持仓处理", ""]
    if not holdings:
        return lines + [
            "- 当前无持仓：当前0股，精确卖出0股；卖出监控保持空转。",
            "",
        ]

    lines.extend([
        "| 持仓 | 当前股数 | 现价/成本 | 止损 | 目标 | 明日动作 | 精确卖出数量 | 下一信号 |",
        "|---|---:|---:|---:|---:|---|---:|---|",
    ])
    for item in holdings:
        shares = int(item.get("shares") or 0)
        sell_quantity = max(0, min(shares, int(item.get("sell_quantity") or 0)))
        lines.append(
            f"| {_clean(item.get('label'), limit=40)} | 当前{shares}股 | "
            f"{_clean(item.get('price_text'), limit=30)} / {_clean(item.get('cost_text'), limit=30)} | "
            f"{_clean(item.get('stop_text'), limit=30)} | {_clean(item.get('target_text'), limit=30)} | "
            f"{_clean(item.get('action'), limit=40)} | 精确卖出{sell_quantity}股 | "
            f"{_clean(item.get('next_signal'), limit=140)} |"
        )
    lines.append("")
    return lines


def _render_candidates(view: Mapping[str, Any]) -> list[str]:
    rows = view.get("candidates")
    candidates = [item for item in rows if isinstance(item, Mapping)][:3] if isinstance(rows, list) else []
    lines = ["### 新开仓机会（主选1只，备选最多2只）", ""]
    if not candidates:
        missing = _clean(
            view.get("candidate_missing_reason")
            or "暂无完整评分候选；缺少同时通过的价格触发、量能、个股资金流、账户预算或硬风控信号。",
            limit=240,
        )
        return lines + [
            "- 新开仓结论：不下单。",
            f"- 当前阻断条件：{missing}",
            "",
        ]

    lines.extend([
        "| 级别 | 标的 | 动作 | 建议金额/股数 | 触发/入场 | 止损 | 目标 | 阻断/闸门 | 下一信号 |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ])
    for index, item in enumerate(candidates):
        level = "主选" if index == 0 else f"备选{index}"
        lines.append(
            f"| {level} | {_clean(item.get('label'), limit=40)} | "
            f"{_clean(item.get('action'), limit=36)} | "
            f"{_clean(item.get('amount_shares_text'), limit=50)} | "
            f"{_clean(item.get('entry_text'), limit=30)} | "
            f"{_clean(item.get('stop_text'), limit=30)} | "
            f"{_clean(item.get('target_text'), limit=30)} | "
            f"{_clean(item.get('gate_reason'), limit=100)} | "
            f"{_clean(item.get('next_signal'), limit=140)} |"
        )
    lines.append("")
    return lines


def _render_long_horizon(view: Mapping[str, Any]) -> list[str]:
    rows = view.get("long_horizon")
    long_rows = [item for item in rows if isinstance(item, Mapping)] if isinstance(rows, list) else []
    lines = [
        "## 二、中长期论文状态",
        "",
        "- 这里只展示已建立的长期论文状态，不单独触发交易动作；预算不足本身不构成中长期研究结论。",
        "",
    ]
    if not long_rows:
        return lines + [
            "- 当前没有形成可跟踪的中长期论文；等待 Serenity 研究、财务证据和红线条件形成可验证闭环。",
            "",
        ]

    lines.extend([
        "| 标的 | 论文状态 | 估值区间 | 长期分 | 红线 | 本次动作性质 | 下一步 |",
        "|---|---|---|---:|---|---|---|",
    ])
    for item in long_rows[:8]:
        lines.append(
            f"| {_clean(item.get('label'), limit=40)} | "
            f"{_clean(item.get('thesis_status'), limit=30)} | "
            f"{_clean(item.get('valuation_zone'), limit=30)} | "
            f"{_clean(item.get('long_quality_score'), limit=20)} | "
            f"{_clean(item.get('red_line_status'), limit=30)} | "
            f"{_clean(item.get('action_nature'), limit=32)} | "
            f"{_clean(item.get('next_signal'), limit=160)} |"
        )
    lines.append("")
    return lines


def _render_research_appendix(view: Mapping[str, Any]) -> list[str]:
    rows = view.get("research_reference")
    research_rows = [item for item in rows if isinstance(item, Mapping)] if isinstance(rows, list) else []
    budget_rows_value = view.get("budget_blocked")
    budget_rows = (
        [item for item in budget_rows_value if isinstance(item, Mapping)]
        if isinstance(budget_rows_value, list)
        else []
    )
    budget_count = int(view.get("budget_blocked_count") or len(budget_rows))
    lines = [
        "## 三、研究参照与预算阻断（附录）",
        "",
        (
            f"- 预算阻断 {budget_count} 只：仅确认其未进入本报告可执行候选；"
            "上游是否进入模型分析以当次路由审计为准，本报告不作未验证推断。"
            if budget_count
            else "- 当前没有账户预算阻断项。"
        ),
    ]
    combined: list[tuple[str, Mapping[str, Any]]] = [
        ("研究参照", item) for item in research_rows
    ]
    if combined:
        lines.extend([
            "",
            "| 类型 | 标的 | 一手门槛 | 结论 | 迁移条件 |",
            "|---|---|---:|---|---|",
        ])
        for kind, item in combined[:12]:
            lines.append(
                f"| {kind} | {_clean(item.get('label'), limit=40)} | "
                f"{_clean(item.get('lot_value_text'), limit=30)} | "
                f"{_clean(item.get('reason'), limit=100)} | "
                f"{_clean(item.get('next_signal'), limit=160)} |"
            )
    else:
        lines.extend(["", "- 无研究参照项。"])
    lines.append("")
    return lines


def render_next_day_sections(view: Mapping[str, Any]) -> list[str]:
    """Render a concise Feishu-first next-day report from an explicit view model."""
    lines: list[str] = [
        "## 一、当前账户动作",
        "",
        f"- 服务交易日：{_clean(view.get('target_date'), limit=20)}",
    ]
    hard_risks = view.get("hard_risk_lines")
    _extend_text_lines(lines, hard_risks)
    if isinstance(hard_risks, list) and hard_risks:
        lines.append("")
    lines.extend(_render_holdings(view))
    lines.extend(_render_candidates(view))
    lines.extend(_render_long_horizon(view))
    lines.extend([
        "- 说明：研究原文、角色辩论和完整数据审计位于附录，不占用飞书首屏。",
        "",
        FEISHU_SUMMARY_END_MARKER,
        "",
    ])
    lines.extend(_render_research_appendix(view))
    lines.extend([
        "## 四、系统、数据与模型审计",
        "",
    ])
    _extend_text_lines(lines, view.get("audit_lines"))
    lines.append("")
    _extend_text_lines(lines, view.get("score_audit_lines"))
    if view.get("score_audit_lines"):
        lines.append("")
    lines.extend([
        "## 五、复盘与自迭代",
        "",
    ])
    _extend_text_lines(lines, view.get("review_lines"))
    lines.append("")
    return lines
