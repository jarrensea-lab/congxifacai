# 恭喜发财 v7 快速开始

## 1. 配置

在项目根目录创建 `.env.local`：

```bash
cp .env.example .env.local
```

至少配置：

```bash
DEEPSEEK_API_KEY=...
DASHSCOPE_API_KEY=...
FEISHU_WEBHOOK_URL=...
FEISHU_WEBHOOK_ONLY=true
```

## 2. 安装依赖

```bash
pip install -r requirements.txt
```

## 3. 启动 API

本地调试：

```bash
PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

生产常驻：

```bash
scripts/install-congxicai-v7-launchd.sh
```

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

## 4. 跑主报告

```bash
PYTHONPATH=.:backend .venv/bin/python scripts/daily_report.py
```

默认归档到：

```text
/Users/zhuchenyuan/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告
```

验证时可使用临时副本，避免改动真实持仓：

```bash
CONGXI_PORTFOLIO_PATH=/tmp/user_portfolio.json \
CONGXI_REPORT_ARCHIVE_DIR=/tmp/congxi-report \
CONGXI_CANDIDATE_POOL_PATH=/tmp/candidate_pool.json \
FEISHU_WEBHOOK_URL= \
PYTHONPATH=.:backend .venv/bin/python scripts/daily_report.py
```

## 5. 验证

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m ruff check backend scripts
bash -n scripts/congxicai-v7-service.sh scripts/guardian.sh scripts/install-congxicai-v7-launchd.sh
plutil -lint scripts/com.zhuchenyuan.congxicai-v7.plist
```

## 6. 当前主线

- `scripts/daily_report.py` 是次日投资策略主报告入口。
- `backend/app/main.py` 是 FastAPI + APScheduler 常驻入口。
- `backend/app/services/quant_lifecycle.py` 管理标的池和持仓预警。
- `backend/app/services/target_snapshot.py` 汇总单标的数据快照。
- `backend/app/services/target_scoring.py` 给出账户可执行评分。
