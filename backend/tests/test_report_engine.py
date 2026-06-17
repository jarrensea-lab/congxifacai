"""测试报告引擎 — 数据模型构建 + 卡片渲染"""
from datetime import datetime
from app.report_engine.report_schema import ReportData, Recommendation, PositionItem, SystemHealth, PerformanceData
from app.report_engine.renderers.markdown_card import build_premarket_card, build_closing_card, build_midday_card


class TestReportSchema:
    def test_report_data_defaults(self):
        """测试ReportData默认值"""
        data = ReportData(report_type="premarket", date="2026-06-17")
        assert data.report_type == "premarket"
        assert data.risk_level == 3
        assert data.confidence == 5
        assert data.positions == []
        assert data.recommendations == []

    def test_recommendation_full(self):
        """测试推荐标的数据模型"""
        rec = Recommendation(
            code="000970", name="中科三环",
            strategy_type="short_term",
            buy_range="14.00-14.50",
            stop_loss="13.80",
            target="15.50",
            reason="N字突破",
            technical_signals="MACD金叉",
            concept_tags=["稀土", "永磁"],
            trend_score=8,
            beginner_guide="新手可轻仓参与",
            recommend_date="2026-06-17",
        )
        assert rec.code == "000970"
        assert rec.trend_score == 8
        assert "稀土" in rec.concept_tags

    def test_position_item(self):
        """测试持仓数据模型"""
        pos = PositionItem(
            code="000970", name="中科三环",
            quantity=200, cost_price=13.50,
            current_price=14.77, profit_pct=9.4,
            market_value=2954.0, risk_level="normal",
        )
        assert pos.quantity == 200
        assert pos.profit_pct == 9.4
        assert pos.risk_level == "normal"

    def test_system_health_defaults(self):
        """测试系统健康默认值"""
        health = SystemHealth()
        assert health.api_service is False
        assert health.qwen_api is False
        assert health.tasks_success == 0

    def test_performance_data(self):
        """测试绩效数据"""
        perf = PerformanceData(
            daily_pnl=1234.56, daily_pnl_pct=2.34,
            cumulative_pnl=50000.0, win_rate=60.0,
            position_count=3, total_assets=200000.0,
            available_cash=100000.0,
        )
        assert perf.daily_pnl == 1234.56
        assert perf.win_rate == 60.0


class TestMarkdownCard:
    def test_premarket_card_has_risk_section(self):
        """测试盘前卡片包含风险预警板块"""
        data = ReportData(
            report_type="premarket",
            generated_at=datetime.now(),
            date="2026-06-17",
            risk_level=3,
            market_direction="震荡偏多",
            confidence=7,
        )
        card = build_premarket_card(data)
        assert "风险预警" in card
        assert "R3" in card
        assert "市场背景" in card

    def test_premarket_card_with_recommendations(self):
        """测试盘前卡片含推荐标的"""
        data = ReportData(
            report_type="premarket",
            generated_at=datetime.now(),
            date="2026-06-17",
            risk_level=2,
            recommendations=[
                Recommendation(
                    code="000970", name="中科三环",
                    strategy_type="short_term",
                    buy_range="14.00", target="15.50",
                    reason="N字突破", trend_score=8,
                ),
            ],
        )
        card = build_premarket_card(data)
        assert "中科三环" in card
        assert "000970" in card
        assert "短线机会" in card

    def test_premarket_card_with_danger_position(self):
        """测试盘前卡片显示高风险持仓"""
        data = ReportData(
            report_type="premarket",
            generated_at=datetime.now(),
            date="2026-06-17",
            positions=[
                PositionItem(
                    code="600000", name="风险股",
                    quantity=100, cost_price=20.0,
                    current_price=17.0, profit_pct=-15.0,
                    market_value=1700.0, risk_level="danger",
                ),
            ],
        )
        card = build_premarket_card(data)
        assert "🔴" in card
        assert "风险股" in card

    def test_closing_card_with_performance(self):
        """测试收盘卡片含绩效数据"""
        data = ReportData(
            report_type="closing",
            generated_at=datetime.now(),
            date="2026-06-17",
            positions=[
                PositionItem(
                    code="000970", name="中科三环",
                    quantity=200, cost_price=13.50,
                    current_price=14.77, profit_pct=9.4,
                    market_value=2954.0,
                ),
            ],
            performance=PerformanceData(
                daily_pnl=1234.56, daily_pnl_pct=2.34,
                cumulative_pnl=56789.0, position_count=3,
                total_assets=123456.0, available_cash=50000.0,
            ),
        )
        card = build_closing_card(data)
        assert "+1,234.56" in card or "1234.56" in card
        assert "中科三环" in card
        assert "收盘全景" in card or "📊" in card

    def test_midday_card_basic(self):
        """测试午盘快报卡片"""
        data = ReportData(
            report_type="midday",
            generated_at=datetime.now(),
            date="2026-06-17",
            market_summary="上午市场震荡上行，成交量放大",
            positions=[
                PositionItem(
                    code="000970", name="中科三环",
                    quantity=200, cost_price=13.50,
                    current_price=14.50, profit_pct=7.4,
                    market_value=2900.0,
                ),
            ],
            knowledge_tip="下午关注3350点支撑",
        )
        card = build_midday_card(data)
        assert "午盘快报" in card
        assert "中科三环" in card
        assert "下午策略" in card


class TestBitableWriter:
    def test_writer_creates_correct_fields(self):
        """测试多维表格字段构造"""
        from app.report_engine.renderers.bitable_writer import BitableWriter
        writer = BitableWriter()
        # 验证基础属性
        assert writer.lark_cli == "/Users/zhuchenyuan/.npm-global/bin/lark-cli"
        assert hasattr(writer, "app_token")
        assert "strategy" in writer._tables
        assert "stock_pool" in writer._tables
        assert "positions" in writer._tables
        assert "indices" in writer._tables
        assert "risk" in writer._tables
        assert "performance" in writer._tables


class TestFeishuDoc:
    def test_upload_function_exists(self):
        """测试飞书上传函数可导入"""
        from app.report_engine.renderers.feishu_doc import (
            upload_image_to_drive, create_doc_from_markdown, create_doc_with_image
        )
        assert callable(upload_image_to_drive)
        assert callable(create_doc_from_markdown)
        assert callable(create_doc_with_image)


class TestPushTracker:
    """测试推送状态追踪器"""


    def test_compute_retry_delay_increasing(self):
        """指数退避延迟随重试次数递增"""
        from app.services.push_tracker import compute_retry_delay
        d1 = compute_retry_delay(1, base_delay=10, max_delay=600)
        d2 = compute_retry_delay(2, base_delay=10, max_delay=600)
        d3 = compute_retry_delay(3, base_delay=10, max_delay=600)
        assert d2 > d1  # 2^1=20 > 10
        assert d3 > d2  # 2^2=40 > 20

    def test_compute_retry_delay_max_cap(self):
        """延迟不超过 max_delay"""
        from app.services.push_tracker import compute_retry_delay
        d = compute_retry_delay(10, base_delay=10, max_delay=60)
        assert d <= 66  # 60 + 10% jitter

    def test_compute_retry_delay_has_jitter(self):
        """两次相同参数的结果带抖动（大概率不等）"""
        from app.services.push_tracker import compute_retry_delay
        results = {compute_retry_delay(1, base_delay=30, max_delay=600) for _ in range(5)}
        assert len(results) > 1  # 抖动导致结果不全相同

    def test_push_tracker_record_and_mark(self):
        """记录推送 → 标记成功 全流程"""
        from app.services.push_tracker import push_tracker
        from app.database import SessionLocal
        from app.models import PushRecord

        record_id = push_tracker.record(
            push_type="test", push_date="2026-06-17", status="pending", max_retries=2,
        )
        assert record_id is not None

        db = SessionLocal()
        try:
            record = db.query(PushRecord).filter(PushRecord.id == record_id).first()
            assert record is not None
            assert record.push_type == "test"
            assert record.status == "pending"
            assert record.max_retries == 2

            ok = push_tracker.mark_success(record_id)
            assert ok is True
            db.refresh(record)
            assert record.status == "success"
        finally:
            db.query(PushRecord).filter(PushRecord.id == record_id).delete()
            db.commit()
            db.close()

    def test_push_tracker_mark_failed_and_retry_count(self):
        """标记失败 → 重试计数增加"""
        from app.services.push_tracker import push_tracker
        from app.database import SessionLocal
        from app.models import PushRecord

        record_id = push_tracker.record(
            push_type="test_fail", push_date="2026-06-17", status="pending", max_retries=3,
        )
        assert record_id is not None

        push_tracker.mark_failed(record_id, "第一次超时")
        push_tracker.mark_failed(record_id, "第二次超时")

        db = SessionLocal()
        try:
            record = db.query(PushRecord).filter(PushRecord.id == record_id).first()
            assert record.status == "failed"
            assert record.retry_count == 2
        finally:
            db.query(PushRecord).filter(PushRecord.id == record_id).delete()
            db.commit()
            db.close()

    def test_get_today_summary_format(self):
        """获取今日推送摘要格式正确"""
        from app.services.push_tracker import push_tracker
        from app.database import SessionLocal
        from app.models import PushRecord

        db = SessionLocal()
        try:
            r1 = PushRecord(push_type="premarket", push_date="2026-06-17", status="success")
            r2 = PushRecord(push_type="midday", push_date="2026-06-17", status="failed",
                            error_message="连接超时", retry_count=2)
            db.add(r1); db.add(r2); db.commit()

            summary = push_tracker.get_today_summary("2026-06-17")
            assert "今日推送状态" in summary
            assert "成功" in summary
            assert "失败" in summary

            db.query(PushRecord).filter(PushRecord.push_date == "2026-06-17").delete()
            db.commit()
        finally:
            db.close()
