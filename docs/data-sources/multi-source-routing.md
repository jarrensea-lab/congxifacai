# 多源容错策略

> 自动提取自 `backend/app/data_sources/data_router.py`

## 数据源优先级

```
首选: 腾讯财经 (TencentDataSource)
  ↓ 失败
备选1: 新浪财经 (SinaDataSource)
  ↓ 失败
备选2: 东方财富 (EastmoneyDataSource)
  ↓ 全部失败
熔断绕过: 重试所有源 (忽略熔断，缩时超时)
```

## 断路器机制

### 状态追踪

每个数据源维护 `(fail_count, last_fail_time, open_until)` 三元组：

- `fail_count`: 累计失败次数
- `last_fail_time`: 最后失败时间戳
- `open_until`: 熔断解除时间 (当前时间 + cooldown)

### 熔断冷却时间

指数退避策略，底数为 2，上限 5 分钟：

| 失败次数 | 冷却时间 |
|----------|----------|
| 1 | 30s |
| 2 | 60s |
| 3 | 120s |
| 4 | 240s |
| 5+ | 300s (上限) |

```python
cooldown = min(30 * 2 ** (fail_count - 1), 300)
```

### 成功恢复

每次成功后 `fail_count -= 1` (不低于 0)，逐步降低熔断门槛。

## 路由逻辑

### `fetch(stock_code, priority="balanced")`

```
1. 遍历数据源列表，跳过 open_until 未到期的源
2. 调用 source.fetch(stock_code)，8 秒超时
3. 检查 price > 0 → 成功，记录成功并返回
4. price <= 0 或超时 → 记录失败，尝试下一个
5. 全部失败 → 熔断绕过模式:
   - 重试所有源 (忽略断路器状态)
   - 超时缩短为 5 秒
   - 任一返回非空结果即返回
6. 全部失败 → 返回 None
```

## 日志输出

```
# 熔断警告
"数据源 tencent 第3次失败，熔断120秒"

# 熔断跳过
"数据源 tencent 熔断中，跳过"

# 熔断绕过
"熔断绕过: 使用 sina"
```

## 设计要点

### 为什么需要熔断绕过？
当所有数据源都熔断时，宁可降低超时重试，也不能完全断数据。这保证了极端情况下仍有数据可用（虽然可能是过期或降级的数据）。

### 为什么 price > 0 才算成功？
部分数据源在异常时返回空字典或 `price=0`，这类结果无实际用途，应视为失败。

### 为什么成功后降级计数而非归零？
`fail_count -= 1` 而非 `fail_count = 0`，避免间歇性故障被快速"原谅"。需要连续成功多次才能完全恢复信任。

---

> 来源: `backend/app/data_sources/data_router.py`
