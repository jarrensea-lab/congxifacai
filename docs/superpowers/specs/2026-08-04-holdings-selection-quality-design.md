# 持仓与选股质量优化设计

## 背景与目标

2026-08-04 的只读核验显示，项目已经能把短视频和资金流线索隔离在研究层，也能对账户最小交易单位和单笔风险做硬门校验；但生产输出仍有四个可改善点：基本面评分过于粗糙、周期股可能因低 PE 获得不恰当加分、实际持仓与新增资格容易在候选池状态上混淆，以及历史推荐价锚可能继续出现在新一日评分审计中。

本次优化服务于 2026-08-05 次日策略，目标是：

1. 把已经采集的营收、毛利率、存货、应收、经营现金流、自由现金流和杠杆指标转成确定性、可审计的基本面质量结果。
2. 对显式标记为周期型的公司禁用“低 PE 自动加分”，只有正常化盈利或现金流证据存在时才允许估值加分。
3. 在所有候选上暴露一手止损风险、风险预算占用和账户集中度，而不只返回“买得起”。
4. 将“已持仓，需要管理”与“允许新增/加仓”拆成两个字段；研究参照状态不得让持仓管理消失，也不得反向生成加仓授权。
5. 标记历史推荐行情与当前评分行情的偏差；偏差超过阈值时，历史买入区间只能留在审计字段，不能作为当前动作依据。
6. 使用真实持仓和候选池的临时副本生成次日策略，禁止下单、禁止飞书推送、禁止污染生产持仓文件。

## 方案比较

### 方案 A：硬门增强（采用）

保留现有六维综合评分和 Target Pool 生命周期，在其下增加独立的财务质量评估、风险审计字段、持仓上下文和历史价锚状态。抖音与 gbrain 内容仍只是研究假设，必须经过已有 OCR、样本和生产晋级门。

优点是改动边界小、可回滚、不会把单条视频变成交易因子，也能直接改善当前持仓和小账户候选。缺点是基本面只提供确定性规则，不尝试预测行业周期拐点。

### 方案 B：重写综合评分权重

把技术面 70%/研究 30% 或现有六维分数改为新的全局权重，并让板块资金流直接影响总分。它可能更快改变排序，但需要至少 60 个独立样本、20 个交易日和完整成本回测；不适合在生成明日策略前直接启用。

### 方案 C：抖音主题直连生产候选

将通信、半导体、CPO 等短视频主题直接映射股票并晋级 `watching` 或 `executable`。该方案来源质量和幸存者偏差不可控，会绕过项目证据门，明确不采用。

## 架构

### 财务质量评估

新增 `backend/app/services/financial_quality.py`，输入标准化 `financial` payload，输出：

- `score`：0–100 的质量分，只使用已提供字段；缺失字段不假装为零质量。
- `coverage`：已覆盖指标比例。
- `earnings_profile`：`cyclical`、`compounder` 或 `unknown`，只接受显式输入，不根据股票名称猜测。
- `flags`：现金流弱、存货/应收快于营收、毛利率下滑、高杠杆、自由现金流为负等可审计标记。
- `valuation_eligible`：周期型公司只有出现正常化盈利或正自由现金流证据时才允许 PE 估值加分。

`composite_score.py` 继续保留基本面 15 分上限，但由该评估器提供质量与估值分解。财务质量不绕过行情、资金流、交易剧本、账户和风险硬门。

### 小账户风险审计

`position_sizing.py` 在现有仓位结果上增加：

- `risk_per_lot`
- `risk_budget_utilization_pct`
- `lot_concentration_pct`

即使因一手风险过高被阻断，这些字段也必须返回。`target_scoring.py` 将它们带入 scorecard，候选池持久化时同步写入 `scoring_decision`。是否可执行仍由现有 `risk_budget_too_small` 硬门决定。

### 持仓与新增资格分离

`score_target()` 新增可选 `is_held` 上下文并输出：

- `position_context`: `held` 或 `not_held`
- `position_management_required`: 是否必须出现在持仓管理区
- `entry_action`: 当前评分允许的新增动作

对已持仓标的，`research_only`、`watch` 或报价阻断只限制新增/加仓，不得解释为“不需要管理”。日报评分入口从真实 `positions` 传入 held codes。

### 历史推荐价锚

新增纯函数比较候选项 `last_recommendation.realtime_quote.price` 与当前评分行情：

- 偏差不超过 5%：`current`
- 偏差超过 5%：`stale_divergence`
- 缺少任一价格：`unverifiable`

结果写入 scorecard 和候选池审计。历史 `buy_range`、目标和说明仍可保留供复盘，但当前 `entry_price`、止损、目标、手数和动作必须来自当次 snapshot 与确定性评分。

### 自选股边界

本 PR 不会把券商手工自选自动导入生产候选，也不会删除手工自选。现有易淘金同步继续保护 `manual_protected_codes`。当前自选与 Target Pool 的差异只作为运营审计，不作为交易授权。

## 数据流

```text
短视频/gbrain 研究线索
  -> research_reference
  -> 当日 quote/kline/fund_flow/financial snapshot
  -> financial quality + historical reference audit
  -> playbook + market regime + per-lot risk sizing
  -> entry_action（新增资格）
  -> position_context（持仓管理）
  -> visible decision gate
  -> 次日策略临时归档
```

## 失败处理

- 财务字段缺失：返回覆盖率和缺失审计，不凭空补值；生产所需 `financial` 仍按原规则 fail-closed。
- 周期属性缺失：使用 `unknown`，不启用周期型 PE 加分，也不因缺失直接否决短线交易。
- 历史价锚偏差：标记失效，不自动改写历史记录。
- 持仓上下文缺失：默认 `not_held`，保持向后兼容。
- 报告所需外部数据不可用：生成明确的降级/阻断策略，不发送买入授权。

## 测试与验收

1. 每个新增行为先写失败测试，再写最小实现。
2. 财务质量覆盖复利型、周期型、现金流恶化、存货/应收异常与字段缺失。
3. 风险审计覆盖一手风险超过预算和买得起但过度集中的候选。
4. 持仓上下文覆盖持仓为 `research_only` 时仍要求管理且禁止新增。
5. 历史推荐价锚覆盖 5% 内、5% 外和无法验证。
6. 运行相关测试、全量 `backend/tests`、ruff、脚本语法检查和 plist 校验。
7. 使用 `CONGXI_PORTFOLIO_PATH`、`CONGXI_CANDIDATE_POOL_PATH`、`CONGXI_REPORT_ARCHIVE_DIR` 临时路径，以及空 `FEISHU_WEBHOOK_URL`，生成并审阅 2026-08-05 策略。

## 非目标

- 不自动下单、撤单、转账或修改飞书提醒逻辑。
- 不把单条抖音资金流数据作为生产买入因子。
- 不在本次 PR 中全局重调策略权重或自动学习参数。
- 不自动修改用户在券商中的手工自选股。
