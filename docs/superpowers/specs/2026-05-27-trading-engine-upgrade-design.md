# 恭喜发财 v3.0 — 模拟交易引擎升级设计

> 状态：已确认
> 日期：2026-05-27
> 版本：v1

## 一、目标

在现有行情监控+AI分析系统基础上，新增**模拟交易引擎**，实现趋势跟踪策略的完整链路：信号生成 → 人工审批 → 模拟成交 → 绩效分析 → 策略优化。最终目标是帮助用户通过实际操作掌握量化交易全流程。

### 核心原则

- **深度优先**：先做透趋势跟踪策略，跑通全流程，后续扩展动量/海龟/套利
- **仿真级真实度**：实时行情驱动决策，市价/限价模拟撮合，A股规则约束
- **人机混合**：日常信号自动审批，重大决策（仓位>20%、止损）人工确认
- **独立模块**：新增 `trading_engine/` 模块，与现有系统松耦合

## 二、模块架构

### 2.1 新增模块

```
backend/app/trading_engine/
├── __init__.py
├── account.py          # 模拟账户管理（资金/持仓/净值/可用资金）
├── broker.py           # 模拟撮合引擎（市价/限价/滑点/T+1/手续费）
├── strategy_base.py    # 策略基类（统一接口: 信号计算/仓位计算/止盈止损）
├── trend_tracker.py    # 趋势跟踪策略（MA金叉死叉 + ATR风控）
├── signal_engine.py    # 信号生成、审批队列、自动/手动触发
├── order_manager.py    # 订单生命周期管理（创建→提交→撮合→成交/撤销）
├── performance.py      # 绩效分析（收益率/夏普/最大回撤/胜率/盈亏比）
├── risk_guard.py       # 交易级风控（单笔上限/日亏损熔断/频率限制）
└── scheduler.py        # 策略定时检测任务（每5分钟扫描信号）
```

### 2.2 与现有系统关系

```
现有系统                          新增模块
┌─────────────┐                 ┌─────────────────┐
│ data_sources │───行情数据────▶│  trading_engine  │
│ monitor.py   │                │  ├─ strategy     │
│ ai/debate.py │───AI建议─────▶│  ├─ signal       │
│ feishu       │◀──告警推送────│  ├─ broker       │
│ WebSocket    │◀──实时通知────│  ├─ account      │
│ models.py    │───DB共享──────│  └─ performance  │
└─────────────┘                 └─────────────────┘
```

### 2.3 新增 API 端点

| 类别 | 端点 | 说明 |
|------|------|------|
| 账户 | `GET /api/trading/account` | 获取账户概览（资金/持仓/净值） |
| 信号 | `GET /api/trading/signals?status=pending` | 获取信号列表 |
| 信号 | `POST /api/trading/signals/{id}/approve` | 批准信号 |
| 信号 | `POST /api/trading/signals/{id}/reject` | 拒绝信号 |
| 订单 | `GET /api/trading/orders?status=all` | 获取订单列表 |
| 订单 | `DELETE /api/trading/orders/{id}` | 撤销未成交订单 |
| 绩效 | `GET /api/trading/performance?period=1m` | 获取绩效指标 |
| 绩效 | `GET /api/trading/performance/curve` | 获取净值曲线数据 |
| 策略 | `GET /api/trading/strategy/params` | 获取策略参数 |
| 策略 | `PATCH /api/trading/strategy/params` | 更新策略参数 |
| 策略 | `POST /api/trading/strategy/backtest` | 触发回测（异步） |
| 策略 | `GET /api/trading/strategy/backtest/{id}` | 获取回测结果 |

### 2.4 定时任务扩展

| 频率 | 任务 | 说明 |
|------|------|------|
| 每5分钟 | `strategy_scan_job` | 扫描监控股列表，计算MA/ATR，生成信号 |
| 每5分钟 | `order_check_job` | 检查限价单是否达标，执行撮合 |
| 每日15:05 | `performance_daily_job` | 计算当日绩效，更新账户净值 |

## 三、数据模型

### 3.1 新增表

```sql
-- 模拟账户（单例，只有一行）
CREATE TABLE sim_account (
    id INTEGER PRIMARY KEY,
    cash REAL NOT NULL DEFAULT 100000.00,
    frozen REAL NOT NULL DEFAULT 0.00,
    total_value REAL NOT NULL DEFAULT 100000.00,
    initial_capital REAL NOT NULL DEFAULT 100000.00,
    daily_pnl REAL NOT NULL DEFAULT 0.00,
    total_pnl REAL NOT NULL DEFAULT 0.00,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 策略信号
CREATE TABLE signals (
    id INTEGER PRIMARY KEY,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    strategy_name TEXT NOT NULL DEFAULT 'trend_tracker',
    signal_type TEXT NOT NULL,  -- 'buy' / 'sell'
    price REAL,                  -- 触发时参考价格
    confidence REAL,             -- 信号置信度 0-1
    reason TEXT,                 -- 触发条件说明
    params_json TEXT,            -- 当前策略参数快照
    suggested_qty INTEGER,       -- 建议买入股数
    approved_by TEXT DEFAULT NULL, -- 'manual' / 'auto' / NULL
    approved_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending', -- pending / approved / rejected / expired / executed
    created_at TEXT NOT NULL
);

-- 交易订单
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    signal_id INTEGER,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    direction TEXT NOT NULL,     -- 'buy' / 'sell'
    order_type TEXT NOT NULL,    -- 'market' / 'limit' / 'stop_loss'
    price REAL,                  -- 委托价格（市价单为空）
    quantity INTEGER NOT NULL,
    filled_price REAL,
    filled_quantity INTEGER DEFAULT 0,
    fee REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'pending', -- pending / partial / filled / cancelled / rejected
    rejection_reason TEXT,
    created_at TEXT NOT NULL,
    filled_at TEXT
);

-- 交易日志
CREATE TABLE trade_log (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    direction TEXT NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    amount REAL NOT NULL,        -- 成交金额
    fee REAL NOT NULL,
    pnl REAL,                    -- 本次交易盈亏（卖出时计算）
    pnl_pct REAL,
    strategy_name TEXT NOT NULL,
    holding_days INTEGER,        -- 持仓天数（卖出时计算）
    traded_at TEXT NOT NULL
);
```

### 3.2 现有表扩展

```sql
ALTER TABLE holdings ADD COLUMN sim_quantity INTEGER DEFAULT 0;
ALTER TABLE holdings ADD COLUMN sim_cost_price REAL;
ALTER TABLE holdings ADD COLUMN sim_holding_id TEXT; -- 关联到持仓批次

ALTER TABLE ai_strategies ADD COLUMN strategy_param_advice TEXT;
ALTER TABLE ai_strategies ADD COLUMN backtest_result_json TEXT;

ALTER TABLE risk_alerts ADD COLUMN trading_rule_triggered TEXT;
```

### 3.3 核心设计决策

| 决策 | 选型 | 原因 |
|------|------|------|
| 资金单位 | 人民币分（整数存储，展示除100） | 避免浮点精度误差 |
| 成交价 | 实时行情价 + 滑点（±0.1%） | 模拟真实市场冲击 |
| T+1 | 当日买入次日才可卖出 | 符合A股规则 |
| 手续费 | 佣金万2.5 + 印花税（卖出0.1%） | 可配置，接近实盘 |
| 初始资金 | 100,000 元（10000000分） | 用户指定 |

## 四、策略引擎

### 4.1 策略基类接口

```python
class BaseStrategy:
    name: str
    params: dict

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame: ...
    def generate_signals(self, df: pd.DataFrame) -> list[Signal]: ...
    def calculate_position_size(self, account: Account, price: float, atr: float) -> int: ...
    def check_stop_loss(self, holding: SimHolding, current_price: float) -> bool: ...
    def check_take_profit(self, holding: SimHolding, current_price: float) -> bool: ...
```

### 4.2 趋势跟踪策略（trend_tracker）

**买入信号**（需同时满足3条件）：
1. MA5 上穿 MA20（金叉）
2. 收盘价 > MA20 × 1.02（脱离均线2%确认趋势）
3. 成交量 > 过去14日均量（放量确认）

**卖出信号**（满足任一即触发）：
1. MA5 下穿 MA20（死叉）
2. 收盘价 < MA20 × 0.95（跌破均线5%）
3. 持仓亏损触及止损价（成本价 - 1.5 × ATR14）
4. 持仓盈利 > 15% → 启动移动止盈（回撤3%平仓）

**仓位计算**（海龟ATR法）：
```
risk_amount = cash × 2%
stop_distance = 1.5 × ATR(14)
position_qty = floor(risk_amount / stop_distance / 100) × 100  # A股100股整数倍
position_qty = min(position_qty, max_single_position)            # 不超过单只上限
```

**默认参数**：
```json
{
    "ma_short": 5,
    "ma_long": 20,
    "atr_period": 14,
    "atr_stop_multiplier": 1.5,
    "trend_confirm_pct": 0.02,
    "volume_confirm_multiplier": 1.0,
    "risk_per_trade": 0.02,
    "trailing_stop_pct": 0.03,
    "take_profit_pct": 0.15,
    "max_single_position_pct": 0.20
}
```

### 4.3 信号生命周期

```
定时扫描(每5分钟)
  │
  ├─→ 计算监控股的 MA5/MA20/ATR
  ├─→ 判断触发条件
  │
  ├─ 触发买入信号 → 新建 Signal(pending)
  │   ├─ 飞书推送 + WebSocket通知
  │   └─ 前端展示（含AI辅助判断）
  │
  ├─ 触发普通卖出信号 → 新建 Signal(pending)
  │   └─ 同上审批流程
  │
  └─ 触发止损信号 → 新建 Signal(approved=auto)
      └─ 止损自动化，直接生成 Order → Broker撮合
```

### 4.4 风控门禁（risk_guard）

在 Broker 撮合成交前强制检查：

| 规则 | 阈值 | 动作 |
|------|------|------|
| 单笔仓位上限 | ≤ 总资产 20% | 超限时缩减到上限 |
| 日亏损熔断 | 当日亏损 ≥ 5% | 停止当日所有新开仓 |
| 频率限制 | 同一股票同日 ≤ 3次交易 | 拒绝新开仓 |
| 最小现金 | 保留 ≥ 5000元 | 不足时拒绝买入信号 |
| T+1检查 | 当日买入不可卖出 | 拒绝卖出信号 |
| 涨跌停检查 | 触及涨跌停板 | 拒绝对应方向交易 |

## 五、绩效分析

### 5.1 核心指标

| 指标 | 计算公式 | 展示 |
|------|---------|------|
| 累计收益率 | (当前净值 - 初始资金) / 初始资金 | 百分比，红绿 |
| 年化收益率 | (1 + 累计收益率)^(252/交易天数) - 1 | 百分比 |
| 最大回撤 | max(历史峰值净值 - 当前净值) / 历史峰值净值 | 百分比+恢复天数 |
| 夏普比率 | (年化收益 - 无风险利率) / 年化波动率 | 数值 |
| 胜率 | 盈利交易数 / 总交易数 | 百分比 |
| 盈亏比 | 平均盈利 / 平均亏损 | 比值 |
| 交易次数 | 统计期内总成交次数 | 数值 |
| 平均持仓天数 | 总持仓天数 / 交易次数 | 天数 |

### 5.2 可视化

- **净值曲线**：账户净值变化折线图，叠加沪深300基准线
- **月度收益热力图**：12月的收益矩阵
- **盈亏分布**：单笔交易盈亏直方图
- **信号准确率**：批准信号中盈利占比随时间变化

## 六、前端新增页面

### 6.1 策略信号台

- 待审批信号卡片列表（绿色买入/红色卖出）
- 每个信号展示：股票信息、触发条件、建议仓位、AI判断摘要
- [批准] [拒绝] 按钮 + 拒绝理由输入
- 已处理信号历史列表
- 当日/本周审批统计

### 6.2 模拟账户

- 概览卡片：总资产/现金/持仓市值/累计收益率/当日盈亏
- 持仓列表（实时行情、浮动盈亏、止损/止盈线）
- 净值曲线图表（叠加基准线）
- 交易记录表格（含筛选和分页）

### 6.3 策略绩效

- 核心指标卡片（胜率/盈亏比/夏普/最大回撤）
- 月度收益热力图
- 单笔交易盈亏分布图
- 策略参数配置面板（可调整MA窗口/ATR倍数/仓位比例等）

## 七、关键行为约定

### 7.1 模拟账户初始化
- 系统首次启动时自动创建模拟账户，初始资金 100,000 元
- 提供 API `POST /api/trading/account/reset` 手动重置账户

### 7.2 信号过期
- 买入信号：当日收盘前有效，15:00后自动标记为 `expired`
- 卖出信号：触发后3个交易日内有效，超时 `expired`

### 7.3 监控股来源
- 默认使用现有系统活跃持仓列表（`holdings.is_active=True`）
- 支持通过 API `POST /api/trading/watchlist` 添加额外监控股

### 7.4 滑点规则
- 市价买入：当前价 × (1 + 0.1%)
- 市价卖出：当前价 × (1 - 0.1%)
- 限价单：仅在行情价触及/穿越委托价时成交，无滑点

## 八、实施优先级

| 阶段 | 内容 | 依赖 |
|------|------|------|
| **Phase 1** | 数据模型 + 模拟账户 + 撮合引擎 | — |
| **Phase 2** | 策略基类 + 趋势跟踪策略 + 信号引擎 | Phase 1 |
| **Phase 3** | 订单管理 + 风控门禁 | Phase 1+2 |
| **Phase 4** | 绩效分析 + 回测接口 | Phase 3 |
| **Phase 5** | 前端面板（信号台+账户+绩效） | Phase 4 |
| **Phase 6** | 飞书推送 + WebSocket集成 | Phase 3 |

## 八、未包含功能（后续迭代）

以下功能暂不纳入本次升级，待趋势跟踪策略稳定后再考虑：

- 动量策略（第9章）
- 海龟策略（第10章）
- 高频交易框架（第11章）
- 套利策略（第12章）
- 机器学习策略（第13章）
- Backtrader深度集成（第14章）
- 多策略并行对比
- 实盘接口对接
