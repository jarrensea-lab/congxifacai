# v8.1 Long Horizon Integration Plan

日期：2026-07-06
范围：`/Users/zhuchenyuan/AI/workflows/恭喜发财`
状态：plan ready for review

## 0. 结论

这不是给恭喜发财“加一个长线模块”。

目标是把 `ai-berkshire` 式中长期投研纪律接入恭喜发财现有生产主链，让系统同时回答两类问题：

```text
中长期：哪些公司值得持续研究、等待和配置？原来的论文是否仍成立？
短期：今天/明天能不能动，买多少，什么价格，错了哪里止损？
```

最终形态是统一投资操作系统：

```text
研究发现
  -> 证据归一
  -> 长短线分层
  -> 账户可执行过滤
  -> 剧本触发
  -> 报告/飞书提醒
  -> 持仓论文复核
  -> 复盘校准
```

## 1. gstack Autoplan Review Summary

### CEO Review

采用 **Selective Expansion**：保留恭喜发财“盈利操作系统”的生产主线，但把中长期投研能力作为上游质量层和持仓后纪律层接入。

不能做成长期报告归档中心。长期研究只有进入 `Evidence Ledger`、`Target Pool`、`Target Scoring`、报告和复盘，才算对系统有用。

### Engineering Review

现有可复用能力：

| 能力 | 现有文件 | 复用方式 |
|---|---|---|
| 证据账本 | `backend/app/services/evidence_ledger.py` | 增加 long thesis / quality screen / thesis drift 证据类型 |
| 标的生命周期 | `backend/app/services/quant_lifecycle.py` | 扩展长期状态，不新建旁路池 |
| 单标的数据快照 | `backend/app/services/target_snapshot.py` | 加入长期质量、估值锚点、论文状态 |
| 评分与动作 | `backend/app/services/target_scoring.py` | 拆出 long quality score + tactical execution score |
| 产业链瓶颈 | `backend/app/ai/serenity_analyst.py` | 对齐 bottleneck hunter 思路，增强可验证假设和红线 |
| 报告入口 | `scripts/daily_report.py` | 第一屏仍动作优先，新增长期依据摘要和复核状态 |
| 预警链路 | `notification_gate.py` + `quant_lifecycle.py` | 论文破裂、估值进入积累区、红线触发进入飞书提醒 |

工程原则：先定义数据契约和回归测试，再改报告和调度。避免一次性改大模型 prompt、报告渲染、持仓规则和评分权重。

### Design Review

这是报告/信息架构设计，不是视觉 UI 设计。第一屏仍然必须先回答账户动作，长期层只能作为“为什么这样动作”的证据摘要。

报告信息顺序：

```text
1. 今日账户动作
2. 持仓论文健康度和红线
3. 今日可执行/观察/雷达
4. 长期机会与积累区
5. 数据覆盖、评分、研究归档
6. 复盘和下次验证点
```

### DX Review

开发者体验重点不是外部 SDK，而是本地工作流可运行、可验证、可回滚：

- 所有新增数据结构必须有示例 JSON。
- 所有新增 CLI 必须支持临时目录环境变量，不能污染真实持仓。
- 报告验证命令必须能一条跑完。
- 失败时必须说明缺哪类数据、哪个契约未满足、是否阻断推送。

## 2. 核心对象与状态

### 2.1 Long Thesis

新增结构化长期论文对象，不直接等同于交易建议。

```json
{
  "symbol": "002123",
  "name": "示例公司",
  "horizon": "6-36m",
  "thesis_status": "forming",
  "core_thesis": "五句话以内说明为什么值得跟踪",
  "assumptions": [
    {
      "id": "a1",
      "claim": "收入增速连续两个季度改善",
      "verification": "quarterly_financial",
      "frequency": "quarterly",
      "status": "unverified"
    }
  ],
  "red_lines": [
    {
      "id": "r1",
      "condition": "核心客户订单被证伪",
      "severity": "critical",
      "action": "exit_review"
    }
  ],
  "valuation_anchor": {
    "fair_zone": [0, 0],
    "accumulation_zone": [0, 0],
    "overpriced_zone": [0, 0],
    "method": "pe_ps_fcf_cross_check"
  },
  "quality_score": 0,
  "confidence": "B",
  "evidence_ids": []
}
```

### 2.2 Lifecycle States

扩展 Target Pool，而不是新建孤立长线池。

```text
research_reference
  -> long_research
  -> long_watch
  -> accumulation_zone
  -> tactical_watch
  -> executable
  -> position
  -> thesis_review
  -> exit_candidate
  -> removed
```

状态含义：

| 状态 | 含义 | 能否交易 |
|---|---|---|
| `long_research` | 有研究线索，论文未成形 | 否 |
| `long_watch` | 论文初步成立，等待价格/证据 | 否 |
| `accumulation_zone` | 长期赔率进入可关注区 | 否，需短期触发 |
| `tactical_watch` | 长期论文成立，短期条件接近 | 否，等剧本触发 |
| `executable` | 长期/短期/账户/风控均通过 | 可人工复核 |
| `thesis_review` | 持仓或观察标的论文弱化 | 不加仓，需复核 |
| `exit_candidate` | 红线触发或论文破裂 | 减仓/退出候选 |

### 2.3 Two-Score Contract

同一只股票必须拆成两套评分：

```text
long_quality_score:
  生意质量、产业链瓶颈、财务质量、管理层/治理、估值锚点、论文完整度、红线风险

tactical_execution_score:
  市场状态、交易剧本、量价、资金流、催化、账户可买、止损空间
```

动作合成规则：

| 长期质量 | 短期执行 | 动作 |
|---|---|---|
| 强 | 未触发 | `long_watch` / `accumulation_zone` |
| 强 | 触发 | `tactical_watch` -> `executable` |
| 弱 | 短期强 | 禁止追题材，最多 `watching` |
| 论文弱化 | 持仓 | `thesis_review` |
| 红线触发 | 持仓 | `exit_candidate` |

## 3. 数据流

```text
ai-berkshire style research
  quality screen / bottleneck / thesis / drift / news pulse
          |
          v
Long Thesis Adapter
          |
          v
Evidence Ledger
          |
          v
Target Pool lifecycle
          |
          v
Target Snapshot + Target Scoring
          |
          +--------------------+
          |                    |
          v                    v
Long Quality Decision     Tactical Execution Decision
          |                    |
          +---------+----------+
                    v
              Report / Feishu
                    |
                    v
             Performance Ledger
```

Shadow paths:

- 无长期论文：保持现有短线评分，不阻断生产日报。
- 长期论文存在但过期：标记 `thesis_stale`，不允许升格 `executable`。
- 长期好但买不起：只能 `research_reference` 或 `long_watch`。
- 长期红线触发：持仓进入 `exit_candidate`，并推送飞书复核提醒。
- 外部研究数据不可信：只写 `evidence confidence=C`，不参与买入分。

## 4. Implementation Worklist

### P0. 契约和测试先行

目标：先把长期层的数据契约固定，避免报告先变复杂。

- [x] 新增 long thesis 示例文件：`data/examples/long_thesis.example.json`。
- [x] 新增状态说明文档：`docs/architecture/long-horizon-contract.md`。
- [x] 为 `TargetPoolStore.VALID_STATUSES` 增加长期状态。
- [x] 增加测试：长期状态不能直接进入 `active_items()` 可交易扫描。
- [x] 增加测试：`research_reference -> long_research -> long_watch` 不会进入盘中可交易扫描。

验证：

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q backend/tests/test_quant_lifecycle.py
```

### P1. Long Thesis Store

目标：持久化中长期论文，支持建立、读取、复核、状态迁移。

- [x] 新增 `backend/app/services/long_thesis.py`。
- [x] 支持 `CONGXI_LONG_THESIS_PATH` 临时路径。
- [x] 定义 `LongThesisStore.load/get/upsert/append_review`。
- [x] 每条论文必须包含 assumptions、red_lines、valuation_anchor、evidence_ids。
- [x] 增加红线触发状态：`healthy`、`weakened`、`broken`、`stale`。

验证：

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q backend/tests/test_long_thesis.py
```

### P2. Evidence Ledger Adapter

目标：把 ai-berkshire 式研究输出拆成可追踪证据。

- [x] 扩展 `EvidenceLedgerStore` 支持首批长期论文证据类型：
  - `long_thesis`
  - `long_assumption`
  - `long_red_line`
- [ ] 后续扩展 `EvidenceLedgerStore` 支持 ai-berkshire 全量研究证据类型：
  - `quality_screen`
  - `bottleneck_map`
  - `thesis_review`
  - `thesis_drift`
  - `news_pulse`
- [x] 增加 `build_long_horizon_evidence(...)`。
- [x] 要求每条长期证据写入 `confidence`、`source_report_path`、`data_cutoff_date`。
- [x] `build_long_horizon_evidence(...)` 为长期论文、假设和红线生成稳定 `evidence_id`。

验证：

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q backend/tests/test_profit_evidence_pipeline.py backend/tests/test_sentinel_contracts.py
```

### P3. Scoring Split

目标：拆分长期质量分和短期执行分。

- [x] 在 `target_scoring.py` 增加 `score_long_quality(snapshot, thesis)`。
- [x] 保留现有 `score_target()` 作为短期执行主入口。
- [x] `score_target()` 接受可选 `long_thesis`，但只能通过合成规则影响状态，不可单独触发 `buy`。
- [x] 把当前 `serenity_score * 0.3` 改为长期质量输入的一部分：有 `long_thesis` 时使用长期质量分，否则保持旧 Serenity 分路径。
- [x] 输出字段增加：
  - `long_quality_score`
  - `thesis_status`
  - `valuation_zone`
  - `red_line_status`
  - `combined_decision_reason`
- [x] 红线触发时输出 `long_thesis_broken`，阻断买入动作并要求先做 thesis review。

验证：

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q backend/tests/test_target_scoring.py
```

### P4. Serenity / Bottleneck Upgrade

目标：把 Serenity 从“产业链讲解”升级为长期论文输入。

- [x] 在 `serenity_analyst.py` 输出中补齐：
  - `long_assumptions`
  - `red_lines`
  - `valuation_questions`
  - `quarterly_verification_tasks`
  - `bottleneck_duration`
- [x] 增加瓶颈持续性判断：一次性、1-2 季度、1-3 年、结构性。
- [x] 增加 “替代路径 / 被绕过风险”。
- [x] 借鉴 quality-screen 做去劣红线，而不是只做主题评分。
- [x] Serenity 归档报告增加“长期论文输入”区，保持 research-only，不输出交易动作。

验证：

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q backend/tests/test_serenity_analyst.py backend/tests/test_serenity_financial_evidence.py
```

### P5. Report Integration

目标：日报第一屏仍动作优先，但能解释长期依据。

- [x] `scripts/daily_report.py` 增加长期依据摘要区。
- [x] 持仓/标的评分审计增加论文状态解释：
  - `论文成立`
  - `边际弱化`
  - `红线触发`
  - `论文过期`
- [x] 新开仓策略增加“本次动作性质”：
  - 短线试错
  - 长期建仓
  - 长期加仓
  - 风险退出
- [x] `long_watch`、`accumulation_zone` 只出现在长期依据/机会解释区，不出现在第一屏可执行买入区。
- [x] 研究全文仍归档，不压过操作结论。

验证：

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q backend/tests/test_daily_report_delivery.py backend/tests/test_report_engine.py
```

### P6. Feishu Event Pulse

目标：让长期机会和论文破裂能被提醒，但不制造噪音。

- [ ] 新增长期事件类型：
  - `valuation_entered_accumulation_zone`
  - `thesis_red_line_triggered`
  - `thesis_weakened`
  - `thesis_stale`
  - `long_to_tactical_watch`
- [ ] 复用 `notification_gate.py` 的冷却和聚合。
- [ ] 飞书卡只推“需要人工复核”的长期事件，不推完整研究。

验证：

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q backend/tests/test_notification_gate.py backend/tests/test_quant_lifecycle.py
```

### P7. CLI 和导入流程

目标：能把已有研究报告或手工结论导入系统。

- [ ] 新增 `scripts/import_long_thesis.py`。
- [ ] 支持从 JSON 输入导入，不先做 Markdown 解析。
- [ ] 支持 `--dry-run` 显示会写入哪些 evidence / thesis / target 状态。
- [ ] 支持临时路径环境变量，避免污染真实数据。
- [ ] 后续再考虑 Markdown 半自动抽取。

验证：

```bash
CONGXI_LONG_THESIS_PATH=/tmp/long_thesis.json \
CONGXI_EVIDENCE_LEDGER_PATH=/tmp/evidence_ledger.jsonl \
PYTHONPATH=.:backend .venv/bin/python scripts/import_long_thesis.py --dry-run data/examples/long_thesis.example.json
```

## 5. Testing Map

```text
NEW DATA FLOWS
  long thesis json -> LongThesisStore -> EvidenceLedger -> TargetPool -> Scoring -> Report
    happy: valid thesis imports and appears as long_watch
    missing: no thesis, existing short-term report still works
    stale: stale thesis cannot promote executable
    red line: broken thesis triggers exit_candidate

NEW CODEPATHS
  LongThesisStore
    unit tests: load/upsert/append_review/malformed json
  score_long_quality
    unit tests: assumptions/red lines/valuation zones
  score_target + long_thesis
    regression tests: long good cannot bypass tactical trigger
  daily_report long summary
    integration tests: first screen remains action-first

NEW ERROR PATHS
  malformed thesis json
  missing evidence_id
  stale data_cutoff_date
  conflicting lifecycle state
  Feishu push failure
```

Minimum full gate before shipping:

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q backend/tests/test_long_thesis.py backend/tests/test_target_scoring.py backend/tests/test_quant_lifecycle.py backend/tests/test_daily_report_delivery.py backend/tests/test_profit_evidence_pipeline.py
.venv/bin/python -m ruff check backend scripts
git diff --check
```

## 6. Failure Modes Registry

| Codepath | Failure mode | Rescue | Test | User impact |
|---|---|---|---|---|
| Thesis import | JSON malformed | reject dry-run/write, no partial mutation | yes | no report pollution |
| Evidence adapter | missing evidence_id | block scoring contribution | yes | shows low confidence |
| Scoring split | long quality bypasses trigger | rule blocks buy without tactical trigger | yes | avoids false buy |
| Report renderer | long watch shown as executable | bucket tests | yes | avoids misleading trade |
| Feishu event | repeated thesis stale alerts | notification gate cooldown | yes | avoids spam |
| Data freshness | old thesis treated current | stale status | yes | asks for复核 |

## 7. Rollout Plan

1. Land P0-P2 behind inert paths. No report behavior change except tests and examples.
2. Land P3 scoring split with regression tests proving no new buy path appears.
3. Land P4 Serenity long thesis fields.
4. Land P5 report integration using temporary report archive and candidate pool.
5. Land P6 Feishu event pulse with notification gate cooldown.
6. Run one paper week: long thesis events can alert, but cannot drive buy without existing tactical trigger.
7. After one week, review false positives and decide whether to let `accumulation_zone -> tactical_watch` enter daily radar automatically.

## 7.1 Trigger Ownership

默认执行原则：

```text
结构、测试、离线导入、paper-only 报告摘要：Codex 自动触发和推进。
真实数据行为、飞书提醒、生产候选池状态迁移、交易建议权限变化：必须用户确认。
```

### Codex 自动触发

以下阶段不需要用户手动触发。只要用户说“继续 / 按计划做 / 从 P0 开始”，Codex 应自行推进到该阶段验收点：

| 阶段 | 触发者 | 条件 | 允许动作 |
|---|---|---|---|
| P0 契约和测试 | Codex | 用户确认执行 plan | 新增示例、文档、测试，不改生产行为 |
| P1 Long Thesis Store | Codex | P0 测试通过 | 新增 store 和临时路径支持 |
| P2 Evidence Adapter | Codex | P1 测试通过 | 新增 evidence 类型和 dry-run 写入能力 |
| P3 Scoring Split | Codex | P0-P2 通过 | 改评分结构，但必须保持长期分不能触发 `buy` |
| P4 Serenity Upgrade | Codex | P3 回归通过 | 增加长期论文字段，不改变交易建议权限 |
| P5 Report Summary | Codex | P4 测试通过 | 在临时报告或 paper-only 模式展示长期依据 |

Codex 自动触发时必须使用临时路径或 inert mode，避免污染真实文件：

```bash
CONGXI_LONG_THESIS_PATH=/tmp/congxi-long-thesis.json \
CONGXI_EVIDENCE_LEDGER_PATH=/tmp/congxi-evidence-ledger.jsonl \
CONGXI_CANDIDATE_POOL_PATH=/tmp/congxi-candidate-pool.json \
CONGXI_REPORT_ARCHIVE_DIR=/tmp/congxi-report \
FEISHU_WEBHOOK_URL=
```

### 用户手动触发

以下阶段必须由用户明确说“开启 / 允许 / 接入真实生产”后才能执行：

| 阶段 | 触发者 | 为什么需要确认 |
|---|---|---|
| 写入真实 `data/candidate_pool.json` 的长期状态 | 用户 | 会改变生产标的池语义 |
| 将 `accumulation_zone` 自动提升到日报雷达 | 用户 | 会改变用户看到的机会列表 |
| 开启长期事件飞书提醒 | 用户 | 会改变通知频率和注意力负担 |
| 让长期论文状态影响真实持仓处理建议 | 用户 | 影响卖出/减仓语义 |
| paper-only 转 production | 用户 | 从观察层进入生产决策层 |

### Trigger Gates

每个自动阶段结束后，Codex 必须给出：

- 已完成文件。
- 验证命令和结果。
- 是否仍处于 inert / paper-only。
- 是否触达真实持仓、真实候选池或飞书。
- 下一阶段是否属于自动触发还是用户手动触发。

如果下一阶段属于“用户手动触发”，Codex 必须停止并等待确认。

## 8. Not In Scope

- 自动下单。
- 自动解析任意 Markdown 研究报告并直接入池。
- 改变真实持仓文件作为测试输入。
- 把长期研究全文塞进日报第一屏。
- 用长期质量分直接覆盖 `market_regime`、`playbook`、`position_sizing`。
- 承诺长期收益。

## 9. Pause Conditions

必须暂停并让用户决定：

- 新状态会改变真实持仓或真实候选池的生产数据。
- 需要接入券商、账户登录、验证码或私有凭证。
- 长期评分与短期执行分出现冲突，且规则无法判断动作。
- 报告可能把 `long_watch` 写成买入建议。
- Feishu 事件提醒可能导致过量推送或暴露研究隐私。

## 10. Parallelization

| Lane | Work | Modules | Depends on |
|---|---|---|---|
| A | Long Thesis Store + examples | `backend/app/services/`, `data/examples/` | P0 |
| B | Evidence adapter | `evidence_ledger.py`, tests | P0 |
| C | Serenity output upgrade | `backend/app/ai/` | P0 |
| D | Scoring split | `target_scoring.py` | A+B |
| E | Report integration | `scripts/daily_report.py` | D |
| F | Feishu event pulse | `notification_gate.py`, lifecycle scan | D |

Execution:

```text
P0 sequential
  -> A + B + C in parallel
  -> D
  -> E + F in parallel
  -> full regression + one paper week
```

## 11. Acceptance Criteria

- 长期论文可以被结构化导入，并写入 Evidence Ledger。
- 长期论文状态能影响 `long_watch / accumulation_zone / thesis_review / exit_candidate`。
- 长期质量分不能单独产生 `buy`。
- `executable` 仍必须通过账户、市场状态、交易剧本、仓位预算和缺失数据检查。
- 日报第一屏仍先回答持仓、买、卖、不动和触发条件。
- 长期区能解释“为什么值得等”和“哪些事实会让它不值得等”。
- 飞书只提醒长期层的状态变化，不推完整研究报告。
- 所有真实报告验证支持临时路径，不污染真实持仓和候选池。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Status | Findings |
|---|---|---|---|---|
| CEO Review | `/autoplan` + `/plan-ceo-review` | Scope and strategy | complete | use selective expansion, not a standalone module |
| Eng Review | `/autoplan` + `/plan-eng-review` | Architecture and tests | complete | reuse Evidence Ledger, Target Pool, Snapshot, Scoring |
| Design Review | `/autoplan` + `/plan-design-review` | Report information architecture | complete | action-first report, long-term evidence below action |
| DX Review | `/autoplan` + `/plan-devex-review` | CLI/docs/local verification | complete | examples, dry-run, temp paths, explicit errors required |
