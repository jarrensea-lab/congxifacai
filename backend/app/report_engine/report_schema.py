"""报告标准化数据模型 — 所有报告统一Schema"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class PositionItem(BaseModel):
    code: str
    name: str
    quantity: int
    cost_price: float
    current_price: float
    profit_pct: float
    market_value: float
    risk_level: str = "normal"  # normal / warning / danger


class Recommendation(BaseModel):
    code: str
    name: str
    strategy_type: str          # short_term / mid_low_freq
    buy_range: str = ""
    stop_loss: str = ""
    target: str = ""
    reason: str = ""
    technical_signals: str = ""
    concept_tags: list[str] = []
    trend_score: int = 5       # 1-10
    beginner_guide: str = ""
    recommend_date: str = ""


class RiskAlert(BaseModel):
    stock_code: str
    stock_name: str
    alert_type: str
    level: str                  # low / mid / high
    message: str
    suggestion: str = ""
    timestamp: str = ""


class PerformanceData(BaseModel):
    daily_pnl: float = 0
    daily_pnl_pct: float = 0
    cumulative_pnl: float = 0
    win_rate: float = 0
    position_count: int = 0
    total_assets: float = 0
    available_cash: float = 0


class SystemHealth(BaseModel):
    api_service: bool = False
    deepseek_api: bool = False
    qwen_api: bool = False
    tencent_data: bool = False
    eastmoney_data: bool = False
    tushare_data: bool = False
    tasks_success: int = 0
    tasks_fail: int = 0
    last_error: Optional[str] = None


class ReportData(BaseModel):
    report_type: str                      # premarket / midday / risk / closing
    generated_at: Optional[datetime] = None
    date: str = ""
    risk_level: int = 3                   # 1-5
    market_direction: str = ""
    market_summary: str = ""
    confidence: int = 5
    positions: list[PositionItem] = []
    recommendations: list[Recommendation] = []
    alerts: list[RiskAlert] = []
    performance: Optional[PerformanceData] = None
    system_health: Optional[SystemHealth] = None
    knowledge_tip: str = ""
    top_sectors: list[str] = []
