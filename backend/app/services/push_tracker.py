"""推送状态追踪器 — 记录推送状态 + 自动重试失败推送"""
import asyncio
import random
from datetime import datetime, date
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import PushRecord
from app.utils.logger import logger


class PushTracker:
    """推送状态追踪器"""

    # 指数退避重试的基准秒数
    RETRY_BASE_DELAY = 30
    RETRY_MAX_DELAY = 600  # 10 分钟上限

    def record(self, push_type: str, push_date: str, status: str = "pending",
               error_message: Optional[str] = None, max_retries: int = 3) -> Optional[int]:
        """记录一次推送

        Args:
            push_type: 推送类型 (premarket/midday/afternoon/review/daily_report)
            push_date: 推送日期 YYYY-MM-DD
            status: pending/success/failed
            error_message: 失败时的错误信息
            max_retries: 最大重试次数，默认 3

        Returns:
            record_id 或 None
        """
        db = SessionLocal()
        try:
            record = PushRecord(
                push_type=push_type,
                push_date=push_date,
                status=status,
                error_message=error_message,
                retry_count=0,
                max_retries=max_retries,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record.id
        except Exception as e:
            logger.error(f"推送记录写入失败: push_type={push_type} error={e}")
            return None
        finally:
            db.close()

    def mark_success(self, record_id: int) -> bool:
        """标记推送成功"""
        return self._update_status(record_id, "success", error_message=None)

    def mark_failed(self, record_id: int, error_message: str) -> bool:
        """标记推送失败（增加重试计数）"""
        db = SessionLocal()
        try:
            record = db.query(PushRecord).filter(PushRecord.id == record_id).first()
            if not record:
                return False
            record.status = "failed"
            record.error_message = error_message[:500]
            record.retry_count = PushRecord.retry_count + 1
            record.last_retry_at = datetime.now()
            db.commit()
            return True
        except Exception as e:
            logger.error(f"标记失败异常: {e}")
            return False
        finally:
            db.close()

    def mark_retrying(self, record_id: int) -> bool:
        """标记正在重试"""
        return self._update_status(record_id, "retrying")

    def get_failed_records(self, max_retries: int = 3) -> list[PushRecord]:
        """获取需要重试的失败记录（未超过最大重试次数）"""
        db = SessionLocal()
        try:
            records = (
                db.query(PushRecord)
                .filter(
                    PushRecord.status == "failed",
                    PushRecord.retry_count < PushRecord.max_retries,
                )
                .order_by(PushRecord.last_retry_at.asc().nullsfirst())
                .limit(10)
                .all()
            )
            return records
        except Exception as e:
            logger.error(f"查询失败记录异常: {e}")
            return []
        finally:
            db.close()

    def get_today_records(self, push_date: Optional[str] = None) -> list[PushRecord]:
        """获取指定日期的推送记录"""
        if push_date is None:
            push_date = str(date.today())
        db = SessionLocal()
        try:
            return (
                db.query(PushRecord)
                .filter(PushRecord.push_date == push_date)
                .order_by(PushRecord.created_at.asc())
                .all()
            )
        finally:
            db.close()

    def get_today_summary(self, push_date: Optional[str] = None) -> str:
        """生成当日推送状态摘要（用于系统日报）"""
        records = self.get_today_records(push_date)
        if not records:
            return "今日暂无推送记录"

        total = len(records)
        success = sum(1 for r in records if r.status == "success")
        failed = sum(1 for r in records if r.status == "failed")
        retrying = sum(1 for r in records if r.status == "retrying")
        pending = sum(1 for r in records if r.status == "pending")

        lines = [f"📋 今日推送状态 ({total} 次)"]
        lines.append(f"- ✅ 成功: {success}")
        if failed:
            lines.append(f"- ❌ 失败: {failed}")
        if retrying:
            lines.append(f"- 🔄 重试中: {retrying}")
        if pending:
            lines.append(f"- ⏳ 待处理: {pending}")

        if failed:
            lines.append("")
            lines.append("失败详情:")
            for r in records:
                if r.status == "failed":
                    err = (r.error_message or "未知错误")[:80]
                    lines.append(f"  - [{r.push_type}] {err} (已重试 {r.retry_count}/{r.max_retries})")

        return "\n".join(lines)

    def _update_status(self, record_id: int, status: str,
                       error_message: Optional[str] = None) -> bool:
        db = SessionLocal()
        try:
            record = db.query(PushRecord).filter(PushRecord.id == record_id).first()
            if not record:
                return False
            record.status = status
            if error_message is not None:
                record.error_message = error_message[:500]
            db.commit()
            return True
        except Exception as e:
            logger.error(f"更新状态异常: id={record_id} status={status} error={e}")
            return False
        finally:
            db.close()


def compute_retry_delay(attempt: int, base_delay: int = 30, max_delay: int = 600) -> float:
    """指数退避延迟计算（含随机抖动）

    Args:
        attempt: 当前是第几次重试 (从 1 开始)
        base_delay: 基准延迟秒数
        max_delay: 最大延迟秒数

    Returns:
        等待秒数
    """
    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter


# 全局单例
push_tracker = PushTracker()
