"""系统相关数据模型"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON
from app.database import Base


class RiskAlert(Base):
    """风险提醒表"""
    __tablename__ = "risk_alerts"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(6), nullable=False, index=True)
    stock_name = Column(String(50))
    alert_type = Column(String(20), comment="风险类型：price_drop/volume_spike/fund_outflow")
    alert_level = Column(String(10), comment="风险等级：high/medium/low")
    alert_message = Column(Text, comment="风险描述")
    suggestion = Column(String(500), comment="操作建议")
    trading_rule_triggered = Column(Text, comment="触发的交易规则描述")
    review_type = Column(String(20), default="risk")  # risk / daily_review
    timestamp = Column(DateTime(timezone=True), default=datetime.now, comment="时间戳")

    def to_dict(self):
        return {
            "id": self.id,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "alert_type": self.alert_type,
            "alert_level": self.alert_level,
            "alert_message": self.alert_message,
            "suggestion": self.suggestion,
            "trading_rule_triggered": self.trading_rule_triggered,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }


class UserPreference(Base):
    """用户偏好 — 分析维度权重、默认风险等级"""
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dimension_weights = Column(JSON, default=lambda: {
        "technical": 0.30,
        "fundamental": 0.25,
        "capital_flow": 0.20,
        "sentiment": 0.15,
        "macro": 0.10,
    })
    default_risk_level = Column(Integer, default=3)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class PushRecord(Base):
    """推送记录表 — 追踪每次定时推送的状态，失败时支持自动重试"""
    __tablename__ = "push_records"

    id = Column(Integer, primary_key=True, index=True)
    push_type = Column(String(20), nullable=False, index=True,
                   comment="推送类型: premarket/midday/afternoon/review/daily_report/bot")
    push_date = Column(String(10), nullable=False, index=True, comment="推送日期 YYYY-MM-DD")
    status = Column(String(20), nullable=False, default="pending",
                   comment="状态: pending/success/failed/retrying")
    error_message = Column(Text, nullable=True, comment="失败时的错误信息")
    retry_count = Column(Integer, nullable=False, default=0, comment="已重试次数")
    max_retries = Column(Integer, nullable=False, default=3, comment="最大重试次数")
    last_retry_at = Column(DateTime(timezone=True), nullable=True, comment="最近一次重试时间")
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "push_type": self.push_type,
            "push_date": self.push_date,
            "status": self.status,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "last_retry_at": self.last_retry_at.isoformat() if self.last_retry_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
