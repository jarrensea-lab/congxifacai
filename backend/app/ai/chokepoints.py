"""A 股产业链卡点预检（用于盘前快速扫描）"""
from typing import List
from app.ai.constants import THEME_CHOKEPOINTS


def get_theme_chokepoints(theme: str) -> List[str]:
    """获取常见产业链主题的卡点环节"""
    return THEME_CHOKEPOINTS.get(theme, [])


def get_chokepoint_prompt(theme: str = "", news_summary: str = "") -> str:
    """生成产业链卡点预检 Prompt，用于盘前快速扫描"""
    chokepoints_hint = ""
    if theme and theme in THEME_CHOKEPOINTS:
        chokepoints = THEME_CHOKEPOINTS[theme]
        chokepoints_hint = "\n该主题的典型卡点环节：\n" + "\n".join(f"  - {cp}" for cp in chokepoints)

    return f"""你是一位产业链卡点预检分析师。请在盘前对当前市场热点进行快速产业链扫描。

【市场热点】
{news_summary}

【提示卡点】
{chokepoints_hint}

请快速分析：
1. 今天市场上最热的产业链主题是什么？
2. 哪些产业链层级今天最值得关注？
3. 有没有新的供应链信号/事件值得跟踪？

输出JSON格式（简洁版）：
{{{{
    "hot_theme": "最热的产业链主题",
    "chokepoint_layer": "最紧的卡点层级",
    "focus_reason": "为什么这个层级值得关注",
    "trigger_event": "今天的触发信号（如有）",
    "watch_items": ["需要跟踪的方向"],
    "priority": "高/中/低"
}}}}
"""
