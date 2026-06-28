# Serenity 候选池维护规则

`data/serenity/theme_candidates.json` 是 Serenity 独立研究流水线的种子候选池，只用于研究排序和证据核验，不生成交易指令。

## 文件结构

- `aliases`: 用户常用主题说法到标准主题的映射，例如 `电网设备 -> AI基建/电力`。
- `candidates`: 标准主题到候选标的数组的映射。

每个候选标的必须包含：

- `name`: 公司名称。
- `code`: 6 位 A 股代码。
- `chokepoint`: 对应的产业链瓶颈。
- `chain_position`: 产业链位置。
- `scores`: Serenity 8 维评分，所有维度都必须是 1-10 的整数。

评分维度固定为：

- `需求确定性`
- `瓶颈强度`
- `传导清晰度`
- `业务纯度`
- `证据强度`
- `市场忽视度`
- `验证速度`
- `下行安全`

## 证据字段

`evidence_items` 可选，但建议每个候选至少有 1 条。每条证据包含：

- `fact`: 当前事实或待核验线索。
- `strength`: `strong`、`medium` 或 `weak`。
- `source`: 来源说明。

如果只有内部方法论线索，还没有公告、财报或订单交叉验证，`strength` 应写为 `weak` 或 `medium`，并在 `verify_next` 中写清下一步核验任务。

## 校验命令

编辑候选池后运行：

```bash
PYTHONPATH=backend .venv/bin/python scripts/validate_serenity_candidates.py
```

校验通过后再运行 Serenity 单测：

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_serenity_analyst.py -q
```

## 安全添加命令

优先使用 `scripts/add_serenity_candidate.py` 添加主题别名或候选。它会：

- 读取现有 JSON。
- 先校验现有候选池，失败则不写入。
- 按主题和代码去重。
- 写入临时文件并再次校验。
- 校验通过后替换原文件。

示例：

```bash
PYTHONPATH=backend .venv/bin/python scripts/add_serenity_candidate.py \
  --theme AI基建/电力 \
  --alias 电网设备 \
  --candidate-json '{"name":"测试电气","code":"300001","chokepoint":"测试瓶颈","chain_position":"测试位置","scores":{"需求确定性":7,"瓶颈强度":8,"传导清晰度":7,"业务纯度":6,"证据强度":5,"市场忽视度":5,"验证速度":6,"下行安全":5},"evidence_items":[{"fact":"测试证据待核验","strength":"medium","source":"手工维护"}],"verify_next":"核验公告、财报和订单。"}'
```

## 边界

- 候选池只维护研究线索，不写买入、卖出、仓位或目标价。
- 新增主题时，优先补 `aliases` 和 3-5 个高质量候选，避免一次性塞入未经核验的大列表。
- 如果候选缺少财报、订单或公告证据，必须在 `verify_next` 中明确下一步核验。
