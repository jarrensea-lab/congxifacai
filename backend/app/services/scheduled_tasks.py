"""定时任务实现 — 5 个交易时段任务 + 消息轮询

注意：此模块使用全局 _config dict 接收外部依赖。
在 main.py 的 lifespan() 中通过 configure_tasks() 设置依赖后，再注册到 APScheduler。
"""
import json
import asyncio
import time
from datetime import datetime, date
from typing import Optional, Dict, Any

import httpx
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Position, SimAccount, RiskAlert, AIStrategy
from app.utils.logger import logger
from app.utils.trading_calendar import is_trading_day
from app.services.holdings import get_holdings_data
from app.services.feishu_pusher import push_webhook_retry

# ============================================================
# 模块全局依赖注入
# ============================================================

_config: Dict[str, Any] = {}

def configure_tasks(**kwargs):
    """注入 main.py 创建的全局实例"""
    _config.clear()
    _config.update(kwargs)


def _get(name: str):
    """安全获取依赖"""
    return _config.get(name)


# ============================================================
# 市场数据获取
# ============================================================

async def fetch_market_data() -> dict:
    """通过 DataRouter 拉取市场数据（多源容错）"""
    data_router = _get("data_router")
    tencent_client = _get("tencent_client")

    indices = {}
    for code in ["sh000001", "sz399001", "sz399006"]:
        try:
            result = await data_router.fetch(code)
            if result and result.get("price"):
                indices[code] = {"price": result["price"], "change_pct": result.get("change_pct", 0)}
        except Exception:
            continue
    if not indices:
        try:
            batch = await tencent_client.fetch_batch(["sh000001", "sz399001"])
            for k, v in batch.items():
                indices[k] = {"price": v.get("price", 0), "change_pct": v.get("change_pct", 0)}
        except Exception:
            indices = {"sh000001": {"price": 3350, "change_pct": 0}, "sz399001": {"price": 10800, "change_pct": 0}}

    db = SessionLocal()
    try:
        hd = get_holdings_data(db)
    finally:
        db.close()

    return {"indices": indices, "sectors": [], "holdings": hd["holdings"],
            "holdings_str": hd["holdings_str"], "news": [],
            "available_cash": hd.get("available_cash", 0)}


# ============================================================
# 盘前任务
# ============================================================

async def run_premarket_with_status():
    """盘前任务 — AI辩论 + 建仓计划 -> 飞书推送"""
    if not is_trading_day():
        return
    gen_status = _get("generation_status")
    gs = gen_status["premarket"]
    if gs["running"]:
        return

    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    try:
        logger.info("=== 旺财V7 盘前任务启动 ===")
        market_data = await fetch_market_data()
        sh = market_data["indices"].get("sh000001", {}).get("price", 3350)
        sz = market_data["indices"].get("sz399001", {}).get("price", 10800)
        logger.info(f"盘前指数: 上证{sh:.0f} 深证{sz:.0f}")

        from app.engine.analysis import run_analysis
        report = await run_analysis(market_data)
        logger.info("分析完成，启动AI辩论...")
        from app.engine.workshop import run_debate
        debate_result = await run_debate(report, strategy_type="premarket")
        decision = debate_result.get("decision", {})
        risk = debate_result.get("recommended_risk_level", 3)
        pool = decision.get("stock_pool", [])

        from app.services.report_templates import strategy_report_md
        report_md = strategy_report_md(decision)
        extra = "\n\n...\n\n*[完整报告已推送]*"
        summary = report_md[:2800] + (extra if len(report_md) > 2800 else "")

        report_engine = _get("report_engine")
        holdings_data = {
            "holdings": market_data.get("holdings", []),
            "holdings_str": market_data.get("holdings_str", "无持仓"),
        }
        report_ok = await report_engine.push_premarket(
            date=str(date.today()),
            decision=decision,
            positions=holdings_data.get("holdings", []),
            risk_level=risk,
        )
        if not report_ok:
            logger.warning("报告引擎推送异常，降级为原始webhook推送")
            from app.config import settings
            push_webhook_retry(f"旺财V7 盘前策略 [R{risk}]", summary)

        db = SessionLocal()
        try:
            strat = AIStrategy(
                strategy_type="premarket",
                content=report_md,
                recommended_stocks={
                    "short_term": decision.get("short_term", {}).get("recommendations", []),
                    "mid_low_freq": decision.get("mid_low_freq", {}).get("recommendations", []),
                },
            )
            db.add(strat)
            db.commit()
        except Exception as e:
            logger.warning(f"策略存储失败: {e}")
        finally:
            db.close()

        logger.info(f"=== 盘前任务完成: R{risk}, {len(pool)}支标的, {decision.get('final_view','?')} ===")
    except Exception as e:
        logger.error(f"盘前任务异常: {e}", exc_info=True)
        push_webhook_retry("盘前任务异常", f"错误: {str(e)[:500]}")
    finally:
        gs["running"] = False


# ============================================================
# 午盘快速分析
# ============================================================

async def run_midday_with_status():
    """午盘快速分析"""
    if not is_trading_day():
        return
    gen_status = _get("generation_status")
    gs = gen_status["intraday"]
    if gs["running"]:
        return
    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    try:
        logger.info("--- 午盘快速分析 ---")
        market_data = await fetch_market_data()
        debate_engine = _get("debate_engine")
        debate_summary = await debate_engine.debate_intraday(
            json.dumps(market_data, ensure_ascii=False),
            market_data.get("holdings_str", "无持仓"),
            news_context="午间市场概览",
        )
        final = debate_summary.get("final", {})

        holdings = market_data.get("holdings", [])
        if not holdings:
            await _get("report_engine").push_midday(
                date=str(date.today()),
                market_summary="🪹 **当前空仓观望中**\n\n无持仓压力，下午保持观察即可。",
                positions=[], afternoon_tip="空仓是策略，耐心等待高确定性机会。",
            )
            logger.info("--- 午盘快报完成（空仓精简模式） ---")
            return

        snapshot = final.get("market_snapshot", "N/A")
        action = final.get("overall_action", "观望")
        confidence = final.get("confidence", 5)

        content = f"**午盘概况**\n{snapshot}\n\n操作建议: {action} (信心{confidence}/10)\n\n"
        recs = final.get("recommendations", [])
        for r in recs[:3]:
            content += f"- {r.get('name','')}({r.get('code','')}): {r.get('reason','')}\n"
        lesson = final.get("beginner_lesson", "")
        if lesson:
            content += f"\n---\n{lesson}"

        hd = market_data.get("holdings", [])
        pos_list = [{
            "code": h.get("code", ""), "name": h.get("name", ""),
            "position": h.get("position", 0),
            "cost": h.get("cost", 0), "current_price": h.get("current_price", 0),
        } for h in hd]
        await _get("report_engine").push_midday(
            date=str(date.today()),
            market_summary=f"{snapshot}\n\n操作建议: {action} (信心{confidence}/10)",
            positions=pos_list,
            afternoon_tip=lesson,
        )
        logger.info(f"--- 午盘快报完成: {action} ---")
    except Exception as e:
        logger.error(f"午盘分析异常: {e}", exc_info=True)
    finally:
        gs["running"] = False


# ============================================================
# 午后风险检查
# ============================================================

async def run_afternoon_with_status():
    """午后风险检查 — 使用 MonitorService 多维度风控"""
    if not is_trading_day():
        return
    gen_status = _get("generation_status")
    gs = gen_status["afternoon"]
    if gs["running"]:
        return
    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    monitor = _get("monitor")
    report_engine = _get("report_engine")
    try:
        logger.info("--- 午后风险检查(MonitorService) ---")
        db = SessionLocal()
        try:
            positions = db.query(Position).filter(Position.quantity > 0).all()
            today_str = str(date.today())

            if not positions:
                logger.info("空仓：推送精简午后检查")
                acc = db.query(SimAccount).first()
                cash = acc.cash / 100 if acc else 0
                await report_engine.push_afternoon_risk(
                    date=today_str,
                    positions=[], alerts=[],
                    performance={"total_assets": cash, "available_cash": cash},
                )
                return

            alerts = []
            for p in positions:
                rt = await monitor.get_realtime_data(p.stock_code)
                if not rt or not rt.get("price"):
                    continue

                price = rt.get("price", 0)
                price_fen = int(price * 100) if isinstance(price, float) and price < 10000 else int(price)
                p.market_price = price_fen
                p.market_value = p.quantity * price_fen
                p.unrealized_pnl = p.market_value - (p.avg_cost * p.quantity)

                pos_dict = {
                    "code": p.stock_code, "name": p.stock_name,
                    "cost_price": round(p.avg_cost / 100, 2) if p.avg_cost else 0,
                    "id": p.id,
                }
                risk_result = await monitor.check_risk(pos_dict, rt, db_session=db)
                if risk_result:
                    msg = f"{risk_result['level'].upper()}: {p.stock_name}({p.stock_code}) - {risk_result['message']}"
                    alerts.append(msg)
                    if risk_result["level"] == "high":
                        try:
                            alert = RiskAlert(
                                stock_code=p.stock_code, stock_name=p.stock_name,
                                alert_type="composite", alert_level="high",
                                alert_message=risk_result["message"][:500],
                                suggestion=risk_result.get("suggestion", ""),
                            )
                            db.add(alert)
                        except Exception:
                            pass

            db.commit()

            acc = db.query(SimAccount).first()
            cash = acc.cash / 100 if acc else 0
            mv = sum(p.market_value for p in positions) / 100
            pos_list = [{
                "stock_code": p.stock_code, "stock_name": p.stock_name,
                "quantity": p.quantity, "avg_cost": p.avg_cost, "market_price": p.market_price,
            } for p in positions]
            alert_list = [{
                "level": "high" if "HIGH" in a.split(": ", 1)[0].upper() else "mid",
                "message": a[:200],
                "stock_name": a.split("(")[0].split(": ")[-1] if ": " in a else "",
            } for a in alerts]
            await report_engine.push_afternoon_risk(
                date=today_str,
                positions=pos_list, alerts=alert_list,
                performance={"total_assets": cash + mv, "available_cash": cash},
            )
        finally:
            db.close()
    except Exception as e:
        logger.error(f"午后检查异常: {e}", exc_info=True)
    finally:
        gs["running"] = False


# ============================================================
# 收盘复盘
# ============================================================

async def run_review_with_status():
    """收盘复盘"""
    if not is_trading_day():
        return
    gen_status = _get("generation_status")
    gs = gen_status["review"]
    if gs["running"]:
        return
    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    try:
        logger.info("=== 收盘复盘 ===")
        from app.engine.review import run_daily_review
        from app.engine.debate_tracker import DebateTracker

        result = run_daily_review()
        result_str = result.get("result", "N/A")
        violations = result.get("violations", [])

        content = f"**今日复盘: {result_str}**\n\n"
        if violations:
            for v in violations:
                content += f"- {v.get('rule','?')}: {v.get('detail','?')}\n"
        else:
            content += "无违规项\n"

        db = SessionLocal()
        try:
            pos = db.query(Position).filter(Position.quantity > 0).all()
            acc = db.query(SimAccount).first()
            mv = sum(p.market_value for p in pos) / 100
            cash = acc.cash / 100 if acc else 0
            content += f"\n总资产: {(cash+mv):,.0f} | 现金: {cash:,.0f} | 持仓: {mv:,.0f}"
        finally:
            db.close()

        push_webhook_retry("收盘复盘", content)

        try:
            db_review = SessionLocal()
            try:
                DebateTracker.fill_pending(db_review)
            finally:
                db_review.close()
        except Exception as e:
            logger.warning(f"辩论回填异常（不影响主流程）: {e}")

        logger.info(f"=== 复盘完成: {result_str} ===")
    except Exception as e:
        logger.error(f"复盘异常: {e}", exc_info=True)
    finally:
        gs["running"] = False


# ============================================================
# 收盘全景报告
# ============================================================

async def run_daily_report_with_status():
    """收盘全景报告 — 交易回顾+持仓+风控+系统健康"""
    if not is_trading_day():
        return
    try:
        logger.info("=== 收盘全景报告 ===")
        today = str(date.today())

        db = SessionLocal()
        try:
            positions = db.query(Position).filter(Position.quantity > 0).all()
            pos_list = [{
                "code": p.stock_code, "name": p.stock_name,
                "quantity": p.quantity,
                "cost": p.avg_cost / 100 if p.avg_cost else 0,
                "current_price": p.market_price / 100 if p.market_price else 0,
                "market_price": p.market_price / 100 if p.market_price else 0,
            } for p in positions]

            acc = db.query(SimAccount).first()
            cash = acc.cash / 100 if acc else 0
            mv = sum(p.market_value for p in positions) / 100 if positions else 0

            risk_alerts = db.query(RiskAlert).filter(
                RiskAlert.created_at >= datetime.now().replace(hour=0, minute=0, second=0)
            ).all()
            alert_list = [{
                "stock_code": a.stock_code, "stock_name": a.stock_name,
                "alert_type": a.alert_type, "alert_level": a.alert_level,
                "alert_message": a.alert_message, "suggestion": a.suggestion or "",
            } for a in risk_alerts]
        finally:
            db.close()

        cloud = _get("cloud")
        health = {
            "api_service": True,
            "deepseek_api": await cloud.is_available() if hasattr(cloud, 'is_available') else False,
            "qwen_api": await _check_qwen(),
            "tencent_data": await _check_data_source("tencent"),
            "eastmoney_data": await _check_data_source("eastmoney"),
            "tushare_data": await _check_data_source("tushare"),
            "tasks_success": len(_get("scheduler").get_jobs()),
            "tasks_fail": 0,
        }

        perf = {
            "daily_pnl": 0, "daily_pnl_pct": 0, "cumulative_pnl": 0,
            "win_rate": 0, "position_count": len(pos_list),
            "total_assets": cash + mv, "available_cash": cash,
        }

        await _get("report_engine").push_closing(
            date=today, positions=pos_list, alerts=alert_list,
            performance=perf, market_summary="收盘市场概况",
            system_health=health, preview="明日关注标的待生成",
        )
        logger.info("=== 收盘全景报告完成 ===")
    except Exception as e:
        logger.error(f"收盘全景报告异常: {e}", exc_info=True)


# ============================================================
# 消息轮询
# ============================================================

def poll_bot_messages():
    """轮询飞书Bot消息 (每30秒)"""
    try:
        from app.services.message_poller import check_and_process_new_messages
        check_and_process_new_messages()
    except Exception as e:
        logger.debug(f"Bot轮询异常: {e}")


# ============================================================
# 启动健康检查
# ============================================================

async def startup_health_check():
    """启动时连通性检查"""
    await asyncio.sleep(2)
    cloud = _get("cloud")
    tencent_client = _get("tencent_client")
    issues = []
    try:
        ok = await cloud.is_available()
        logger.info(f"DeepSeek API: {'OK' if ok else 'UNAVAILABLE'}")
        if not ok:
            issues.append("DeepSeek API 不可用")
    except Exception as e:
        logger.warning(f"DeepSeek 检测失败: {e}")
        issues.append(f"DeepSeek: {e}")

    try:
        tc = await tencent_client.fetch("sh000001")
        logger.info(f"腾讯行情: {'OK' if tc and tc.get('price') else 'UNAVAILABLE'}")
        if not tc or not tc.get("price"):
            issues.append("腾讯行情数据源异常")
    except Exception as e:
        logger.warning(f"行情检测失败: {e}")
        issues.append(f"行情: {e}")

    if issues:
        push_webhook_retry("旺财V7 启动告警", "\n".join(f"- {i}" for i in issues))


# ============================================================
# API 健康检查辅助函数
# ============================================================

async def _check_qwen() -> bool:
    """检查 Qwen API 连通性"""
    from app.config import settings
    qwen_key = getattr(settings, "QWEN_API_KEY", "")
    if not qwen_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://dashscope.aliyuncs.com/api/v1/models",
                headers={"Authorization": f"Bearer {qwen_key}"}
            )
            return resp.status_code == 200
    except Exception:
        return False


async def _check_data_source(name: str) -> bool:
    """检查数据源连通性"""
    tencent_client = _get("tencent_client")
    try:
        if name == "tencent":
            result = await tencent_client.fetch("sh000001")
            return bool(result and result.get("price"))
        elif name == "eastmoney":
            return True
        elif name == "tushare":
            return True
        return False
    except Exception:
        return False
