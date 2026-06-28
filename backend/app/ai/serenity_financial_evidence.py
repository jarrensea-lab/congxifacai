"""Financial evidence helpers for Serenity bottleneck research."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DEFAULT_FINANCIAL_EVIDENCE_CACHE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "serenity" / "financial_evidence.json"
)


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "未知"
    return f"{number:+.2f}%"


def build_financial_evidence(
    candidate: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Convert a financial snapshot into a Serenity evidence item."""
    if not snapshot or snapshot.get("status") == "unavailable":
        return {
            "fact": (
                f"{candidate.get('name', '')}({candidate.get('code', '')})财务证据暂不可用；"
                "需要补充财报接口权限、AKShare 依赖或本地证据缓存。"
            ),
            "strength": "weak",
            "source": "财务证据不可用",
            "metrics": {},
        }

    metrics = {
        "report_period": snapshot.get("report_period", ""),
        "revenue_yoy_pct": _to_float(snapshot.get("revenue_yoy_pct")),
        "gross_margin_pct": _to_float(snapshot.get("gross_margin_pct")),
        "gross_margin_yoy_pct": _to_float(snapshot.get("gross_margin_yoy_pct")),
        "inventory_yoy_pct": _to_float(snapshot.get("inventory_yoy_pct")),
        "receivable_yoy_pct": _to_float(snapshot.get("receivable_yoy_pct")),
        "operating_cashflow_yoy_pct": _to_float(snapshot.get("operating_cashflow_yoy_pct")),
    }
    fact = (
        f"{candidate.get('name', '')}({candidate.get('code', '')})财务核验"
        f"{metrics['report_period'] or ''}：营收同比 {_fmt_pct(metrics['revenue_yoy_pct'])}，"
        f"毛利率 {('%.2f%%' % metrics['gross_margin_pct']) if metrics['gross_margin_pct'] is not None else '未知'}，"
        f"毛利率同比 {_fmt_pct(metrics['gross_margin_yoy_pct'])}，"
        f"存货同比 {_fmt_pct(metrics['inventory_yoy_pct'])}，"
        f"应收同比 {_fmt_pct(metrics['receivable_yoy_pct'])}，"
        f"经营现金流同比 {_fmt_pct(metrics['operating_cashflow_yoy_pct'])}。"
    )
    has_core_metrics = (
        metrics["revenue_yoy_pct"] is not None
        and metrics["gross_margin_pct"] is not None
    )
    return {
        "fact": fact,
        "strength": "strong" if has_core_metrics else "medium",
        "source": f"{snapshot.get('source', '财务数据')}财务证据",
        "metrics": metrics,
    }


def adjust_scores_with_financial_evidence(
    scores: Dict[str, Any],
    financial_evidence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Adjust Serenity scores with financial transmission evidence."""
    adjusted_scores = dict(scores)
    reasons: List[Dict[str, Any]] = []
    red_flag_signals: Dict[str, bool] = {}
    if not financial_evidence:
        return {"scores": adjusted_scores, "reasons": reasons, "red_flag_signals": red_flag_signals}

    metrics = financial_evidence.get("metrics") or {}
    revenue_yoy = _to_float(metrics.get("revenue_yoy_pct"))
    gross_margin_yoy = _to_float(metrics.get("gross_margin_yoy_pct"))
    inventory_yoy = _to_float(metrics.get("inventory_yoy_pct"))
    receivable_yoy = _to_float(metrics.get("receivable_yoy_pct"))
    cashflow_yoy = _to_float(metrics.get("operating_cashflow_yoy_pct"))

    def lower(dimension: str, amount: int, reason: str) -> None:
        before = int(adjusted_scores.get(dimension, 1))
        after = max(1, min(10, before - amount))
        adjusted_scores[dimension] = after
        reasons.append({"dimension": dimension, "before": before, "after": after, "reason": reason})

    if financial_evidence.get("strength") == "weak":
        lower("证据强度", 1, "财务证据不可用或过弱，研究结论需要公告和财报补证。")

    if revenue_yoy is not None:
        if inventory_yoy is not None and receivable_yoy is not None:
            if inventory_yoy > revenue_yoy and receivable_yoy > revenue_yoy:
                red_flag_signals["inventory_receivable_growth"] = True
                lower(
                    "证据强度",
                    1,
                    f"存货同比 {_fmt_pct(inventory_yoy)}、应收同比 {_fmt_pct(receivable_yoy)} 均快于营收同比 {_fmt_pct(revenue_yoy)}。",
                )
        if revenue_yoy > 0 and gross_margin_yoy is not None and gross_margin_yoy < 0:
            red_flag_signals["margin_not_improving"] = True
            lower(
                "传导清晰度",
                1,
                f"营收增长但毛利率同比 {_fmt_pct(gross_margin_yoy)}，瓶颈稀缺尚未传导到盈利质量。",
            )

    if cashflow_yoy is not None and cashflow_yoy < -30:
        lower("下行安全", 1, f"经营现金流同比 {_fmt_pct(cashflow_yoy)}，现金流质量需要优先核验。")

    return {"scores": adjusted_scores, "reasons": reasons, "red_flag_signals": red_flag_signals}


def load_financial_evidence_cache(path: Optional[str] = None) -> Dict[str, Any]:
    cache_path = Path(path or os.getenv("SERENITY_FINANCIAL_EVIDENCE_PATH") or DEFAULT_FINANCIAL_EVIDENCE_CACHE_PATH)
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_financial_evidence_cache(data: Dict[str, Any], path: Optional[str] = None) -> None:
    cache_path = Path(path or os.getenv("SERENITY_FINANCIAL_EVIDENCE_PATH") or DEFAULT_FINANCIAL_EVIDENCE_CACHE_PATH)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_latest_snapshot(code: str, rows: Any, source: str) -> Dict[str, Any]:
    if rows is None or getattr(rows, "empty", False):
        return {"code": code, "status": "unavailable", "source": source}
    row = rows.iloc[0] if hasattr(rows, "iloc") else rows[0]
    get = row.get if hasattr(row, "get") else lambda key, default=None: default
    return {
        "code": code,
        "report_period": str(get("end_date", get("ann_date", ""))),
        "revenue_yoy_pct": _to_float(get("or_yoy", get("tr_yoy", get("revenue_yoy_pct")))),
        "gross_margin_pct": _to_float(get("grossprofit_margin", get("gross_margin_pct"))),
        "gross_margin_yoy_pct": _to_float(get("gross_margin_yoy_pct")),
        "inventory_yoy_pct": _to_float(get("inventory_yoy_pct")),
        "receivable_yoy_pct": _to_float(get("receivable_yoy_pct")),
        "operating_cashflow_yoy_pct": _to_float(get("ocf_yoy", get("operating_cashflow_yoy_pct"))),
        "source": source,
        "status": "success",
    }


def _to_ts_code(code: str) -> str:
    code = code.replace("sh", "").replace("sz", "").replace("bj", "")
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith("8"):
        return f"{code}.BJ"
    return f"{code}.SZ"


async def fetch_tushare_financial_evidence(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch financial indicators from Tushare when permissions are available."""
    try:
        import tushare as ts
    except Exception:
        return {}
    token = os.getenv("TUSHARE_TOKEN", "")
    if not token:
        try:
            token = ts.get_token() or ""
        except Exception:
            token = ""
    if not token:
        return {}
    try:
        pro = ts.pro_api(token)
    except Exception:
        return {}

    results: Dict[str, Dict[str, Any]] = {}
    for code in codes:
        ts_code = _to_ts_code(code)
        try:
            rows = await asyncio.to_thread(pro.fina_indicator, ts_code=ts_code, limit=1)
            results[code] = _extract_latest_snapshot(code, rows, "tushare")
        except Exception as exc:
            results[code] = {
                "code": code,
                "status": "unavailable",
                "source": "tushare",
                "error": str(exc)[:160],
            }
    return results


async def fetch_akshare_financial_evidence(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch public financial indicators from AKShare when installed."""
    try:
        import akshare as ak
    except Exception:
        return {}

    results: Dict[str, Dict[str, Any]] = {}
    for code in codes:
        try:
            rows = await asyncio.to_thread(ak.stock_financial_analysis_indicator, symbol=code)
            results[code] = _extract_latest_snapshot(code, rows, "akshare")
        except Exception as exc:
            results[code] = {
                "code": code,
                "status": "unavailable",
                "source": "akshare",
                "error": str(exc)[:160],
            }
    return results


async def fetch_financial_evidence(
    codes: List[str],
    cache_path: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch real financial evidence with cache fallback."""
    if not codes:
        return {}
    results = await fetch_tushare_financial_evidence(codes)
    missing = [code for code in codes if not results.get(code) or results[code].get("status") != "success"]
    if missing:
        ak_results = await fetch_akshare_financial_evidence(missing)
        results.update({code: data for code, data in ak_results.items() if data.get("status") == "success"})

    cache = load_financial_evidence_cache(cache_path)
    for code in codes:
        if not results.get(code) or results[code].get("status") != "success":
            cached = cache.get(code)
            if isinstance(cached, dict):
                results[code] = {**cached, "source": cached.get("source", "cache")}
            else:
                results[code] = results.get(code) or {
                    "code": code,
                    "status": "unavailable",
                    "source": "financial-evidence",
                }

    successful = {code: data for code, data in results.items() if data.get("status") == "success"}
    if successful:
        merged_cache = {**cache, **successful}
        save_financial_evidence_cache(merged_cache, cache_path)
    return results


def run_optional_fetcher(fetcher: Callable[[List[str]], Any], codes: List[str]) -> Dict[str, Dict[str, Any]]:
    result = fetcher(codes)
    if asyncio.iscoroutine(result):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)
        raise RuntimeError("financial_fetcher requires async caller")
    return result or {}
