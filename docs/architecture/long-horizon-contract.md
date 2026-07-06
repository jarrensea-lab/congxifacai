# Long Horizon Contract

日期：2026-07-06
范围：v8.1 Long Horizon Integration

## 目标

Long Horizon 层用于沉淀中长期研究纪律，但不能绕过恭喜发财现有交易主链。

```text
长期论文 -> 证据账本 -> Target Pool 生命周期 -> 双评分 -> 报告/提醒 -> 复盘
```

## 核心边界

- 长期论文不是买入建议。
- 长期质量分不能单独触发 `buy`。
- `executable` 仍必须通过市场状态、交易剧本、账户可执行性、仓位预算和缺失数据检查。
- 长期层默认 paper-only，真实候选池和飞书提醒需要用户确认后开启。

## 存储

长期论文默认写入：

```text
data/long_thesis.json
```

测试和报告验证必须优先使用：

```bash
CONGXI_LONG_THESIS_PATH=/tmp/congxi-long-thesis.json
```

## 状态

长期状态扩展在 `TargetPoolStore` 中，但不进入盘中候选扫描。

| 状态 | 含义 | 可交易 |
|---|---|---|
| `long_research` | 有线索，论文未成形 | 否 |
| `long_watch` | 论文初步成立，等待价格或证据 | 否 |
| `accumulation_zone` | 进入长期赔率关注区 | 否 |
| `tactical_watch` | 长期论文成立，短期接近触发 | 否 |
| `thesis_review` | 论文边际弱化，需要复核 | 否 |
| `exit_candidate` | 红线触发或论文破裂 | 只用于退出复核 |

## Evidence Types

长期层进入 `EvidenceLedgerStore` 的证据类型：

| type | 来源 | 用途 |
|---|---|---|
| `long_thesis` | 结构化长期论文 | 长期质量和估值锚点 |
| `long_assumption` | 论文核心假设 | 季度/月度复核 |
| `long_red_line` | 论文红线 | 触发退出或复核 |

每条证据必须包含：

- `evidence_id`
- `code`
- `confidence`
- `source_report_path`
- `data_cutoff_date`

## Scoring Contract

```text
long_quality_score:
  生意质量、瓶颈位置、财务质量、估值锚点、论文完整度、红线风险

tactical_execution_score:
  市场状态、交易剧本、量价、资金流、账户可买、止损空间
```

动作合成：

| 长期质量 | 短期执行 | 输出 |
|---|---|---|
| 强 | 未触发 | `long_watch` 或 `accumulation_zone` |
| 强 | 触发 | `tactical_watch` 后再由短期评分决定是否 `executable` |
| 弱 | 短期强 | 最多 `watching`，禁止追题材 |
| 弱化 | 持仓 | `thesis_review` |
| 红线触发 | 持仓 | `exit_candidate` |

## Failure Handling

| 故障 | 处理 |
|---|---|
| 论文 JSON 损坏 | 返回空 store，不污染生产 |
| 论文过期 | 标记 `stale`，禁止升格可执行 |
| 证据缺 `evidence_id` | 不进入评分贡献 |
| 长期状态进入 active scan | 测试阻断 |
| 红线触发 | 进入 `exit_candidate`，等待人工确认 |
