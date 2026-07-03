# Target Pool Operational Clarity Spec

Date: 2026-07-02
Status: ready for implementation
Review target: report wording, target lifecycle, outside-pool radar, research reference cleanup, and portfolio cash consistency

## Context

Today's production report and follow-up Q&A exposed a concrete product bug: the system can show a stock as "核心主攻/可执行" even when it came from `outside_pool_scan`, has not entered the production target lifecycle, and has not passed the full trigger contract.

This is not a user comprehension issue. It is a data-contract and renderer issue.

The user-facing language must answer four separate questions without mixing them:

1. What do I hold now?
2. What can I buy tomorrow if strict trigger conditions appear?
3. What should only be watched by the radar?
4. What is only research background and must not be treated as a tradable target?

## Verified Current State

| Evidence | Current behavior | Why it is wrong |
|---|---|---|
| `scripts/daily_report.py:545-560` | `_primary_trade_candidate()` falls back from `_first_executable_target()` to `_affordable_outside_targets()[0]`. | A pool-external radar row can become "核心主攻". |
| `scripts/daily_report.py:611-638` | The first section is titled "明日【唯一】实盘狙击标的（可执行）" and renders the fallback item under that heading. | The heading promises executability, but the source may only be a radar candidate. |
| `scripts/daily_report.py:1596-1609` | `outside_pool_scan` is attached only to the in-memory `decision` dict. | Radar rows appear in reports but do not exist in the durable target pool queried later. |
| `backend/app/services/quant_lifecycle.py:222-233` | `TargetPoolStore.VALID_STATUSES` includes `research_reference`, `position`, and other compatibility statuses. | The store allows mixed lifecycle meanings, so "标的池" becomes overloaded. |
| `data/candidate_pool.json` | 7 rows: 1 `watching`, 6 `research_reference`; no `executable`. | User asks "标的池有哪些" and receives mostly untradable research references. |
| `backend/tests/test_daily_report_delivery.py:176-182` | Test asserts an outside-pool scan item appears as `核心主攻`. | The bug is protected by tests. |
| `data/user_portfolio.json` | `available_cash=5374.61`, `cash=6085.61`. | Two cash fields disagree after a real trade. |

## Desired Behavior

### User-facing terms

The report must stop using "标的" as a vague umbrella word in the first screen.

| Term | Meaning | User can do |
|---|---|---|
| 持仓 | User already owns it. | Hold, reduce, sell, or add only if a separate add signal triggers. |
| 可执行标的 | Durable target-pool item that passed account budget, data completeness, and strict trigger gates. | Can prepare a manual buy plan, still requiring final price confirmation. |
| 盘中雷达 | Low-cost or theme-related candidate worth monitoring, but not yet executable. | Watch only; it becomes tradable only after trigger promotion. |
| 研究参照 | Serenity/Sentinel/industry evidence or unaffordable stock. | Research only; not a buy or hold instruction. |
| 剔除 | Failed lifecycle item. | Do not trade unless re-entered by a new evidence package. |

### Strict rule

Only `target_scores` rows with `action in {"buy", "add", "actionable", "executable"}` and no budget/data block may appear under "可执行".

`outside_pool_scan` must never render as "唯一实盘狙击标的". It can render under "盘中雷达", with clear promotion conditions.

## Proposed Change

### 1. Split durable objects by lifecycle purpose

Keep `data/candidate_pool.json` as the production target lifecycle file, but make the report and helper APIs treat it as a strict trading lifecycle:

```text
target_pool
  executable          can be rendered as buy/add candidate
  watching            durable watch item waiting for trigger or missing data
  removed             lifecycle dead/end state
```

Move or hide `research_reference` from the production first-screen path. The minimal implementation can keep the raw JSON rows for compatibility, but all user-facing "标的池总数" counts must default to:

```text
visible_target_pool = executable + watching
research_reference  = research archive count, hidden unless explicitly requested
```

Add a separate durable radar file for pool-external scans:

```text
data/radar_pool.json
  items[code]
    code
    name
    source
    current_price
    lot_value
    trigger_price
    stop_loss
    target_price
    watch_reason
    promotion_required_signals
    last_seen_at
    expires_at
```

Rationale: adding a `radar` status to `TargetPoolStore` would be cheaper but keeps the same overloaded "标的池" language. A separate radar store makes the user question "现在标的池里总共几支" answerable without ambiguity.

### 2. Report renderer contract

Change the first section title and content:

```text
## 一、账户动作与明日可执行标的

- 当前持仓怎么处理：...
- 是否需要卖：...
- 是否需要买：...
- 今日结论：暂无可执行买入 / 有 1 只可执行候选
```

If no executable item exists, render:

```text
- 明日买入：不下单。
- 原因：标的池没有通过预算、数据完整性、价格、量能、成交额、资金流的完整触发项。
- 盘中只观察：见第二节雷达池，不等于可买。
```

If an outside radar candidate exists, render it only in section two:

```text
## 二、盘中雷达池（观察，不等于可买）
```

Each radar row must include:

- current status: `观察`
- promotion condition: price + volume ratio + amount + fund flow
- action after promotion: manual confirmation, then one-lot trial only
- invalidation condition: gap-up chase, net outflow, price below trigger, stale data

### 3. Position handling contract

Real holdings must be rendered before any new buy idea.

For `000629`-style manually entered positions, the report must show concrete levels:

```text
000629 钒钛股份
  当前动作：持有，不加仓
  止损：3.37
  第一目标：3.98
  加仓条件：只有重新触发四合一且不破账户预算时才允许
```

Implementation options:

1. On manual trade input, upsert `PositionWatchStore`.
2. If no position-watch plan exists, derive temporary stop/target from trade history and latest radar/target evidence, and mark it as `derived`.

Recommended MVP: do both. Persist when possible; derive as fallback so reports never go vague.

### 4. Cash source of truth

Make `available_cash` the source of truth for JSON portfolio cash. `cash` should either be removed from writes or kept synchronized as a compatibility alias.

MVP:

- `recalculate_portfolio()` sets `cash = available_cash` when `available_cash` exists.
- `apply_trade_to_user_portfolio()` updates both fields consistently.
- Tests assert the fields cannot diverge after buy/sell.

## Acceptance Criteria

1. When `target_scores` has no executable item but `outside_pool_scan` has affordable candidates, the first section says there is no executable buy.
2. Outside-pool candidates render only under "盘中雷达池（观察，不等于可买）".
3. `backend/tests/test_daily_report_delivery.py` no longer asserts outside-pool rows become `核心主攻`.
4. `research_reference` rows are hidden from default "标的池总数" and moved to a research/archive section or count.
5. The daily report clearly distinguishes `持仓`, `可执行`, `雷达`, `研究参照`, and `剔除`.
6. Manual trades keep `available_cash`, `cash`, and `total_assets` internally consistent.
7. A report generated after the user's `000629` holding shows concrete hold/stop/target action rather than generic stop/target language.
8. Existing daily report delivery still passes and writes to the configured archive without exposing credentials.

## Testing Plan

Run at minimum:

```bash
PYTHONPATH=.:backend .venv/bin/pytest -q \
  backend/tests/test_daily_report_delivery.py \
  backend/tests/test_quant_lifecycle.py \
  backend/tests/test_portfolio_state.py \
  backend/tests/test_small_account_discovery.py
```

Add or update tests for:

- no executable target + outside radar present
- executable target present + radar present
- research references hidden from default report count
- manual buy/sell cash synchronization
- position watch fallback when no stored stop plan exists

## Rollback Plan

The change is renderer/store additive. Rollback can be done by:

1. Restoring old renderer selection behavior.
2. Ignoring `data/radar_pool.json`.
3. Keeping `candidate_pool.json` unchanged.

No destructive migration is required for MVP. If research references are physically moved later, first create a timestamped backup under `data/backups/`.

## Out of Scope

- Automatic order placement.
- Changing the stock-picking model or theme discovery algorithm.
- Rewriting Sentinel or Serenity.
- Guaranteeing profit from a trigger.
- Deleting historical reports or raw evidence files.
- Pulling broker holdings automatically without a separate credential/security design.

## Files Reference

| File | Intended change |
|---|---|
| `scripts/daily_report.py` | Stop promoting outside scan to primary executable; render radar separately; improve first-screen wording. |
| `backend/app/services/quant_lifecycle.py` | Add strict visible-pool helpers and optionally `RadarPoolStore`; keep compatibility statuses internal. |
| `backend/app/services/portfolio_store.py` | Synchronize `available_cash` and `cash`; optionally upsert position watch after manual trades. |
| `backend/tests/test_daily_report_delivery.py` | Reverse the bad assertions and protect the new report contract. |
| `backend/tests/test_quant_lifecycle.py` | Add visible target pool and radar durability tests. |
| `backend/tests/test_portfolio_state.py` | Add cash consistency and position-watch tests. |
| `data/candidate_pool.json` | No destructive edit in code change; cleanup should be explicit and backed up. |

