"""7 维评分逻辑"""
from typing import Dict, Any, List
from app.ai.constants import SCORING_DIMENSIONS


def score_company(
    name: str,
    code: str,
    demand_certainty: int,
    transmission_clarity: int,
    business_purity: int,
    market_cap_elasticity: int,
    market_neglect: int,
    verification_speed: int,
    downside_risk: int,
    custom_notes: str = "",
) -> Dict[str, Any]:
    """对一家公司/标的进行 7 维评分

    Args:
        name: 公司名称
        code: 股票代码
        demand_certainty: 需求确定性 (1-10)
        transmission_clarity: 传导清晰度 (1-10)
        business_purity: 业务纯度 (1-10)
        market_cap_elasticity: 市值弹性 (1-10)
        market_neglect: 市场忽视度 (1-10)
        verification_speed: 验证速度 (1-10)
        downside_risk: 下行风险/安全性 (1-10, 越高越安全)
        custom_notes: 自定义备注

    Returns:
        评分结果 dict
    """
    scores = {
        "需求确定性": demand_certainty,
        "传导清晰度": transmission_clarity,
        "业务纯度": business_purity,
        "市值弹性": market_cap_elasticity,
        "市场忽视度": market_neglect,
        "验证速度": verification_speed,
        "下行风险": downside_risk,
    }

    weighted_sum = sum(
        scores[dim] * SCORING_DIMENSIONS[dim]["weight"]
        for dim in scores
    )
    # 标准化到 0-100
    total_score = round(weighted_sum * 10, 1)

    return {
        "name": name,
        "code": code,
        "score": total_score,
        "scores": scores,
        "breakdown": {
            dim: {
                "value": scores[dim],
                "weight": SCORING_DIMENSIONS[dim]["weight"],
                "weighted": round(scores[dim] * SCORING_DIMENSIONS[dim]["weight"], 2),
                "description": _describe_level(dim, scores[dim]),
            }
            for dim in scores
        },
        "notes": custom_notes,
    }


def _describe_level(dimension: str, level: int) -> str:
    """获取评分等级的文本描述"""
    dim = SCORING_DIMENSIONS.get(dimension, {})
    levels = dim.get("levels", {})
    # 找最接近的等级
    closest = min(levels.keys(), key=lambda k: abs(k - level))
    return levels.get(closest, "")


def score_summary_table(results: List[Dict[str, Any]]) -> str:
    """将多个标的评分结果格式化为摘要表格

    Returns:
        Markdown 格式的表格
    """
    if not results:
        return "无评分结果"
    lines = [
        "| 标的 | 总分 | 需求确定性 | 传导清晰度 | 业务纯度 | 市值弹性 | 市场忽视度 | 验证速度 | 下行安.",
        "|------|:---:|:----------:|:----------:|:--------:|:--------:|:----------:|:--------:|:--------:|"
    ]
    for r in sorted(results, key=lambda x: x["score"], reverse=True):
        s = r["scores"]
        lines.append(
            f"| {r['name']}({r['code']}) | **{r['score']}** "
            f"| {s['需求确定性']} | {s['传导清晰度']} | {s['业务纯度']} "
            f"| {s['市值弹性']} | {s['市场忽视度']} | {s['验证速度']} | {s['下行风险']} |"
        )
    return "\n".join(lines)
