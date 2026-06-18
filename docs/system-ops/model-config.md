# 模型配置说明

> 自动提取自 `backend/app/config.py`

## Ollama 服务配置

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| 服务地址 | `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API 端点 |
| 主模型 | `OLLAMA_MODEL` | `qwen3.5:35b-a3b-q4_K_M` | 猎手角色使用，复杂短线分析 |
| 快速模型 | `OLLAMA_FAST_MODEL` | `qwen3.5:9b` | 账房+守夜人+盘中裁判，估值/风控 |
| 推理模型 | `OLLAMA_REASONING_MODEL` | `deepseek-r1:14b` | 盘前/复盘裁判，多观点综合决策 |

## 服务配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `SERVER_HOST` | `0.0.0.0` | 后端监听地址 |
| `SERVER_PORT` | `8000` | 后端端口 |
| `FRONTEND_PORT` | `3000` | 前端开发服务器端口 |

## 数据库配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DATABASE_PATH` | `{PROJECT_ROOT}/data/stock_data.db` | SQLite 数据库文件路径 |

## 缓存配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `CACHE_MAX_SIZE` | `1000` | 缓存最大条目数 |
| `CACHE_TTL` | `300` | 默认缓存过期时间 (秒) |

## 通知配置

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| 飞书 Webhook | `FEISHU_WEBHOOK_URL` | `""` | 留空则静默跳过飞书推送 |

## 配置文件位置

- 环境变量文件: `{PROJECT_ROOT}/../.env.local` (项目根目录)
- Python 配置类: `backend/app/config.py`
- 配置单例: `get_settings()` 函数，`@lru_cache()` 缓存

## 配置示例 (.env.local)

```bash
# Ollama 多模型配置
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:35b-a3b-q4_K_M
OLLAMA_FAST_MODEL=qwen3.5:9b
OLLAMA_REASONING_MODEL=deepseek-r1:14b

# 飞书通知（可选，留空则不推送）
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# 服务端口
SERVER_PORT=8000
FRONTEND_PORT=3000
```

## Ollama 客户端参数

> 来源: `backend/app/ai/client.py`

| 参数 | 值 | 说明 |
|------|-----|------|
| `temperature` | 0.7 | 生成温度，控制随机性 |
| `stream` | false | 非流式输出 |
| `keep_alive` | "15m" | 模型驻留内存时间 (减少重复加载) |
| `timeout` | 300s (总) + 10s (连接) | httpx 超时设置 |
| `num_predict` | 0 (默认) / 2048 (R1) | 最大输出 token 数，R1 推理模型需更多 |

---

> 来源: `backend/app/config.py:1-44`、`backend/app/ai/client.py`
