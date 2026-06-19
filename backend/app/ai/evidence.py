"""证据等级评定"""
from typing import Dict, Any, List

EVIDENCE_STRENGTH = {
    "strong": {
        "label": "强证据",
        "emoji": "🟢",
        "sources": [
            "交易所文件/公告", "财报/年报/半年报/季报",
            "电话会/IR演示材料", "官方客户合同/订单公告",
            "监管文件/项目备案/环评/能评", "专利/标准/技术文献",
        ],
    },
    "medium": {
        "label": "中等证据",
        "emoji": "🟡",
        "sources": [
            "可信财经媒体", "行业期刊/协会数据",
            "公司官网/产品页面", "卖方/专业研究（假设可见）",
            "供应商/客户交叉公开验证",
        ],
    },
    "weak": {
        "label": "弱证据",
        "emoji": "🔴",
        "sources": [
            "KOL/社交媒体帖子", "论坛讨论",
            "来源不明的截图", "无基本面支撑的价格异动",
        ],
    },
}


def evidence_summary(company: str, evidence_items: List[Dict[str, str]]) -> str:
    """为一家公司生成证据摘要

    Args:
        company: 公司名称
        evidence_items: [{"fact": "...", "strength": "strong|medium|weak", "source": "..."}, ...]

    Returns:
        Markdown 格式的证据摘要
    """
    if not evidence_items:
        return f"{company}: 暂无明确证据"
    lines = [f"**{company} 证据摘要**"]
    for item in evidence_items:
        strength = EVIDENCE_STRENGTH.get(item["strength"], {})
        emoji = strength.get("emoji", "⚪")
        label = strength.get("label", "未知")
        lines.append(f"- {emoji} **{label}**: {item['fact']} — 来源: {item.get('source', '?')}")
    return "\n".join(lines)
