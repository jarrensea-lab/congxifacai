"""策略生命周期引擎数据模型"""
from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON, Date, ForeignKey, Boolean
from app.database import Base


class AIStrategy(Base):
    """AI 策略表"""
    __tablename__ = "ai_strategies"

    id = Column(Integer, primary_key=True, index=True)
    strategy_type = Column(String(20), comment="类型：premarket/review")
    content = Column(Text, comment="策略内容")
    recommended_stocks = Column(JSON, comment="推荐股票列表")
    generated_code = Column(Text, nullable=True, comment="AI生成的完整策略代码")
    code_version = Column(Integer, default=1, comment="代码版本号")
    code_status = Column(String(20), default="draft", comment="代码状态: draft/validated/deployed/archived")
    strategy_instance_id = Column(Integer, ForeignKey("strategy_instances.id"), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=datetime.now, comment="时间戳")

    def to_dict(self):
        return {
            "id": self.id,
            "strategy_type": self.strategy_type,
            "content": self.content,
            "recommended_stocks": self.recommended_stocks,
            "generated_code": self.generated_code,
            "code_version": self.code_version,
            "code_status": self.code_status,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }


class StrategyInstance(Base):
    """策略实例 — 策略生命周期核心模型"""
    __tablename__ = "strategy_instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    status = Column(String(20), default="draft")
    # draft → confirmed → planned → executing → completed → reviewed

    # 风险等级 R1-R5
    risk_level = Column(Integer, default=3)

    # 决策参数
    position_limit_pct = Column(Float, default=30.0)
    single_stock_limit_pct = Column(Float, default=15.0)
    stop_loss_pct = Column(Float, default=-5.0)
    holding_period_days = Column(Integer, default=5)

    # 标的池 JSON: [{"code":"000001","name":"平安银行","weight":0.3},...]
    stock_pool = Column(JSON)

    # 各阶段输出
    analysis_report = Column(JSON)      # ① 分析研判输出
    debate_summary = Column(JSON)       # ② 策略工坊辩论摘要
    execution_plan = Column(JSON)       # ③ 执行规划输出

    # 绩效数据
    expected_return_best = Column(Float)
    expected_return_neutral = Column(Float)
    expected_return_worst = Column(Float)
    actual_return = Column(Float, nullable=True)
    review_notes = Column(Text, nullable=True)


class ReviewLog(Base):
    """每日审查日志"""
    __tablename__ = "review_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_instance_id = Column(Integer, ForeignKey("strategy_instances.id"), nullable=True)
    review_date = Column(Date, default=date.today)
    result = Column(String(20), default="pass")  # pass / yellow / red / breaker
    violations = Column(JSON)  # [{"rule":"仓位超限","detail":"..."}]


class DebateResult(Base):
    """辩论质量追踪 — 记录五角色快照与回填实际收益"""
    __tablename__ = "debate_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_type = Column(String(20), comment="盘前/午盘/复盘")
    debated_at = Column(DateTime, default=datetime.now)
    market_condition = Column(String(20), comment="trending_up/ranging/trending_down")

    # 五角色快照
    hunter_decision = Column(String(10), comment="buy/sell/hold")
    hunter_conviction = Column(Integer, default=0)
    accountant_decision = Column(String(10))
    accountant_conviction = Column(Integer, default=0)
    guardian_risk_level = Column(Integer, comment="R1-R5")
    guardian_risk_appetite = Column(String(10))
    judge_decision = Column(String(10))
    judge_confidence = Column(Integer, default=5)
    researcher_decision = Column(String(10), comment="买入/卖出/持有/观望")
    researcher_conviction = Column(Integer, default=0, comment="信心 1-10")

    # 推荐标的
    short_term_codes = Column(JSON, comment="短线推荐代码列表")
    mid_term_codes = Column(JSON, comment="中线推荐代码列表")

    # 回填数据
    short_term_return_5d = Column(Float, nullable=True, comment="短线推荐5日平均收益率")
    mid_term_return_20d = Column(Float, nullable=True, comment="中线推荐20日平均收益率")
    judge_direction_correct = Column(Boolean, nullable=True, comment="裁判方向判断是否正确")
    result_filled_at = Column(DateTime, nullable=True, comment="回填完成时间")
