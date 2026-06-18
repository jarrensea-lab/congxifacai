# 分析提示词

> 自动提取自 `backend/app/ai/prompts.py`
>
> 四大分析场景: 盘前策略、盘中策略、收盘复盘、个股建议

---

## 1. PREMARKET_PROMPT — 盘前分析

**角色**: A股量化交易策略师
**场景**: 每日盘前 (09:00 自动 + 手动触发)
**输入变量**: `{market_summary}` `{hot_topics}` `{fund_flow}`
**策略输出**: 短线 + 中低频 双轨

### 输出结构

```json
{
    "market_prediction": {
        "trend": "上涨/震荡/下跌",
        "confidence": 1-10,
        "key_factors": ["因素列表"],
        "volume_prediction": "放量/缩量/持平"
    },
    "sector_prediction": {
        "strong_sectors": [{"name": "板块名", "reason": "理由"}],
        "weak_sectors": [{"name": "板块名", "reason": "理由"}],
        "rotation_signal": "板块轮动方向"
    },
    "short_term": {
        "strategy_name": "短线策略 (1-5天)",
        "holdings_advice": [{
            "code": "代码", "name": "名称",
            "action": "买入/持有/减仓/卖出",
            "reason": "短线理由", "confidence": "高/中/低"
        }],
        "recommendations": [{
            "code": "代码", "name": "名称",
            "reason": "技术面/资金面/题材",
            "buy_range": "买入区间", "stop_loss": "止损价",
            "target": "目标价", "level": "高/中/低"
        }]
    },
    "mid_low_freq": {
        "strategy_name": "中低频策略 (1-4周)",
        "holdings_advice": [/* 同上结构 */],
        "recommendations": [/* 同上结构 */]
    },
    "risk_warning": "整体风险提示"
}
```

### 完整提示词

```
你是一名专业的 A 股量化交易策略师，精通短线交易和中低频波段操作。

【今日盘前数据】
{market_summary}

【热门板块与资金】
{hot_topics}

【资金流向】
{fund_flow}

【你的任务】
请基于以上数据，完成以下分析。你必须分别从「短线」和「中低频」两个维度给出独立建议。

短线策略 (持股1-5天)：关注技术突破、资金流入、题材热度、量价异动。
  以 MA5/MA10、RSI(14)、量比、分钟级资金流为主要参考。
中低频策略 (持股1-4周)：关注估值合理性、趋势延续性、基本面支撑。
  以 MA20/MA60、PE/PB、ROE、北向资金趋势为主要参考。

【输出要求】请严格按照 JSON 格式输出（不要包含 markdown code fence）。
```

---

## 2. INTRADAY_PROMPT — 盘中分析

**角色**: A股盘中交易策略师
**场景**: 交易日 11:30 / 14:00 定时 + 手动触发
**输入变量**: `{market_summary}` `{alerts_summary}`
**特点**: 聚焦可执行操作，非长期判断

### 输出结构

```json
{
    "market_snapshot": {
        "indices_status": "三大指数盘中表现",
        "breadth": "涨跌家数分布",
        "leading_sectors": ["强势板块"],
        "lagging_sectors": ["弱势板块"],
        "market_tone": "偏多/偏空/震荡"
    },
    "operational_strategy": {
        "overall_action": "积极做多/谨慎做多/观望/减仓/避险",
        "key_tactics": ["操作要点"],
        "buy_opportunities": ["买入时机"],
        "sell_signals": ["卖出信号"]
    },
    "holdings_advice": [{
        "code": "代码", "name": "名称",
        "action": "加仓/持有/减仓/做T/卖出",
        "reason": "操作理由", "urgency": "立即/尾盘/观察"
    }],
    "recommendations": [{
        "code": "代码", "name": "名称",
        "reason": "技术突破/资金异动/板块联动",
        "buy_range": "买入区间", "stop_loss": "止损价",
        "target": "目标价", "level": "高/中/低"
    }],
    "risk_reminder": "盘中风险提醒",
    "position_advice": "当前仓位建议"
}
```

---

## 3. REVIEW_PROMPT — 收盘复盘

**角色**: 专业交易复盘分析师
**场景**: 交易日 15:00 定时 + 手动触发
**输入变量**: `{today_trades}` `{holdings_performance}` `{market_performance}`

### 输出结构

```json
{
    "market_summary": {
        "indices": "三大指数表现",
        "volume": "成交量变化",
        "breadth": "涨跌家数分布"
    },
    "sector_summary": {
        "top_sectors": [{"name": "板块", "change_pct": "涨跌幅", "driver": "驱动因素"}],
        "bottom_sectors": [{"name": "板块", "change_pct": "涨跌幅", "reason": "原因"}]
    },
    "profit_loss_analysis": {
        "total_pnl": "总盈亏",
        "per_stock": [{"code": "代码", "name": "名称", "daily_pnl_pct": "日盈亏%", "total_pnl_pct": "累计%"}],
        "best_performer": "最佳持仓",
        "worst_performer": "最差持仓"
    },
    "short_term": {
        "strategy_name": "短线次日策略",
        "holdings_advice": [/* 操作建议 */],
        "recommendations": [/* 推荐股票 */]
    },
    "mid_low_freq": {
        "strategy_name": "中低频次日策略",
        "holdings_advice": [/* 操作建议 */],
        "recommendations": [/* 推荐股票 */]
    },
    "tomorrow_outlook": "明日总体展望",
    "lessons_learned": ["经验教训1", "经验教训2"]
}
```

---

## 4. SUGGESTION_PROMPT — 个股建议

**角色**: A股交易顾问
**场景**: 按需查询
**输入变量**: `{stock_info}` `{realtime_data}` `{technical_indicators}`

### 输出结构

```json
{
    "short_term": {
        "action": "hold/buy/sell/reduce",
        "suggestion": "短线操作建议",
        "reason": "技术面+资金面",
        "price_targets": {"stop_loss": "止损位", "target": "目标价"},
        "urgency": "立即/尽快/观察"
    },
    "mid_low_freq": {
        "action": "hold/buy/sell/reduce",
        "suggestion": "中低频操作建议",
        "reason": "估值+趋势+基本面",
        "price_targets": {"stop_loss": "止损位", "target": "目标价"},
        "urgency": "立即/尽快/观察"
    }
}
```

---

## 提示词设计原则

1. **双轨输出**: 每个提示词都必须产出短线 + 中低频两套独立建议
2. **结构化约束**: 严格 JSON Schema 约束，避免模型自由发挥
3. **禁止 code fence**: 明确要求不要 markdown 包裹，方便解析
4. **角色区分**: 盘前侧重预测，盘中侧重操作，复盘侧重总结
5. **新手友好**: 从 v2.2.1 起，所有提示词都包含 `knowledge_tips` / `beginner_note` 等新人教育字段

---

> 来源: `backend/app/ai/prompts.py:1-269`
