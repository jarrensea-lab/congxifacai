"""策略生命周期协调器 — 串联 6 个阶段"""
from datetime import datetime
from app.database import SessionLocal
from app.models import StrategyInstance


class StrategyLifecycle:
    """管理策略从创建到回顾的完整生命周期"""

    def __init__(self):
        self.db = SessionLocal()

    def create_instance(self) -> StrategyInstance:
        """创建新的策略实例"""
        instance = StrategyInstance(
            created_at=datetime.now(),
            status="draft",
            risk_level=3,
            position_limit_pct=30.0,
            single_stock_limit_pct=15.0,
            stop_loss_pct=-5.0,
            holding_period_days=5,
        )
        self.db.add(instance)
        self.db.commit()
        self.db.refresh(instance)
        return instance

    def get_active_instance(self) -> StrategyInstance | None:
        """获取当前活跃的策略实例"""
        return (
            self.db.query(StrategyInstance)
            .filter(StrategyInstance.status.in_([
                "draft", "analyzed", "confirmed", "planned", "executing"
            ]))
            .order_by(StrategyInstance.created_at.desc())
            .first()
        )

    def update_status(self, instance: StrategyInstance, status: str):
        """更新策略实例状态"""
        instance.status = status
        instance.updated_at = datetime.now()
        self.db.commit()

    def close(self):
        self.db.close()
