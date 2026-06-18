"""辩论质量追踪 — 记录五角色快照 + 回填实际收益 + 绩效摘要"""
from datetime import datetime
from typing import Optional, List
from app.database import SessionLocal
from app.models import DebateResult
from app.utils.logger import logger


def classify_market(sh_change_pct: float) -> str:
    """根据上证指数涨跌幅度分类市场状态"""
    if sh_change_pct >= 1.5:
        return "trending_up"
    elif sh_change_pct <= -1.5:
        return "trending_down"
    else:
        return "ranging"


class DebateTracker:
    """辩论质量追踪器"""

    @staticmethod
    def save(strategy_type: str, engine_result: dict,
             sh_change_pct: float = 0) -> Optional[int]:
        """辩论结束后保存五角色快照

        Args:
            strategy_type: premarket / midday / review
            engine_result: AIDebateEngine.debate() 的返回结果
            sh_change_pct: 辩论时上证指数涨跌幅
        """
        final = engine_result.get("final", {})
        st = final.get("short_term", {})
        ml = final.get("mid_low_freq", {})

        short_codes = [r.get("code", "") for r in st.get("recommendations", []) if r.get("code")]
        mid_codes = [r.get("code", "") for r in ml.get("recommendations", []) if r.get("code")]

        # 从 debate 原始输出中提取角色信息
        debate = engine_result.get("debate", {})
        guardian_raw = debate.get("guardian", {})
        if isinstance(guardian_raw, dict):
            guardian_risk = guardian_raw.get("risk_appetite", "")
        else:
            guardian_risk = ""
        researcher_raw = debate.get("researcher", {})
        if isinstance(researcher_raw, dict):
            researcher_dec = researcher_raw.get("analysis", "")[:10] if isinstance(researcher_raw.get("analysis"), str) else ""
            researcher_conv = researcher_raw.get("conviction", 0) or 0
        else:
            researcher_dec = ""
            researcher_conv = 0

        db = SessionLocal()
        try:
            record = DebateResult(
                strategy_type=strategy_type,
                debated_at=datetime.now(),
                market_condition=classify_market(sh_change_pct),
                hunter_decision="buy" if len(st.get("recommendations", [])) > 0 else "hold",
                hunter_conviction=len(st.get("recommendations", [])),
                accountant_decision="buy" if len(ml.get("recommendations", [])) > 0 else "hold",
                accountant_conviction=len(ml.get("recommendations", [])),
                guardian_risk_level=engine_result.get("recommended_risk_level", 3),
                guardian_risk_appetite=guardian_risk,
                judge_decision=final.get("final_decision", "hold"),
                judge_confidence=final.get("confidence", 5),
                researcher_decision=researcher_dec,
                researcher_conviction=researcher_conv,
                short_term_codes=short_codes,
                mid_term_codes=mid_codes,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            logger.info(f"辩论快照已保存: {strategy_type} #{record.id}")
            return record.id
        except Exception as e:
            logger.error(f"辩论快照保存失败: {e}")
            db.rollback()
            return None
        finally:
            db.close()

    @staticmethod
    def fill_pending(db) -> int:
        """回填已到期的辩论实际收益

        在收盘复盘任务中调用。5天后回填短线收益，20天后回填中线收益。
        """
        pending = db.query(DebateResult).filter(
            DebateResult.result_filled_at.is_(None)
        ).all()

        now = datetime.now()
        filled = 0

        for record in pending:
            if not record.debated_at:
                continue
            days_elapsed = (now - record.debated_at).days

            # 5日短线回填
            if record.short_term_codes and record.short_term_return_5d is None and days_elapsed >= 5:
                ret = DebateTracker._fetch_avg_return(record.short_term_codes)
                record.short_term_return_5d = ret
                # 方向正确性: 推荐买入且收益>0, 或推荐卖出且收益<0
                jd = (record.judge_decision or "").lower()
                is_buy = "买" in jd or "buy" in jd
                is_sell = "卖" in jd or "sell" in jd
                is_hold = "持有" in jd or "hold" in jd
                if is_buy and ret > 0:
                    record.judge_direction_correct = True
                elif is_sell and ret < 0:
                    record.judge_direction_correct = True
                elif is_hold:
                    record.judge_direction_correct = True  # 持有不判断
                else:
                    record.judge_direction_correct = False

            # 20日中线回填
            if record.mid_term_codes and record.mid_term_return_20d is None and days_elapsed >= 20:
                ret = DebateTracker._fetch_avg_return(record.mid_term_codes)
                record.mid_term_return_20d = ret

            # 标记完成
            st_done = record.short_term_return_5d is not None or not record.short_term_codes
            mt_done = record.mid_term_return_20d is not None or not record.mid_term_codes
            if st_done and mt_done:
                record.result_filled_at = now
                filled += 1

        if filled:
            db.commit()
            logger.info(f"辩论回填: {filled} 条")
        return filled

    @staticmethod
    def _fetch_avg_return(stock_codes: List[str]) -> float:
        """获取推荐标的当前平均涨跌幅（腾讯实时行情, 简化为当日涨跌幅）"""
        if not stock_codes:
            return 0.0
        try:
            from app.data_sources.tencent_client import TencentDataSource
            import asyncio
            tc = TencentDataSource()
            loop = asyncio.new_event_loop()
            try:
                batch = loop.run_until_complete(tc.fetch_batch(stock_codes))
            finally:
                loop.close()
            returns = []
            for code in stock_codes:
                d = batch.get(code, {})
                chg = d.get("change_pct")
                if chg is not None:
                    returns.append(chg)
            return (sum(returns) / len(returns)) if returns else 0.0
        except Exception as e:
            logger.warning(f"获取推荐标的收益失败: {e}")
            return 0.0

    @staticmethod
    def get_performance_summary(db, market_condition: str = None) -> str:
        """生成角色近期表现摘要（给裁判 prompt 注入用）

        按市场状态分组统计各角色的判断准确率。
        """
        query = db.query(DebateResult).filter(
            DebateResult.judge_direction_correct.isnot(None)
        )
        if market_condition:
            query = query.filter(DebateResult.market_condition == market_condition)

        records = query.order_by(DebateResult.debated_at.desc()).limit(50).all()
        if not records:
            return ""

        total = len(records)
        correct = sum(1 for r in records if r.judge_direction_correct)
        judge_acc = correct / total * 100

        st_pos = sum(1 for r in records if r.short_term_return_5d is not None and r.short_term_return_5d > 0)
        st_tot = sum(1 for r in records if r.short_term_return_5d is not None)
        mt_pos = sum(1 for r in records if r.mid_term_return_20d is not None and r.mid_term_return_20d > 0)
        mt_tot = sum(1 for r in records if r.mid_term_return_20d is not None)

        ctx = "全部市场" if not market_condition else f"市场状态=[{market_condition}]"
        lines = [
            f"【辩论表现跟踪 · 最近{total}条 · {ctx}】",
            f"裁判: 方向判断正确 {correct}/{total} = {judge_acc:.0f}%",
        ]
        if st_tot > 0:
            lines.append(f"猎手(短线): 选股正收益 {st_pos}/{st_tot} = {st_pos/st_tot*100:.0f}%")
        if mt_tot > 0:
            lines.append(f"账房(中线): 选股正收益 {mt_pos}/{mt_tot} = {mt_pos/mt_tot*100:.0f}%")

        return "\n".join(lines)
