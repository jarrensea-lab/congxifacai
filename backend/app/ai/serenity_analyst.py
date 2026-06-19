"""
Serenity 产业链分析引擎 — 供应链瓶颈分析 + 7 维评分 + 红旗信号检测

⚠️ 兼容层：此文件仅从子模块 re-export 所有符号。
实际实现在以下模块中：
  - constants.py   — 常量定义 (VALUE_CHAIN_LAYERS, THEME_CHOKEPOINTS, SCORING_DIMENSIONS, RED_FLAGS)
  - scoring.py     — 评分逻辑 (score_company, score_summary_table)
  - red_flags.py   — 红旗信号 (check_red_flags, summarize_red_flags)
  - industry_chain.py — 产业链提示词 (build_industry_chain_prompt, INDUSTRY_CHAIN_PROMPT_TEMPLATE, RESEARCHER_DEBATE_PROMPT)
  - evidence.py    — 证据总结 (evidence_summary, EVIDENCE_STRENGTH)
  - chokepoints.py — 卡点查询 (get_theme_chokepoints, get_chokepoint_prompt)
"""

from app.ai.constants import (
    VALUE_CHAIN_LAYERS,
    THEME_CHOKEPOINTS,
    SCORING_DIMENSIONS,
    RED_FLAGS,
)
from app.ai.scoring import (
    score_company,
    score_summary_table,
)
from app.ai.red_flags import (
    check_red_flags,
    summarize_red_flags,
)
from app.ai.industry_chain import (
    build_industry_chain_prompt,
    INDUSTRY_CHAIN_PROMPT_TEMPLATE,
    RESEARCHER_DEBATE_PROMPT,
)
from app.ai.evidence import (
    evidence_summary,
    EVIDENCE_STRENGTH,
)
from app.ai.chokepoints import (
    get_theme_chokepoints,
    get_chokepoint_prompt,
)

__all__ = [
    "VALUE_CHAIN_LAYERS",
    "THEME_CHOKEPOINTS",
    "SCORING_DIMENSIONS",
    "score_company",
    "score_summary_table",
    "RED_FLAGS",
    "check_red_flags",
    "summarize_red_flags",
    "build_industry_chain_prompt",
    "INDUSTRY_CHAIN_PROMPT_TEMPLATE",
    "RESEARCHER_DEBATE_PROMPT",
    "EVIDENCE_STRENGTH",
    "evidence_summary",
    "get_theme_chokepoints",
    "get_chokepoint_prompt",
]
