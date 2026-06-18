# 多模型分工策略

> 自动提取自 `backend/app/ai/debate.py`、`backend/app/config.py`、`CHANGELOG.md v2.3.0`

## 设计目标

从单一 `qwen3.5:35b` 模型升级为**三模型分工**架构，显著提升生成速度（50%）和质量。

## 角色分配

| 角色 | 模型 | 大小 | 职责 |
|------|------|------|------|
| 猎手 (短线) | `qwen3.5:35b` | 22.8GB | 复杂短线分析，需要大模型的知识广度 |
| 账房 (中低频) | `qwen3.5:9b` | 6.3GB | 估值/趋势分析较直接，9B 胜任 |
| 守夜人 (风控) | `qwen3.5:9b` | 6.3GB | 风险检查逻辑简单，快速模型即可 |
| 裁判 (盘前/复盘) | `deepseek-r1:14b` | 8.6GB | 推理模型擅长综合多方观点做决策 |
| 裁判 (盘中) | `qwen3.5:9b` | 6.3GB | 交易时段速度优先，避免模型切换 |

## 模型选择方法

> 来源: `backend/app/ai/debate.py:156-175`

```python
def _hunter_model(self, fast=False):
    # fast=True: 盘中场景用快速模型 (9B)
    # fast=False: 用主模型 (35B)
    return self.fast_model if fast else self.main_model

def _accountant_model(self):
    return self.fast_model  # 始终 9B

def _guardian_model(self):
    return self.fast_model  # 始终 9B

def _aggregator_model(self, fast=False):
    # fast=True: 盘中速度优先，用 9B 避免模型切换
    # fast=False: 盘前/复盘用 R1 推理模型
    return self.fast_model if fast else self.reasoning_model
```

## Ollama 2-Model 内存限制

当前 Mac 上 Ollama 只能同时驻留 **2 个模型**（VRAM 上限约 60GB）：

| 组合 | 内存 | 可否共存 |
|------|------|----------|
| 35B (32G) + 9B (19G) | 51GB | ✅ 可共存 |
| R1 (42G) + 9B (19G) | 61GB | ✅ 可共存 |
| 35B (32G) + R1 (42G) | 74GB | ❌ 不能共存，需切换模型 (~30s) |

## 调用策略

### 盘前/复盘（全辩论模式）

```
猎手 (35B, ~60s)
  │ 顺序调用 (避免 Ollama 排队)
  ▼
账房 (9B, ~20s)
  │
  ▼
守夜人 (9B, ~20s)
  │
  ▼
裁判 (R1, ~40s)  ← 接受一次模型切换 (~30s)
  │  35B → R1 切换
  │  num_predict=2048  (R1 推理模型需要更多 token)
  ▼
总耗时: ~2.3min
```

### 盘中（快速模式）

```
单次调用 (9B, ~60s)
  │  不分角色，精简提示词
  │  聚焦可执行操作 + 今日一课
  ▼
总耗时: ~1min
```

## 性能提升

| 场景 | 旧方案 | 新方案 | 提升 |
|------|--------|--------|------|
| 盘前辩论 | ~6min | ~3min | 50% |
| 盘中分析 | ~3min | ~1.5min | 50% |

## 配置项

> 来源: `backend/app/config.py:13-16`

| 配置 | 默认值 | 用途 |
|------|--------|------|
| `OLLAMA_MODEL` | `qwen3.5:35b-a3b-q4_K_M` | 主模型（猎手） |
| `OLLAMA_FAST_MODEL` | `qwen3.5:9b` | 快速模型（账房+守夜人） |
| `OLLAMA_REASONING_MODEL` | `deepseek-r1:14b` | 推理模型（裁判） |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |

---

> 来源: `backend/app/ai/debate.py` (class AIDebateEngine)、`CHANGELOG.md` v2.3.0、`backend/app/config.py`
