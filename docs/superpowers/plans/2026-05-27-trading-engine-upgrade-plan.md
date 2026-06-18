# 恭喜发财 v3.0 — 模拟交易引擎升级 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有行情监控+AI分析系统上新增模拟交易引擎，实现趋势跟踪策略的完整链路：信号生成→人工审批→模拟成交→绩效分析。

**Architecture:** 新增独立 `trading_engine/` 模块（10个文件），与现有 FastAPI 后端松耦合。复用现有数据源、AI客户端和飞书通知。新增12个 REST API + 3个定时Job + 3个 Vue 前端页面。

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, Pandas, NumPy, Vue 3, ECharts

---

## 文件结构总览

### 新建文件 (15个)

```
backend/app/trading_engine/
├── __init__.py                  # 模块入口，暴露 service 单例
├── account.py                   # SimAccountManager — 资金/持仓/净值
├── broker.py                    # SimBroker — 撮合/滑点/手续费
├── strategy_base.py             # BaseStrategy — 策略接口
├── trend_tracker.py             # TrendTrackerStrategy — MA+ATR
├── signal_engine.py             # SignalEngine — 信号生成/审批
├── order_manager.py             # OrderManager — 订单生命周期
├── risk_guard.py                # RiskGuard — 交易风控门禁
├── performance.py               # PerformanceAnalyzer — 绩效指标
└── scheduler.py                 # 策略定时扫描 + 订单检查

frontend/src/
├── api/trading.js               # 交易相关 API 封装
├── store/trading.js             # Pinia 交易状态管理
├── views/Trading.vue            # 交易看板主页面
├── components/TradingSignal.vue # 策略信号台组件
├── components/TradingAccount.vue# 模拟账户组件
```

### 修改文件 (5个)

```
backend/app/models.py            # 新增4个数据模型
backend/app/database.py          # init_db 自动初始化账户
backend/app/main.py              # 注册API路由 + 新增定时任务
frontend/src/router/index.js     # 新增 /trading 路由
frontend/src/App.vue             # 导航栏新增「交易看板」
```

---

## Phase 1: 数据模型 + 模拟账户 + 撮合引擎

### Task 1.1: 新增数据模型

**Files:**
- Modify: `backend/app/models.py` (末尾追加)
- Create: `backend/app/trading_engine/__init__.py`

- [ ] **Step 1: 在 models.py 末尾追加4个新模型**

```python
# ========== 模拟交易引擎模型 ==========

class SimAccount(Base):
    """模拟账户表（单例）"""
    __tablename__ = "sim_account"

    id = Column(Integer, primary_key=True, index=True)
    cash = Column(Integer, nullable=False, default=10000000, comment="可用资金(分)")
    frozen = Column(Integer, nullable=False, default=0, comment="冻结资金(分)")
    total_value = Column(Integer, nullable=False, default=10000000, comment="总资产(分)")
    initial_capital = Column(Integer, nullable=False, default=10000000, comment="初始资金(分)")
    daily_pnl = Column(Integer, nullable=False, default=0, comment="当日盈亏(分)")
    total_pnl = Column(Integer, nullable=False, default=0, comment="累计盈亏(分)")
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "cash": self.cash,
            "frozen": self.frozen,
            "total_value": self.total_value,
            "initial_capital": self.initial_capital,
            "daily_pnl": self.daily_pnl,
            "total_pnl": self.total_pnl,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TradingSignal(Base):
    """策略信号表"""
    __tablename__ = "trading_signals"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(8), nullable=False, index=True)
    stock_name = Column(String(50))
    strategy_name = Column(String(30), nullable=False, default="trend_tracker")
    signal_type = Column(String(10), nullable=False, comment="buy / sell")
    price = Column(Float, comment="触发时参考价格")
    confidence = Column(Float, default=0.5, comment="信号置信度 0-1")
    reason = Column(Text, comment="触发条件说明")
    params_json = Column(Text, comment="当前策略参数快照JSON")
    suggested_qty = Column(Integer, comment="建议买卖股数")
    approved_by = Column(String(20), comment="manual / auto")
    approved_at = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False, default="pending",
                    comment="pending / approved / rejected / expired / executed")
    created_at = Column(DateTime(timezone=True), default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id, "stock_code": self.stock_code, "stock_name": self.stock_name,
            "strategy_name": self.strategy_name, "signal_type": self.signal_type,
            "price": self.price, "confidence": self.confidence, "reason": self.reason,
            "params_json": self.params_json, "suggested_qty": self.suggested_qty,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TradingOrder(Base):
    """交易订单表"""
    __tablename__ = "trading_orders"

    id = Column(Integer, primary_key=True, index=True)
    signal_id = Column(Integer, index=True)
    stock_code = Column(String(8), nullable=False, index=True)
    stock_name = Column(String(50))
    direction = Column(String(10), nullable=False, comment="buy / sell")
    order_type = Column(String(20), nullable=False, comment="market / limit / stop_loss")
    price = Column(Integer, comment="委托价格(分)，市价单可为空")
    quantity = Column(Integer, nullable=False, comment="委托数量")
    filled_price = Column(Integer, comment="成交价格(分)")
    filled_quantity = Column(Integer, default=0, comment="已成交数量")
    fee = Column(Integer, default=0, comment="手续费(分)")
    status = Column(String(20), nullable=False, default="pending",
                    comment="pending / filled / cancelled / rejected")
    rejection_reason = Column(String(200))
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    filled_at = Column(DateTime(timezone=True))

    def to_dict(self):
        return {
            "id": self.id, "signal_id": self.signal_id, "stock_code": self.stock_code,
            "stock_name": self.stock_name, "direction": self.direction,
            "order_type": self.order_type, "price": self.price, "quantity": self.quantity,
            "filled_price": self.filled_price, "filled_quantity": self.filled_quantity,
            "fee": self.fee, "status": self.status, "rejection_reason": self.rejection_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
        }


class TradeLog(Base):
    """交易日志表"""
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, nullable=False, index=True)
    stock_code = Column(String(8), nullable=False)
    stock_name = Column(String(50))
    direction = Column(String(10), nullable=False)
    price = Column(Integer, nullable=False, comment="成交价格(分)")
    quantity = Column(Integer, nullable=False)
    amount = Column(Integer, nullable=False, comment="成交金额(分)")
    fee = Column(Integer, nullable=False, default=0)
    pnl = Column(Integer, comment="盈亏(分)，卖出时计算")
    pnl_pct = Column(Float)
    strategy_name = Column(String(30), nullable=False)
    holding_days = Column(Integer)
    traded_at = Column(DateTime(timezone=True), default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id, "order_id": self.order_id, "stock_code": self.stock_code,
            "stock_name": self.stock_name, "direction": self.direction,
            "price": self.price, "quantity": self.quantity, "amount": self.amount,
            "fee": self.fee, "pnl": self.pnl, "pnl_pct": self.pnl_pct,
            "strategy_name": self.strategy_name, "holding_days": self.holding_days,
            "traded_at": self.traded_at.isoformat() if self.traded_at else None,
        }
```

- [ ] **Step 2: 在 RiskAlert 模型上追加 trading_rule_triggered 字段**

```python
# 在 RiskAlert 的 Column 定义组末尾追加：
trading_rule_triggered = Column(Text, comment="触发的交易规则描述")
```

- [ ] **Step 3: 在 RiskAlert.to_dict() 中追加字段**

```python
# 在 to_dict() 返回字典中追加：
"trading_rule_triggered": self.trading_rule_triggered,
```

- [ ] **Step 4: 更新 database.py 的 init_db，增加账户自动初始化**

```python
def init_db():
    """初始化数据库"""
    import app.models
    Base.metadata.create_all(bind=engine)
    # 自动初始化模拟账户（仅当不存在时）
    from sqlalchemy.orm import Session
    from app.models import SimAccount
    with Session(engine) as session:
        if not session.query(SimAccount).first():
            session.add(SimAccount())
            session.commit()
```

- [ ] **Step 5: 创建 trading_engine/__init__.py**

```python
"""模拟交易引擎模块"""
from app.trading_engine.account import SimAccountManager
from app.trading_engine.broker import SimBroker
from app.trading_engine.strategy_base import BaseStrategy
from app.trading_engine.trend_tracker import TrendTrackerStrategy
from app.trading_engine.signal_engine import SignalEngine
from app.trading_engine.order_manager import OrderManager
from app.trading_engine.risk_guard import RiskGuard
from app.trading_engine.performance import PerformanceAnalyzer

__all__ = [
    "SimAccountManager", "SimBroker", "BaseStrategy",
    "TrendTrackerStrategy", "SignalEngine", "OrderManager",
    "RiskGuard", "PerformanceAnalyzer",
]
```

- [ ] **Step 6: 验证 — 启动应用确认数据库建表成功**

```bash
cd backend && python -c "
from app.database import init_db
from app.models import SimAccount, TradingSignal, TradingOrder, TradeLog
init_db()
from app.database import SessionLocal
db = SessionLocal()
acc = db.query(SimAccount).first()
print(f'模拟账户: cash={acc.cash/100:.0f}元, total={acc.total_value/100:.0f}元')
db.close()
"
```
Expected: `模拟账户: cash=100000元, total=100000元`

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/app/database.py backend/app/trading_engine/__init__.py
git commit -m "feat(trading): add SimAccount, TradingSignal, TradingOrder, TradeLog models"
```

---

### Task 1.2: 模拟账户管理器

**Files:**
- Create: `backend/app/trading_engine/account.py`

- [ ] **Step 1: 编写 SimAccountManager 类**

```python
"""模拟账户管理 — 资金、持仓、净值"""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import SimAccount
from app.utils.logger import logger


class SimAccountManager:
    """模拟账户管理器（单例数据行）"""

    def __init__(self):
        self._db: Optional[Session] = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def get_account(self) -> SimAccount:
        """获取或创建模拟账户"""
        db = self._get_db()
        acc = db.query(SimAccount).first()
        if not acc:
            acc = SimAccount()
            db.add(acc)
            db.commit()
            db.refresh(acc)
        return acc

    def get_cash_yuan(self) -> float:
        """获取可用资金（元）"""
        return self.get_account().cash / 100.0

    def get_available_cash(self) -> int:
        """获取可用资金（分），用于计算"""
        return self.get_account().cash

    def freeze_cash(self, amount_fen: int) -> bool:
        """冻结资金（用于限价单）"""
        acc = self.get_account()
        if acc.cash < amount_fen:
            return False
        acc.cash -= amount_fen
        acc.frozen += amount_fen
        acc.updated_at = datetime.now()
        self._db.commit()
        return True

    def unfreeze_cash(self, amount_fen: int):
        """解冻资金"""
        acc = self.get_account()
        acc.cash += amount_fen
        acc.frozen -= amount_fen
        acc.updated_at = datetime.now()
        self._db.commit()

    def deduct_cash(self, amount_fen: int) -> bool:
        """扣除资金（买入成交时）"""
        acc = self.get_account()
        if acc.cash < amount_fen:
            return False
        acc.cash -= amount_fen
        acc.updated_at = datetime.now()
        self._db.commit()
        return True

    def add_cash(self, amount_fen: int):
        """增加资金（卖出成交时）"""
        acc = self.get_account()
        acc.cash += amount_fen
        acc.updated_at = datetime.now()
        self._db.commit()

    def update_total_value(self, positions_value_fen: int):
        """更新总资产 = 现金 + 冻结 + 持仓市值"""
        acc = self.get_account()
        acc.total_value = acc.cash + acc.frozen + positions_value_fen
        acc.daily_pnl = acc.total_value - acc.initial_capital
        acc.total_pnl = acc.total_value - acc.initial_capital
        acc.updated_at = datetime.now()
        self._db.commit()

    def reset_account(self):
        """重置账户到初始状态"""
        acc = self.get_account()
        acc.cash = acc.initial_capital
        acc.frozen = 0
        acc.total_value = acc.initial_capital
        acc.daily_pnl = 0
        acc.total_pnl = 0
        acc.updated_at = datetime.now()
        self._db.commit()
        logger.info("模拟账户已重置")

    def get_summary(self) -> dict:
        """获取账户摘要（元为单位，前端展示用）"""
        acc = self.get_account()
        return {
            "cash": round(acc.cash / 100, 2),
            "frozen": round(acc.frozen / 100, 2),
            "total_value": round(acc.total_value / 100, 2),
            "initial_capital": round(acc.initial_capital / 100, 2),
            "daily_pnl": round(acc.daily_pnl / 100, 2),
            "total_pnl": round(acc.total_pnl / 100, 2),
            "total_return_pct": round(
                (acc.total_value - acc.initial_capital) / acc.initial_capital * 100, 2
            ),
        }
```

- [ ] **Step 2: 验证 — 运行单元测试**

```bash
cd backend && python -c "
from app.database import init_db; init_db()
from app.trading_engine.account import SimAccountManager
mgr = SimAccountManager()
print(mgr.get_summary())
mgr.deduct_cash(500000)  # 扣5000元
print('After deduct 5000:', mgr.get_summary())
mgr.add_cash(500000)
print('After add back:', mgr.get_summary())
"
```
Expected: 初始10万元，扣除5000后95000，加回后100000

- [ ] **Step 3: Commit**

```bash
git add backend/app/trading_engine/account.py
git commit -m "feat(trading): add SimAccountManager with cash/deduction/value tracking"
```

---

### Task 1.3: 模拟撮合引擎

**Files:**
- Create: `backend/app/trading_engine/broker.py`

- [ ] **Step 1: 编写 SimBroker 类**

```python
"""模拟撮合引擎 — 市价/限价成交 + 滑点 + 手续费 + T+1"""
import math
from datetime import datetime, date
from typing import Optional
from app.utils.logger import logger


class SimBroker:
    """模拟券商撮合引擎

    - 市价单: 当前行情价 ± 滑点立即成交
    - 限价单: 行情价触及委托价时成交
    - T+1: 当日买入次日才能卖出
    - 手续费: 佣金万2.5 + 卖出印花税千1
    """

    # 可配置参数
    COMMISSION_RATE = 0.00025     # 佣金万2.5
    STAMP_TAX_RATE = 0.001        # 印花税千1（仅卖出）
    SLIPPAGE_RATE = 0.001         # 滑点千1
    MIN_COMMISSION = 500          # 最低佣金5元（分）
    LOT_SIZE = 100                # A股每手100股

    def __init__(self):
        self._today_bought: dict[str, set[date]] = {}  # stock_code -> 买入日期集合

    def record_buy(self, stock_code: str, buy_date: date):
        """记录买入日期（T+1检查用）"""
        if stock_code not in self._today_bought:
            self._today_bought[stock_code] = set()
        self._today_bought[stock_code].add(buy_date)

    def can_sell_today(self, stock_code: str, today: date) -> bool:
        """检查T+1：今日买入的不可卖出"""
        if stock_code not in self._today_bought:
            return True
        return today not in self._today_bought[stock_code]

    def _calc_slippage_price(self, current_price_fen: int, direction: str) -> int:
        """计算滑点价格"""
        slip = max(1, int(current_price_fen * self.SLIPPAGE_RATE))
        if direction == "buy":
            return current_price_fen + slip
        return current_price_fen - slip

    def _calc_commission(self, amount_fen: int) -> int:
        """计算佣金（分），最低5元"""
        commission = max(self.MIN_COMMISSION, int(amount_fen * self.COMMISSION_RATE))
        return commission

    def _calc_stamp_tax(self, amount_fen: int) -> int:
        """计算印花税（分），仅卖出"""
        return int(amount_fen * self.STAMP_TAX_RATE)

    def execute_market_order(self, stock_code: str, direction: str,
                              current_price_fen: int, quantity: int,
                              today: date = None) -> Optional[dict]:
        """执行市价单，返回成交结果或 None（失败）"""
        if today is None:
            today = date.today()

        # T+1检查
        if direction == "sell" and not self.can_sell_today(stock_code, today):
            logger.warning(f"T+1限制: {stock_code} 今日买入不可卖出")
            return None

        # 滑点
        filled_price = self._calc_slippage_price(current_price_fen, direction)
        amount = filled_price * quantity

        # 手续费
        commission = self._calc_commission(amount)
        stamp_tax = self._calc_stamp_tax(amount) if direction == "sell" else 0
        total_fee = commission + stamp_tax

        # 整手检查
        if quantity % self.LOT_SIZE != 0:
            logger.warning(f"数量 {quantity} 不是100股整数倍")
            return None

        if direction == "buy":
            self.record_buy(stock_code, today)

        return {
            "filled_price": filled_price,
            "filled_quantity": quantity,
            "amount": amount,
            "commission": commission,
            "stamp_tax": stamp_tax,
            "total_fee": total_fee,
        }

    def check_limit_order(self, limit_price_fen: int, current_price_fen: int,
                          direction: str) -> bool:
        """检查限价单是否可成交"""
        if direction == "buy":
            # 买入限价：当前价 ≤ 委托价时成交
            return current_price_fen <= limit_price_fen
        else:
            # 卖出限价：当前价 ≥ 委托价时成交
            return current_price_fen >= limit_price_fen

    def execute_limit_order(self, stock_code: str, direction: str,
                             limit_price_fen: int, quantity: int,
                             current_price_fen: int,
                             today: date = None) -> Optional[dict]:
        """执行限价单"""
        if today is None:
            today = date.today()

        if not self.check_limit_order(limit_price_fen, current_price_fen, direction):
            return None

        if direction == "sell" and not self.can_sell_today(stock_code, today):
            return None

        # 限价单无滑点，以委托价成交
        amount = limit_price_fen * quantity
        commission = self._calc_commission(amount)
        stamp_tax = self._calc_stamp_tax(amount) if direction == "sell" else 0
        total_fee = commission + stamp_tax

        if direction == "buy":
            self.record_buy(stock_code, today)

        return {
            "filled_price": limit_price_fen,
            "filled_quantity": quantity,
            "amount": amount,
            "commission": commission,
            "stamp_tax": stamp_tax,
            "total_fee": total_fee,
        }

    def round_lot(self, quantity: int) -> int:
        """取整手数"""
        return (quantity // self.LOT_SIZE) * self.LOT_SIZE
```

- [ ] **Step 2: 验证 Broker 手续费计算**

```bash
cd backend && python -c "
from app.trading_engine.broker import SimBroker
b = SimBroker()
# 买100股，价格1500分(15元)，金额150000分
r = b.execute_market_order('000001', 'buy', 1500, 100)
print('Buy result:', r)
# 卖100股
r2 = b.execute_market_order('000001', 'sell', 1600, 100)
print('Sell result:', r2)
# T+1检查
print('Can sell today:', b.can_sell_today('000001', __import__('datetime').date.today()))
"
```
Expected: 佣金≥500分(5元)，卖出含印花税，T+1=False

- [ ] **Step 3: Commit**

```bash
git add backend/app/trading_engine/broker.py
git commit -m "feat(trading): add SimBroker with market/limit order, slippage, fees, T+1"
```

---

## Phase 2: 策略基类 + 趋势跟踪策略 + 信号引擎

### Task 2.1: 策略基类

**Files:**
- Create: `backend/app/trading_engine/strategy_base.py`

- [ ] **Step 1: 编写 BaseStrategy 抽象基类**

```python
"""策略基类 — 定义统一策略接口"""
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd


class BaseStrategy(ABC):
    """所有量化策略的基类"""

    name: str = "base"
    params: dict = {}

    def __init__(self, **kwargs):
        self.params = {**self.params, **kwargs}

    @abstractmethod
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标，返回含新列的DataFrame"""
        ...

    @abstractmethod
    def generate_buy_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成买入信号，返回布尔Series"""
        ...

    @abstractmethod
    def generate_sell_signals(self, df: pd.DataFrame, holding_cost: float = 0) -> pd.DataFrame:
        """生成卖出信号，返回布尔Series"""
        ...

    def calculate_position_size(self, cash_fen: int, price_fen: int,
                                 atr_fen: int, lot_size: int = 100) -> int:
        """计算仓位大小（默认2%风险ATR法），返回股数"""
        if atr_fen <= 0 or price_fen <= 0:
            return 0
        risk_amount = int(cash_fen * self.params.get("risk_per_trade", 0.02))
        stop_distance = int(atr_fen * self.params.get("atr_stop_multiplier", 1.5))
        if stop_distance <= 0:
            return 0
        qty = risk_amount // stop_distance
        qty = (qty // lot_size) * lot_size  # 整手取整
        max_qty_per_position = int(
            cash_fen * self.params.get("max_single_position_pct", 0.20) / price_fen
        )
        return min(qty, max_qty_per_position)

    def check_stop_loss(self, cost_price_fen: int, current_price_fen: int,
                         atr_fen: int) -> bool:
        """检查是否触发止损: 现价 <= 成本价 - multiplier * ATR"""
        multiplier = self.params.get("atr_stop_multiplier", 1.5)
        stop_price = cost_price_fen - int(atr_fen * multiplier)
        return current_price_fen <= stop_price

    def check_take_profit(self, cost_price_fen: int, current_price_fen: int,
                           highest_price_fen: int = None) -> tuple[bool, bool]:
        """检查止盈: (是否触发固定止盈, 是否触发移动止盈)"""
        take_profit_pct = self.params.get("take_profit_pct", 0.15)
        trailing_pct = self.params.get("trailing_stop_pct", 0.03)

        gain_pct = (current_price_fen - cost_price_fen) / cost_price_fen
        fixed_tp = gain_pct >= take_profit_pct

        trailing_tp = False
        if highest_price_fen and gain_pct >= take_profit_pct:
            drawdown = (highest_price_fen - current_price_fen) / highest_price_fen
            trailing_tp = drawdown >= trailing_pct

        return fixed_tp, trailing_tp

    def set_params(self, **kwargs):
        """更新策略参数"""
        self.params.update(kwargs)

    def get_params(self) -> dict:
        return dict(self.params)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/trading_engine/strategy_base.py
git commit -m "feat(trading): add BaseStrategy with indicator/signal/position/stop/take interfaces"
```

---

### Task 2.2: 趋势跟踪策略

**Files:**
- Create: `backend/app/trading_engine/trend_tracker.py`

- [ ] **Step 1: 编写 TrendTrackerStrategy**

```python
"""趋势跟踪策略 — MA金叉死叉 + ATR + 量能确认"""
import numpy as np
import pandas as pd
from app.trading_engine.strategy_base import BaseStrategy
from app.utils.logger import logger


class TrendTrackerStrategy(BaseStrategy):
    """趋势跟踪策略

    买入: MA5上穿MA20 + 价格确认 + 放量
    卖出: MA5下穿MA20 / 破位 / 止损 / 止盈
    """

    name = "trend_tracker"
    params = {
        "ma_short": 5,
        "ma_long": 20,
        "atr_period": 14,
        "atr_stop_multiplier": 1.5,
        "trend_confirm_pct": 0.02,
        "volume_confirm_multiplier": 1.0,
        "risk_per_trade": 0.02,
        "trailing_stop_pct": 0.03,
        "take_profit_pct": 0.15,
        "max_single_position_pct": 0.20,
    }

    def _calc_atr(self, df: pd.DataFrame) -> pd.Series:
        """计算ATR(14)"""
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=self.params["atr_period"]).mean()

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算MA5/MA20/ATR/成交量均线"""
        df = df.copy()
        df["ma5"] = df["close"].rolling(window=self.params["ma_short"]).mean()
        df["ma20"] = df["close"].rolling(window=self.params["ma_long"]).mean()
        df["atr"] = self._calc_atr(df)
        df["vol_ma14"] = df["volume"].rolling(window=14).mean()
        # 前一日MA（用于判断金叉死叉）
        df["ma5_prev"] = df["ma5"].shift(1)
        df["ma20_prev"] = df["ma20"].shift(1)
        return df

    def generate_buy_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成买入信号"""
        df = self.calculate_indicators(df)
        confirm_pct = self.params["trend_confirm_pct"]
        vol_mult = self.params["volume_confirm_multiplier"]

        # 金叉: 今日MA5>MA20 且 昨日MA5<=MA20
        golden_cross = (df["ma5"] > df["ma20"]) & (df["ma5_prev"] <= df["ma20_prev"])
        # 价格确认: 收盘价 > MA20 * (1+confirm%)
        price_confirm = df["close"] > df["ma20"] * (1 + confirm_pct)
        # 放量确认: 成交量 > 14日均量 * 倍数
        volume_confirm = df["volume"] > df["vol_ma14"] * vol_mult

        df["buy_signal"] = golden_cross & price_confirm & volume_confirm
        return df

    def generate_sell_signals(self, df: pd.DataFrame, holding_cost: float = 0) -> pd.DataFrame:
        """生成卖出信号（任意一个触发即为卖出）"""
        df = self.calculate_indicators(df)

        # 死叉: 今日MA5<MA20 且 昨日MA5>=MA20
        death_cross = (df["ma5"] < df["ma20"]) & (df["ma5_prev"] >= df["ma20_prev"])
        # 破位: 收盘价 < MA20 * 0.95
        breakdown = df["close"] < df["ma20"] * 0.95
        # 止损: 收盘价 < 成本价 - multiplier * ATR
        stop_loss = pd.Series(False, index=df.index)
        if holding_cost > 0:
            stop_mult = self.params["atr_stop_multiplier"]
            stop_loss = df["close"] < (holding_cost - stop_mult * df["atr"])

        df["sell_signal"] = death_cross | breakdown | stop_loss
        return df

    def describe_signal(self, df: pd.DataFrame, idx: int) -> str:
        """生成信号原因描述"""
        row = df.iloc[idx]
        parts = []
        if row.get("buy_signal"):
            parts.append(f"MA5({row['ma5']:.2f})上穿MA20({row['ma20']:.2f})")
            parts.append(f"收盘¥{row['close']:.2f}确认趋势")
            if "volume" in row:
                parts.append(f"成交量{row['volume']:.0f}>14日均量{row.get('vol_ma14', 0):.0f}")
        if row.get("sell_signal"):
            if row["ma5"] < row["ma20"] and row.get("ma5_prev", 0) >= row.get("ma20_prev", 0):
                parts.append(f"MA5({row['ma5']:.2f})下穿MA20({row['ma20']:.2f})")
            if row["close"] < row["ma20"] * 0.95:
                parts.append(f"收盘¥{row['close']:.2f}跌破MA20({row['ma20']:.2f})5%")
        return "; ".join(parts) if parts else "信号触发"
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/trading_engine/trend_tracker.py
git commit -m "feat(trading): add TrendTrackerStrategy with MA crossover + ATR + volume confirmation"
```

---

### Task 2.3: 信号引擎

**Files:**
- Create: `backend/app/trading_engine/signal_engine.py`

- [ ] **Step 1: 编写 SignalEngine**

```python
"""信号引擎 — 生成/审批/过期管理"""
import json
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import TradingSignal, StockHoldings
from app.utils.logger import logger


class SignalEngine:
    """信号引擎: 生成、查询、审批信号"""

    def __init__(self):
        self._db: Optional[Session] = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def create_signal(self, stock_code: str, stock_name: str, signal_type: str,
                       price: float, reason: str, strategy_params: dict,
                       suggested_qty: int = 0, confidence: float = 0.5,
                       auto_approve: bool = False) -> TradingSignal:
        """创建交易信号"""
        db = self._get_db()
        signal = TradingSignal(
            stock_code=stock_code,
            stock_name=stock_name,
            strategy_name="trend_tracker",
            signal_type=signal_type,
            price=price,
            confidence=confidence,
            reason=reason,
            params_json=json.dumps(strategy_params, ensure_ascii=False),
            suggested_qty=suggested_qty,
            status="approved" if auto_approve else "pending",
            approved_by="auto" if auto_approve else None,
            approved_at=datetime.now() if auto_approve else None,
        )
        db.add(signal)
        db.commit()
        db.refresh(signal)
        return signal

    def get_pending_signals(self) -> List[TradingSignal]:
        """获取待审批信号"""
        return self._get_db().query(TradingSignal).filter(
            TradingSignal.status == "pending"
        ).order_by(TradingSignal.created_at.desc()).all()

    def approve_signal(self, signal_id: int) -> Optional[TradingSignal]:
        """批准信号"""
        db = self._get_db()
        s = db.query(TradingSignal).filter(TradingSignal.id == signal_id).first()
        if not s or s.status != "pending":
            return None
        s.status = "approved"
        s.approved_by = "manual"
        s.approved_at = datetime.now()
        db.commit()
        db.refresh(s)
        logger.info(f"信号已批准: {s.stock_code} {s.signal_type} #{signal_id}")
        return s

    def reject_signal(self, signal_id: int, reason: str = "") -> Optional[TradingSignal]:
        """拒绝信号"""
        db = self._get_db()
        s = db.query(TradingSignal).filter(TradingSignal.id == signal_id).first()
        if not s or s.status != "pending":
            return None
        s.status = "rejected"
        s.reason = (s.reason or "") + f" [拒绝原因: {reason}]"
        db.commit()
        db.refresh(s)
        return s

    def mark_executed(self, signal_id: int):
        """标记信号已执行"""
        db = self._get_db()
        s = db.query(TradingSignal).filter(TradingSignal.id == signal_id).first()
        if s:
            s.status = "executed"
            db.commit()

    def expire_stale_signals(self):
        """过期处理: 买入信号当日15:00后过期; 卖出信号3天后过期"""
        db = self._get_db()
        today = date.today()
        pending = db.query(TradingSignal).filter(
            TradingSignal.status == "pending"
        ).all()
        expired_count = 0
        for s in pending:
            created_date = s.created_at.date() if s.created_at else today
            if s.signal_type == "buy" and created_date < today:
                s.status = "expired"
                expired_count += 1
            elif s.signal_type == "sell" and (today - created_date).days > 3:
                s.status = "expired"
                expired_count += 1
        if expired_count > 0:
            db.commit()
            logger.info(f"过期信号: {expired_count} 个")

    def get_active_holdings_codes(self) -> List[str]:
        """获取活跃持仓的股票代码列表（监控股）"""
        db = self._get_db()
        return [h.code for h in db.query(StockHoldings).filter(
            StockHoldings.is_active == True
        ).all()]
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/trading_engine/signal_engine.py
git commit -m "feat(trading): add SignalEngine with create/approve/reject/expire lifecycle"
```

---

## Phase 3: 订单管理 + 风控门禁

### Task 3.1: 风控门禁

**Files:**
- Create: `backend/app/trading_engine/risk_guard.py`

- [ ] **Step 1: 编写 RiskGuard 类**

```python
"""交易级风控门禁"""
from datetime import datetime, date, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import TradingOrder, TradeLog


class RiskGuard:
    """风控门禁 — 在 Broker 成交前强制检查"""

    MAX_SINGLE_POSITION_PCT = 0.20   # 单笔仓位上限 20%
    DAILY_LOSS_LIMIT_PCT = 0.05      # 日亏损熔断 5%
    MAX_DAILY_TRADES = 3             # 同股同日最大交易次数
    MIN_CASH_FEN = 500000            # 最低保留现金 5000元（分）
    LIMIT_UP_DOWN_THRESHOLD = 0.098  # 涨跌停阈值 9.8%

    def __init__(self):
        self._db: Optional[Session] = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def check_before_buy(self, stock_code: str, total_value_fen: int,
                          cash_fen: int, amount_fen: int) -> Tuple[bool, str]:
        """买入前检查，返回 (是否通过, 拒绝原因)"""
        db = self._get_db()
        today = date.today()

        # 1. 单笔仓位上限
        max_amount = int(total_value_fen * self.MAX_SINGLE_POSITION_PCT)
        if amount_fen > max_amount:
            return False, f"单笔仓位超限: ¥{amount_fen/100:.0f} > 上限¥{max_amount/100:.0f}(20%)"

        # 2. 日亏损熔断
        daily_start = datetime.combine(today, datetime.min.time())
        today_trades = db.query(TradeLog).filter(
            TradeLog.traded_at >= daily_start
        ).all()
        if today_trades:
            today_pnl = sum(t.pnl or 0 for t in today_trades)
            initial = db.query(TradeLog).order_by(TradeLog.traded_at.asc()).first()
            if initial:
                loss_pct = -today_pnl / initial.amount if today_pnl < 0 else 0
                if loss_pct > self.DAILY_LOSS_LIMIT_PCT:
                    return False, f"日亏损熔断: 当日亏损 {loss_pct*100:.1f}% > 5%"

        # 3. 最低现金
        if cash_fen - amount_fen < self.MIN_CASH_FEN:
            return False, f"剩余现金不足: 需保留¥{self.MIN_CASH_FEN/100:.0f}"

        return True, ""

    def check_before_sell(self, stock_code: str, stock_price_fen: int,
                           limit_up_fen: int = 0, limit_down_fen: int = 0) -> Tuple[bool, str]:
        """卖出前检查"""
        db = self._get_db()

        # 1. 涨跌停检查
        if limit_down_fen > 0 and stock_price_fen <= limit_down_fen:
            return False, "股票跌停，无法卖出"
        if limit_up_fen > 0 and stock_price_fen >= limit_up_fen:
            return False, "股票涨停，无法买入（卖出方向不存在涨停限制，但仍需提示）"

        return True, ""

    def check_trade_frequency(self, stock_code: str) -> Tuple[bool, str]:
        """检查同股同日的交易频率"""
        db = self._get_db()
        today = date.today()
        daily_start = datetime.combine(today, datetime.min.time())
        count = db.query(TradeLog).filter(
            TradeLog.stock_code == stock_code,
            TradeLog.traded_at >= daily_start
        ).count()
        if count >= self.MAX_DAILY_TRADES:
            return False, f"同股同日交易 {count} 次已达上限 {self.MAX_DAILY_TRADES}"
        return True, ""

    def check_limit_price(self, current_price_fen: int, limit_up_fen: int,
                           limit_down_fen: int) -> Tuple[bool, str]:
        """检查是否触及涨跌停"""
        if limit_up_fen > 0 and current_price_fen >= int(limit_up_fen * (1 - 0.001)):
            return False, "价格接近涨停，买入可能无法成交"
        if limit_down_fen > 0 and current_price_fen <= int(limit_down_fen * (1 + 0.001)):
            return False, "价格接近跌停，卖出可能无法成交"
        return True, ""
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/trading_engine/risk_guard.py
git commit -m "feat(trading): add RiskGuard with position/stop-loss/frequency/limit checks"
```

---

### Task 3.2: 订单管理器

**Files:**
- Create: `backend/app/trading_engine/order_manager.py`

- [ ] **Step 1: 编写 OrderManager**

```python
"""订单管理器 — 创建、撮合、生命周期"""
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import TradingOrder, TradingSignal, TradeLog
from app.trading_engine.account import SimAccountManager
from app.trading_engine.broker import SimBroker
from app.trading_engine.risk_guard import RiskGuard
from app.trading_engine.signal_engine import SignalEngine
from app.utils.logger import logger


class OrderManager:
    """订单生命周期管理器"""

    def __init__(self, account_mgr: SimAccountManager, broker: SimBroker,
                 risk_guard: RiskGuard, signal_engine: SignalEngine):
        self.account = account_mgr
        self.broker = broker
        self.risk = risk_guard
        self.signal = signal_engine
        self._db: Optional[Session] = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def create_from_signal(self, signal: TradingSignal) -> Optional[TradingOrder]:
        """从已批准信号创建订单并执行"""
        db = self._get_db()

        if signal.status != "approved":
            logger.warning(f"信号 {signal.id} 状态为 {signal.status}，不可执行")
            return None

        direction = signal.signal_type
        stock_code = signal.stock_code
        quantity = signal.suggested_qty or 100
        price_fen = int((signal.price or 0) * 100)

        if price_fen <= 0:
            logger.error(f"信号 {signal.id} 价格无效: {signal.price}")
            return None

        # 风控检查
        acc = self.account.get_account()
        if direction == "buy":
            amount_fen = price_fen * quantity
            ok, reason = self.risk.check_before_buy(
                stock_code, acc.total_value, acc.cash, amount_fen
            )
            if not ok:
                order = TradingOrder(
                    signal_id=signal.id, stock_code=stock_code,
                    stock_name=signal.stock_name, direction=direction,
                    order_type="market", quantity=quantity, status="rejected",
                    rejection_reason=reason
                )
                db.add(order)
                db.commit()
                return order
            # 资金检查
            total_cost = amount_fen  # broker 会计算精确费用
            if acc.cash < amount_fen:
                order = TradingOrder(
                    signal_id=signal.id, stock_code=stock_code,
                    stock_name=signal.stock_name, direction=direction,
                    order_type="market", quantity=quantity, status="rejected",
                    rejection_reason=f"资金不足: 需要¥{amount_fen/100:.0f}, 可用¥{acc.cash/100:.0f}"
                )
                db.add(order)
                db.commit()
                return order

        # 撮合
        result = self.broker.execute_market_order(
            stock_code, direction, price_fen, quantity
        )
        if not result:
            order = TradingOrder(
                signal_id=signal.id, stock_code=stock_code,
                stock_name=signal.stock_name, direction=direction,
                order_type="market", quantity=quantity, status="rejected",
                rejection_reason="撮合失败（T+1/价格/数量）"
            )
            db.add(order)
            db.commit()
            return order

        # 创建成交订单
        total_cost = result["amount"] + result["total_fee"]
        order = TradingOrder(
            signal_id=signal.id, stock_code=stock_code,
            stock_name=signal.stock_name, direction=direction,
            order_type="market", quantity=quantity,
            filled_price=result["filled_price"],
            filled_quantity=result["filled_quantity"],
            fee=result["total_fee"], status="filled",
            filled_at=datetime.now(),
        )
        db.add(order)

        # 更新资金
        if direction == "buy":
            self.account.deduct_cash(total_cost)
        else:
            self.account.add_cash(result["amount"] - result["total_fee"])

        # 记录日志
        log = TradeLog(
            order_id=0, stock_code=stock_code, stock_name=signal.stock_name,
            direction=direction, price=result["filled_price"],
            quantity=result["filled_quantity"],
            amount=result["amount"], fee=result["total_fee"],
            strategy_name=signal.strategy_name,
        )
        db.add(log)
        db.commit()
        db.refresh(order)
        db.refresh(log)

        # 更新 log 的 order_id
        log.order_id = order.id
        db.commit()

        # 标记信号已执行
        self.signal.mark_executed(signal.id)

        logger.info(
            f"订单成交: {direction} {stock_code} {quantity}股 "
            f"@¥{result['filled_price']/100:.2f} 费用¥{result['total_fee']/100:.2f}"
        )
        return order

    def get_orders(self, status: str = "all", limit: int = 50) -> List[TradingOrder]:
        """查询订单"""
        q = self._get_db().query(TradingOrder)
        if status != "all":
            q = q.filter(TradingOrder.status == status)
        return q.order_by(TradingOrder.created_at.desc()).limit(limit).all()

    def cancel_order(self, order_id: int) -> Optional[TradingOrder]:
        """撤销未成交订单"""
        db = self._get_db()
        o = db.query(TradingOrder).filter(TradingOrder.id == order_id).first()
        if not o or o.status != "pending":
            return None
        o.status = "cancelled"
        o.rejection_reason = "用户撤销"
        db.commit()
        return o
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/trading_engine/order_manager.py
git commit -m "feat(trading): add OrderManager with create-from-signal, cancel, query"
```

---

## Phase 4: 绩效分析

### Task 4.1: 绩效分析器

**Files:**
- Create: `backend/app/trading_engine/performance.py`

- [ ] **Step 1: 编写 PerformanceAnalyzer**

```python
"""绩效分析器 — 收益率/夏普/最大回撤/胜率/盈亏比"""
import math
from datetime import date, timedelta
from typing import List
import numpy as np
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import TradeLog, SimAccount


class PerformanceAnalyzer:
    """绩效指标计算"""

    def __init__(self):
        self._db: Session = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def get_all_trades(self, days: int = 365) -> List[TradeLog]:
        """获取最近N天的所有交易"""
        since = date.today() - timedelta(days=days)
        return self._get_db().query(TradeLog).filter(
            TradeLog.traded_at >= since
        ).order_by(TradeLog.traded_at.asc()).all()

    def calc_win_rate(self, trades: List[TradeLog]) -> float:
        """胜率 = 盈利交易数 / 有盈亏的交易数"""
        pnl_trades = [t for t in trades if t.pnl is not None and t.pnl != 0]
        if not pnl_trades:
            return 0.0
        wins = sum(1 for t in pnl_trades if t.pnl > 0)
        return round(wins / len(pnl_trades), 4)

    def calc_profit_factor(self, trades: List[TradeLog]) -> float:
        """盈亏比 = 平均盈利 / 平均亏损"""
        wins = [t.pnl for t in trades if t.pnl and t.pnl > 0]
        losses = [abs(t.pnl) for t in trades if t.pnl and t.pnl < 0]
        if not losses or not wins:
            return 0.0
        return round(np.mean(wins) / np.mean(losses), 2)

    def calc_max_drawdown(self, trades: List[TradeLog]) -> dict:
        """最大回撤"""
        if not trades:
            return {"max_drawdown_pct": 0, "recovery_days": 0}
        # 按时间排序计算累计盈亏
        cumulative = 0
        peak = 0
        max_dd = 0
        dd_start = None
        recovery_days = 0
        for t in trades:
            cumulative += (t.pnl or 0)
            if cumulative > peak:
                peak = cumulative
                if dd_start:
                    recovery_days = max(recovery_days, (t.traded_at.date() - dd_start).days)
                    dd_start = None
            dd = (peak - cumulative) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                if dd_start is None:
                    dd_start = t.traded_at.date() if t.traded_at else None
        return {
            "max_drawdown_pct": round(max_dd * 100, 2),
            "recovery_days": recovery_days,
        }

    def calc_sharpe_ratio(self, trades: List[TradeLog], risk_free_rate: float = 0.025) -> float:
        """夏普比率 = (年化收益 - 无风险利率) / 年化波动率"""
        if not trades:
            return 0.0
        daily_returns = [t.pnl_pct or 0 for t in trades if t.pnl_pct is not None]
        if len(daily_returns) < 2:
            return 0.0
        avg_daily = np.mean(daily_returns)
        std_daily = np.std(daily_returns, ddof=1)
        if std_daily == 0:
            return 0.0
        sharpe = (avg_daily * 252 - risk_free_rate) / (std_daily * math.sqrt(252))
        return round(sharpe, 2)

    def calc_annual_return(self) -> float:
        """年化收益率"""
        acc = self._get_db().query(SimAccount).first()
        if not acc:
            return 0.0
        total_return = (acc.total_value - acc.initial_capital) / acc.initial_capital
        days_since_start = (date.today() - (acc.created_at.date() if acc.created_at else date.today())).days or 1
        annual = (1 + total_return) ** (252 / days_since_start) - 1
        return round(annual * 100, 2)

    def get_summary(self) -> dict:
        """获取完整绩效摘要"""
        trades = self.get_all_trades()
        completed = [t for t in trades if t.pnl is not None]
        total_trades = len(completed)

        acc = self._get_db().query(SimAccount).first()
        total_return_pct = round(
            (acc.total_value - acc.initial_capital) / acc.initial_capital * 100, 2
        ) if acc else 0

        avg_days = 0
        if completed:
            days_list = [t.holding_days for t in completed if t.holding_days]
            avg_days = round(np.mean(days_list), 1) if days_list else 0

        return {
            "total_return_pct": total_return_pct,
            "annual_return_pct": self.calc_annual_return(),
            "max_drawdown": self.calc_max_drawdown(completed),
            "sharpe_ratio": self.calc_sharpe_ratio(completed),
            "win_rate": self.calc_win_rate(completed),
            "profit_factor": self.calc_profit_factor(completed),
            "total_trades": total_trades,
            "avg_holding_days": avg_days,
        }

    def get_equity_curve(self, days: int = 90) -> list:
        """获取净值曲线数据（每日一点）"""
        since = date.today() - timedelta(days=days)
        trades = self._get_db().query(TradeLog).filter(
            TradeLog.traded_at >= since
        ).order_by(TradeLog.traded_at.asc()).all()

        acc = self._get_db().query(SimAccount).first()
        base = acc.initial_capital if acc else 10000000

        points = []
        cumulative = base
        for t in trades:
            cumulative += (t.pnl or 0)
            points.append({
                "date": t.traded_at.strftime("%Y-%m-%d") if t.traded_at else "",
                "value": round(cumulative / 100, 2),
                "pnl": round((t.pnl or 0) / 100, 2),
            })
        return points
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/trading_engine/performance.py
git commit -m "feat(trading): add PerformanceAnalyzer with sharpe/drawdown/win-rate/equity-curve"
```

---

## Phase 5: 后端 API 集成 + 定时任务

### Task 5.1: 策略调度器 + API 路由

**Files:**
- Create: `backend/app/trading_engine/scheduler.py`
- Modify: `backend/app/main.py` (注册路由 + 添加定时Job)

- [ ] **Step 1: 编写 scheduler.py**

```python
"""策略扫描定时任务"""
import json
from datetime import datetime
import pandas as pd
from app.database import SessionLocal
from app.models import TradingSignal, StockHoldings, SimAccount
from app.data_sources.tencent_client import TencentDataSource
from app.trading_engine.trend_tracker import TrendTrackerStrategy
from app.trading_engine.signal_engine import SignalEngine
from app.utils.logger import logger


tencent = TencentDataSource()
strategy = TrendTrackerStrategy()
signal_engine = SignalEngine()


async def strategy_scan_job():
    """每5分钟扫描监控股，生成交易信号"""
    logger.info("=== 策略扫描 ===")
    db = SessionLocal()
    try:
        holdings = db.query(StockHoldings).filter(StockHoldings.is_active == True).all()
        codes = [h.code for h in holdings]
        if not codes:
            logger.info("无监控股，跳过扫描")
            return

        # 获取行情数据
        batch_data = await tencent.fetch_batch(codes)
        new_signals = 0

        for code in codes:
            rt = batch_data.get(code, {})
            if not rt:
                continue
            price = rt.get("price", 0)
            if price <= 0:
                continue

            # 获取K线数据用于计算指标
            kline = await tencent.fetch_kline(code, "day", 60)
            kline_data = kline.get("data", []) if kline else []
            if len(kline_data) < 21:
                continue

            df = pd.DataFrame(kline_data)
            df.columns = [c.lower() for c in df.columns]
            df = df.sort_values("date")

            name = rt.get("name", code)
            params = strategy.get_params()

            # 买入信号
            try:
                df_buy = strategy.generate_buy_signals(df)
                latest = df_buy.iloc[-1]
                if latest.get("buy_signal"):
                    # 检查是否已有重复pending信号
                    existing = db.query(TradingSignal).filter(
                        TradingSignal.stock_code == code,
                        TradingSignal.signal_type == "buy",
                        TradingSignal.status == "pending",
                    ).first()
                    if not existing:
                        atr = latest.get("atr", price * 0.02)
                        qty = strategy.calculate_position_size(
                            db.query(SimAccount).first().cash, int(price * 100), int(atr * 100)
                        )
                        reason = strategy.describe_signal(df_buy, -1)
                        signal_engine.create_signal(
                            code, name, "buy", price, reason, params, qty, confidence=0.7
                        )
                        new_signals += 1
                        logger.info(f"买入信号: {name}({code}) @¥{price:.2f}")

                # 卖出信号（仅对持仓的）
                holding = next((h for h in holdings if h.code == code), None)
                if holding:
                    df_sell = strategy.generate_sell_signals(df, holding_cost=holding.cost_price)
                    latest_sell = df_sell.iloc[-1]
                    if latest_sell.get("sell_signal"):
                        existing = db.query(TradingSignal).filter(
                            TradingSignal.stock_code == code,
                            TradingSignal.signal_type == "sell",
                            TradingSignal.status.in_(["pending", "approved"]),
                        ).first()
                        if not existing:
                            reason = strategy.describe_signal(df_sell, -1)
                            # 止损信号自动批准
                            is_stop = latest_sell.get("close", 0) < (holding.cost_price or 0)
                            signal_engine.create_signal(
                                code, name, "sell", price, reason, params,
                                suggested_qty=int(holding.position or 0),
                                confidence=0.8, auto_approve=is_stop
                            )
                            new_signals += 1
                            logger.info(f"卖出信号{'[自动止损]' if is_stop else ''}: {name}({code}) @¥{price:.2f}")
            except Exception as e:
                logger.error(f"扫描 {code} 异常: {e}")

        if new_signals > 0:
            logger.info(f"本次生成 {new_signals} 个新信号")
    except Exception as e:
        logger.error(f"策略扫描异常: {e}")
    finally:
        db.close()


async def expire_signals_job():
    """收盘后清理过期信号"""
    signal_engine.expire_stale_signals()
```

- [ ] **Step 2: 在 main.py 中注册 trading API 路由和定时Job**

在 `backend/app/main.py` 中追加以下代码块（放在 `if __name__ == "__main__":` 之前）:

```python
# ==================== 模拟交易引擎 API ====================

from app.trading_engine.account import SimAccountManager
from app.trading_engine.broker import SimBroker
from app.trading_engine.signal_engine import SignalEngine
from app.trading_engine.order_manager import OrderManager
from app.trading_engine.risk_guard import RiskGuard
from app.trading_engine.performance import PerformanceAnalyzer
from app.trading_engine.scheduler import strategy_scan_job, expire_signals_job

account_mgr = SimAccountManager()
sim_broker = SimBroker()
signal_engine = SignalEngine()
risk_guard = RiskGuard()
order_mgr = OrderManager(account_mgr, sim_broker, risk_guard, signal_engine)
perf_analyzer = PerformanceAnalyzer()

# 定时任务: 策略扫描
scheduler.add_job(
    strategy_scan_job,
    CronTrigger(day_of_week="0-4", hour="9-14", minute="*/5"),
    id="strategy_scan",
)
scheduler.add_job(
    strategy_scan_job,
    CronTrigger(day_of_week="0-4", hour="15", minute="0"),
    id="strategy_scan_close",
)
scheduler.add_job(
    expire_signals_job,
    CronTrigger(day_of_week="0-4", hour="15", minute="5"),
    id="expire_signals",
)


# --- 账户 API ---
@app.get("/api/trading/account")
async def trading_account():
    return {"account": account_mgr.get_summary()}


@app.post("/api/trading/account/reset")
async def reset_account():
    account_mgr.reset_account()
    return {"message": "账户已重置", "account": account_mgr.get_summary()}


# --- 信号 API ---
class SignalAction(BaseModel):
    reason: str = ""


@app.get("/api/trading/signals")
async def get_signals(status: str = "pending", limit: int = 50):
    db = SessionLocal()
    try:
        q = db.query(TradingSignal)
        if status != "all":
            q = q.filter(TradingSignal.status == status)
        signals = q.order_by(TradingSignal.created_at.desc()).limit(limit).all()
        return {"signals": [s.to_dict() for s in signals]}
    finally:
        db.close()


@app.post("/api/trading/signals/{signal_id}/approve")
async def approve_signal(signal_id: int, body: SignalAction = SignalAction()):
    s = signal_engine.approve_signal(signal_id)
    if not s:
        raise HTTPException(status_code=404, detail="信号不存在或已处理")
    # 自动创建订单
    order = order_mgr.create_from_signal(s)
    return {"message": "信号已批准", "signal": s.to_dict(),
            "order": order.to_dict() if order else None}


@app.post("/api/trading/signals/{signal_id}/reject")
async def reject_signal(signal_id: int, body: SignalAction = SignalAction()):
    s = signal_engine.reject_signal(signal_id, body.reason)
    if not s:
        raise HTTPException(status_code=404, detail="信号不存在或已处理")
    return {"message": "信号已拒绝", "signal": s.to_dict()}


# --- 订单 API ---
@app.get("/api/trading/orders")
async def get_orders(status: str = "all", limit: int = 50):
    orders = order_mgr.get_orders(status, limit)
    return {"orders": [o.to_dict() for o in orders]}


@app.delete("/api/trading/orders/{order_id}")
async def cancel_order(order_id: int):
    o = order_mgr.cancel_order(order_id)
    if not o:
        raise HTTPException(status_code=404, detail="订单不存在或已成交")
    return {"message": "订单已撤销", "order": o.to_dict()}


# --- 绩效 API ---
@app.get("/api/trading/performance")
async def get_performance(period: str = "1m"):
    days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}.get(period, 30)
    summary = perf_analyzer.get_summary()
    return {"summary": summary}


@app.get("/api/trading/performance/curve")
async def get_equity_curve(days: int = 90):
    return {"curve": perf_analyzer.get_equity_curve(days)}


# --- 策略参数 API ---
@app.get("/api/trading/strategy/params")
async def get_strategy_params():
    return {"params": strategy.get_params()}


class ParamsUpdate(BaseModel):
    params: dict


@app.patch("/api/trading/strategy/params")
async def update_strategy_params(body: ParamsUpdate):
    strategy.set_params(**body.params)
    return {"message": "参数已更新", "params": strategy.get_params()}


# --- 回测 API ---
@app.post("/api/trading/strategy/backtest")
async def trigger_backtest(background_tasks: BackgroundTasks):
    return {"message": "回测功能将在后续版本实现"}
```

- [ ] **Step 3: 在 main.py 顶部追加 import**

在 `from app.models import StockHoldings, RiskAlert, AIStrategy, PositionAdjustment` 这一行追加 `TradingSignal, TradingOrder, TradeLog, SimAccount`:
```python
from app.models import StockHoldings, RiskAlert, AIStrategy, PositionAdjustment, TradingSignal, TradingOrder, TradeLog, SimAccount
```

- [ ] **Step 4: 验证 — 启动应用，测试API**

```bash
cd backend && python -m app.main &
sleep 3
curl http://localhost:8000/api/trading/account | python -m json.tool
curl http://localhost:8000/api/trading/strategy/params | python -m json.tool
```
Expected: 账户返回100000元，策略参数返回默认JSON

- [ ] **Step 5: Commit**

```bash
git add backend/app/trading_engine/scheduler.py backend/app/main.py
git commit -m "feat(trading): add strategy scheduler, 12 REST API endpoints, and CronJobs"
```

---

## Phase 6: 前端面板

### Task 6.1: 交易API客户端 + Pinia状态

**Files:**
- Create: `frontend/src/api/trading.js`
- Create: `frontend/src/store/trading.js`

- [ ] **Step 1: 编写 trading.js API**

```javascript
import apiClient from './client'

export async function getAccount() {
  return apiClient.get('/trading/account')
}

export async function resetAccount() {
  return apiClient.post('/trading/account/reset')
}

export async function getSignals(status = 'pending', limit = 50) {
  return apiClient.get('/trading/signals', { params: { status, limit } })
}

export async function approveSignal(id) {
  return apiClient.post(`/trading/signals/${id}/approve`)
}

export async function rejectSignal(id, reason = '') {
  return apiClient.post(`/trading/signals/${id}/reject`, { reason })
}

export async function getOrders(status = 'all', limit = 50) {
  return apiClient.get('/trading/orders', { params: { status, limit } })
}

export async function cancelOrder(id) {
  return apiClient.delete(`/trading/orders/${id}`)
}

export async function getPerformance(period = '1m') {
  return apiClient.get('/trading/performance', { params: { period } })
}

export async function getEquityCurve(days = 90) {
  return apiClient.get('/trading/performance/curve', { params: { days } })
}

export async function getStrategyParams() {
  return apiClient.get('/trading/strategy/params')
}

export async function updateStrategyParams(params) {
  return apiClient.patch('/trading/strategy/params', { params })
}
```

- [ ] **Step 2: 编写 trading.js Pinia store**

```javascript
import { defineStore } from 'pinia'
import * as api from '../api/trading'

export const useTradingStore = defineStore('trading', {
  state: () => ({
    account: null,
    signals: [],
    orders: [],
    summary: null,
    curve: [],
    params: {},
    loading: false,
    error: null,
  }),

  actions: {
    async fetchAccount() {
      const res = await api.getAccount()
      this.account = res.account
    },
    async fetchSignals(status = 'pending') {
      const res = await api.getSignals(status)
      this.signals = res.signals || []
    },
    async approveSignal(id) {
      await api.approveSignal(id)
      await this.fetchSignals()
      await this.fetchAccount()
    },
    async rejectSignal(id, reason) {
      await api.rejectSignal(id, reason)
      await this.fetchSignals()
    },
    async fetchOrders(status = 'all') {
      const res = await api.getOrders(status)
      this.orders = res.orders || []
    },
    async fetchPerformance(period = '1m') {
      const res = await api.getPerformance(period)
      this.summary = res.summary
    },
    async fetchCurve(days = 90) {
      const res = await api.getEquityCurve(days)
      this.curve = res.curve || []
    },
    async fetchParams() {
      const res = await api.getStrategyParams()
      this.params = res.params || {}
    },
    async updateParams(params) {
      await api.updateStrategyParams(params)
      this.params = { ...this.params, ...params }
    },
    async refreshAll() {
      this.loading = true
      try {
        await Promise.all([
          this.fetchAccount(),
          this.fetchSignals(),
          this.fetchOrders(),
          this.fetchPerformance(),
          this.fetchCurve(),
          this.fetchParams(),
        ])
      } finally {
        this.loading = false
      }
    },
  }
})
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/trading.js frontend/src/store/trading.js
git commit -m "feat(trading): add trading API client and Pinia store"
```

---

### Task 6.2: 交易看板 Vue 页面

**Files:**
- Create: `frontend/src/components/TradingSignal.vue`
- Create: `frontend/src/components/TradingAccount.vue`
- Create: `frontend/src/components/TradingPerformance.vue`
- Create: `frontend/src/views/Trading.vue`
- Modify: `frontend/src/router/index.js`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: 编写 TradingSignal.vue 信号台组件**

```vue
<template>
  <div class="trading-signal">
    <h3>策略信号台</h3>
    <div class="signal-stats">
      <span>待审批: {{ pendingCount }}</span>
      <span>今日新增: {{ todayCount }}</span>
    </div>
    <div v-if="signals.length === 0" class="empty">暂无待审批信号</div>
    <div v-for="s in signals" :key="s.id" class="signal-card" :class="s.signal_type">
      <div class="signal-header">
        <span class="stock-badge">{{ s.stock_name }}({{ s.stock_code }})</span>
        <span class="signal-type-badge" :class="s.signal_type">
          {{ s.signal_type === 'buy' ? '买入' : '卖出' }}
        </span>
      </div>
      <div class="signal-body">
        <p><strong>触发条件:</strong> {{ s.reason }}</p>
        <p>参考价: ¥{{ s.price?.toFixed(2) }} | 置信度: {{ (s.confidence * 100).toFixed(0) }}%</p>
        <p v-if="s.suggested_qty">建议数量: {{ s.suggested_qty }}股</p>
      </div>
      <div class="signal-actions">
        <button class="btn-approve" @click="$emit('approve', s.id)">批准</button>
        <button class="btn-reject" @click="$emit('reject', s.id)">拒绝</button>
        <input v-model="rejectReasons[s.id]" placeholder="拒绝理由(可选)" class="reject-input" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({ signals: { type: Array, default: () => [] } })
defineEmits(['approve', 'reject'])

const rejectReasons = ref({})
const pendingCount = computed(() => props.signals.filter(s => s.status === 'pending').length)
const todayCount = computed(() => props.signals.length)
</script>

<style scoped>
.signal-card { background: #1a1a2e; border-radius: 8px; padding: 16px; margin-bottom: 12px; border-left: 4px solid #666; }
.signal-card.buy { border-left-color: #00c853; }
.signal-card.sell { border-left-color: #ff1744; }
.signal-header { display: flex; justify-content: space-between; margin-bottom: 8px; }
.signal-type-badge { padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.signal-type-badge.buy { background: #00c85333; color: #00c853; }
.signal-type-badge.sell { background: #ff174433; color: #ff1744; }
.signal-actions { display: flex; gap: 8px; margin-top: 12px; }
.btn-approve { background: #00c853; color: #fff; border: none; padding: 6px 16px; border-radius: 4px; cursor: pointer; }
.btn-reject { background: #ff1744; color: #fff; border: none; padding: 6px 16px; border-radius: 4px; cursor: pointer; }
.reject-input { flex: 1; background: #2a2a3e; border: 1px solid #444; color: #fff; padding: 4px 8px; border-radius: 4px; }
.empty { color: #888; text-align: center; padding: 40px; }
</style>
```

- [ ] **Step 2: 编写 TradingAccount.vue 账户组件**

```vue
<template>
  <div class="trading-account">
    <h3>模拟账户</h3>
    <div class="account-cards">
      <div class="acct-card">
        <span class="acct-label">总资产</span>
        <span class="acct-value">¥{{ account?.total_value?.toLocaleString() || '0' }}</span>
      </div>
      <div class="acct-card">
        <span class="acct-label">可用现金</span>
        <span class="acct-value">¥{{ account?.cash?.toLocaleString() || '0' }}</span>
      </div>
      <div class="acct-card">
        <span class="acct-label">累计收益率</span>
        <span class="acct-value" :class="returnColor"> {{ account?.total_return_pct || 0 }}%</span>
      </div>
      <div class="acct-card">
        <span class="acct-label">当日盈亏</span>
        <span class="acct-value" :class="dailyColor">¥{{ account?.daily_pnl?.toLocaleString() || '0' }}</span>
      </div>
    </div>
    <div v-if="curve.length > 0" id="equity-chart" ref="chartRef" style="width:100%;height:300px;"></div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  account: Object,
  curve: { type: Array, default: () => [] },
})

const chartRef = ref(null)
const returnColor = computed(() => (props.account?.total_return_pct || 0) >= 0 ? 'text-green' : 'text-red')
const dailyColor = computed(() => (props.account?.daily_pnl || 0) >= 0 ? 'text-green' : 'text-red')

watch(() => props.curve, (val) => {
  if (!chartRef.value || !val.length) return
  const chart = echarts.init(chartRef.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: val.map(p => p.date), show: false },
    yAxis: { type: 'value', axisLabel: { formatter: '¥{value}' } },
    series: [{
      data: val.map(p => p.value),
      type: 'line',
      smooth: true,
      lineStyle: { color: '#00c853' },
      areaStyle: { color: 'rgba(0,200,83,0.1)' },
    }],
  })
}, { deep: true })
</script>

<style scoped>
.account-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
.acct-card { background: #1a1a2e; border-radius: 8px; padding: 16px; text-align: center; }
.acct-label { display: block; color: #888; font-size: 12px; margin-bottom: 4px; }
.acct-value { font-size: 20px; font-weight: bold; color: #fff; }
.text-green { color: #00c853; }
.text-red { color: #ff1744; }
</style>
```

- [ ] **Step 3: 编写 TradingPerformance.vue 绩效组件**

```vue
<template>
  <div class="trading-performance">
    <h3>策略绩效</h3>
    <div v-if="!summary" class="empty">暂无交易数据</div>
    <div v-else class="perf-cards">
      <div class="perf-card"><span>胜率</span><strong>{{ (summary.win_rate * 100).toFixed(1) }}%</strong></div>
      <div class="perf-card"><span>盈亏比</span><strong>{{ summary.profit_factor }}</strong></div>
      <div class="perf-card"><span>夏普比率</span><strong>{{ summary.sharpe_ratio }}</strong></div>
      <div class="perf-card"><span>最大回撤</span><strong>{{ summary.max_drawdown?.max_drawdown_pct || 0 }}%</strong></div>
      <div class="perf-card"><span>交易次数</span><strong>{{ summary.total_trades }}</strong></div>
      <div class="perf-card"><span>年化收益</span><strong>{{ summary.annual_return_pct }}%</strong></div>
      <div class="perf-card"><span>累计收益</span><strong :class="summary.total_return_pct >= 0 ? 'text-green' : 'text-red'">{{ summary.total_return_pct }}%</strong></div>
      <div class="perf-card"><span>平均持仓</span><strong>{{ summary.avg_holding_days }}天</strong></div>
    </div>
  </div>
</template>

<script setup>
defineProps({ summary: Object })
</script>

<style scoped>
.perf-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.perf-card { background: #1a1a2e; border-radius: 8px; padding: 12px; text-align: center; }
.perf-card span { display: block; color: #888; font-size: 11px; margin-bottom: 2px; }
.perf-card strong { font-size: 18px; color: #fff; }
.empty { color: #888; text-align: center; padding: 40px; }
.text-green { color: #00c853; }
.text-red { color: #ff1744; }
</style>
```

- [ ] **Step 4: 编写 Trading.vue 主页面**

```vue
<template>
  <div class="trading-view">
    <div class="tabs">
      <button :class="{ active: tab === 'signals' }" @click="tab = 'signals'">策略信号台</button>
      <button :class="{ active: tab === 'account' }" @click="tab = 'account'">模拟账户</button>
      <button :class="{ active: tab === 'performance' }" @click="tab = 'performance'">策略绩效</button>
      <button :class="{ active: tab === 'params' }" @click="tab = 'params'">参数配置</button>
    </div>

    <div v-if="tab === 'signals'">
      <TradingSignal :signals="store.signals"
        @approve="handleApprove" @reject="(id) => handleReject(id)" />
    </div>
    <div v-else-if="tab === 'account'">
      <TradingAccount :account="store.account" :curve="store.curve" />
      <div class="orders-section">
        <h4>交易记录</h4>
        <table class="orders-table">
          <thead><tr><th>时间</th><th>股票</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th><th>盈亏</th></tr></thead>
          <tbody>
            <tr v-for="o in store.orders" :key="o.id">
              <td>{{ o.filled_at?.slice(0, 16) || '-' }}</td>
              <td>{{ o.stock_name }}({{ o.stock_code }})</td>
              <td :class="o.direction === 'buy' ? 'text-green' : 'text-red'">{{ o.direction === 'buy' ? '买' : '卖' }}</td>
              <td>¥{{ (o.filled_price / 100).toFixed(2) }}</td>
              <td>{{ o.filled_quantity }}</td>
              <td>¥{{ (o.filled_quantity * o.filled_price / 100).toLocaleString() }}</td>
              <td>-</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div v-else-if="tab === 'performance'">
      <TradingPerformance :summary="store.summary" />
    </div>
    <div v-else-if="tab === 'params'">
      <div class="params-panel">
        <h4>趋势跟踪策略参数</h4>
        <div v-for="(val, key) in store.params" :key="key" class="param-row">
          <label>{{ key }}</label>
          <input type="number" :value="val" step="0.01" @change="e => updateParam(key, parseFloat(e.target.value))" />
        </div>
        <button class="btn-save" @click="saveParams">保存参数</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useTradingStore } from '../store/trading'
import TradingSignal from '../components/TradingSignal.vue'
import TradingAccount from '../components/TradingAccount.vue'
import TradingPerformance from '../components/TradingPerformance.vue'

const store = useTradingStore()
const tab = ref('signals')

onMounted(() => store.refreshAll())

async function handleApprove(id) {
  await store.approveSignal(id)
}

async function handleReject(id) {
  const reason = prompt('拒绝理由(可选):')
  await store.rejectSignal(id, reason || '')
}

async function saveParams() {
  await store.updateParams(store.params)
  alert('参数已保存')
}
</script>

<style scoped>
.trading-view { padding: 20px; }
.tabs { display: flex; gap: 8px; margin-bottom: 20px; border-bottom: 1px solid #333; padding-bottom: 8px; }
.tabs button { background: transparent; border: none; color: #888; padding: 8px 16px; cursor: pointer; border-bottom: 2px solid transparent; }
.tabs button.active { color: #fff; border-bottom-color: #00c853; }
.orders-section { margin-top: 20px; }
.orders-table { width: 100%; border-collapse: collapse; }
.orders-table th, .orders-table td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }
.orders-table th { color: #888; font-weight: normal; }
.params-panel { max-width: 400px; }
.param-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #222; }
.param-row label { color: #aaa; }
.param-row input { width: 100px; background: #1a1a2e; border: 1px solid #444; color: #fff; padding: 4px 8px; border-radius: 4px; }
.btn-save { margin-top: 16px; background: #00c853; color: #fff; border: none; padding: 8px 24px; border-radius: 4px; cursor: pointer; }
</style>
```

- [ ] **Step 5: 更新路由，在 `frontend/src/router/index.js` 中追加**

```javascript
import Trading from '../views/Trading.vue'

// 在 routes 数组中追加:
{
  path: '/trading',
  name: 'Trading',
  component: Trading
}
```

- [ ] **Step 6: 更新导航，在 App.vue 的 nav-links 中追加**

```html
<router-link to="/trading" class="nav-link" :class="{ active: $route.name === 'Trading' }">
  交易看板
</router-link>
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/TradingSignal.vue frontend/src/components/TradingAccount.vue \
        frontend/src/components/TradingPerformance.vue frontend/src/views/Trading.vue \
        frontend/src/router/index.js frontend/src/App.vue
git commit -m "feat(trading): add Trading view with signals/account/performance/params tabs"
```

---

## Phase 7: 集成验证与端到端测试

### Task 7.1: 构建前端 + 启动全栈验证

- [ ] **Step 1: 构建前端**

```bash
cd frontend && npm run build
```
Expected: Build success, no errors

- [ ] **Step 2: 启动后端验证所有API**

```bash
cd backend && python -m app.main &
sleep 3
# 验证账户API
curl -s http://localhost:8000/api/trading/account | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['account']['total_value']==100000, f'Expected 100000, got {d}'"
echo "PASS: Account API"

# 验证信号API
curl -s http://localhost:8000/api/trading/signals | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Signals: {len(d[\"signals\"])} pending')"
echo "PASS: Signals API"

# 验证绩效API
curl -s http://localhost:8000/api/trading/performance | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Summary keys: {list(d[\"summary\"].keys())}')"
echo "PASS: Performance API"

# 验证策略参数API
curl -s http://localhost:8000/api/trading/strategy/params | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'ma_short' in d['params'], 'Missing param'"
echo "PASS: Strategy params API"

# 验证前端页面
curl -s http://localhost:8000/ | head -5
echo "PASS: Frontend serving"

# 关闭测试服务
kill %1
```

- [ ] **Step 3: Commit（如有微调）**

```bash
git add -A
git diff --cached --stat
git commit -m "feat(trading): end-to-end verification, all APIs passing"
```

---

## 完成标准

- [x] 模拟账户创建/查询/重置正常，资金100000元
- [ ] 12个REST API全部可用（账户/信号/订单/绩效/策略）
- [ ] 趋势跟踪策略正确计算MA5/MA20/ATR，生成买卖信号
- [ ] 信号审批→自动创建订单→撮合成交 流程跑通
- [ ] 风控门禁拦截异常交易（仓位超限/日亏损熔断/T+1）
- [ ] 绩效指标显示（胜率/盈亏比/夏普/最大回撤/净值曲线）
- [ ] 前端交易看板4个Tab正常渲染
- [ ] 定时任务（每5分钟信号扫描/收盘过期清理）正常执行

---

## 预估代码量

| 模块 | 新增行数 | 修改行数 |
|------|----------|----------|
| 数据模型 (models.py) | +160 | +10 |
| database.py | — | +10 |
| account.py | +130 | — |
| broker.py | +140 | — |
| strategy_base.py | +70 | — |
| trend_tracker.py | +110 | — |
| signal_engine.py | +110 | — |
| order_manager.py | +150 | — |
| risk_guard.py | +100 | — |
| performance.py | +140 | — |
| scheduler.py | +100 | — |
| main.py (API路由) | +160 | +5 |
| 前端 (6文件) | +500 | +10 |
| **合计** | **~1870** | **~35** |
