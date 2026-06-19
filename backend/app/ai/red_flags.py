"""红旗信号检测（风控增强）"""
from typing import Dict, Any, List
from app.ai.constants import RED_FLAGS


def check_red_flags(
    signals: Dict[str, bool],
    custom_flags: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """检查红旗信号

    Args:
        signals: {flag_id: True/False} 表示是否触发
        custom_flags: 自定义红旗信息

    Returns:
        触发的红旗列表
    """
    triggered = []
    for flag in RED_FLAGS:
        if signals.get(flag["id"], False):
            triggered.append({
                "id": flag["id"],
                "label": flag["label"],
                "check": flag["check"],
                "severity": flag["severity"],
            })
    if custom_flags:
        for cf in custom_flags:
            triggered.append({
                "id": "custom",
                "label": cf,
                "check": cf,
                "severity": "medium",
            })
    return triggered


def summarize_red_flags(flags: List[Dict[str, Any]]) -> str:
    """将红旗信号格式化为文本"""
    if not flags:
        return "✅ 未检测到明显红旗信号"
    high = [f for f in flags if f["severity"] == "high"]
    medium = [f for f in flags if f["severity"] == "medium"]
    lines = []
    if high:
        lines.append("🔴 **高风险信号：**")
        for f in high:
            lines.append(f"  - {f['label']}")
    if medium:
        lines.append("🟡 **中等风险信号：**")
        for f in medium:
            lines.append(f"  - {f['label']}")
    return "\n".join(lines)
