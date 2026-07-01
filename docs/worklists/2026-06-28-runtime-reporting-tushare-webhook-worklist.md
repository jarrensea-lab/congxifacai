# 恭喜发财运行态、报告归档与 Tushare 数据源修正 Worklist

> 日期：2026-06-28
> 状态：已按用户确认方案执行，并完成本地全量测试与日报实跑复核
> 范围：先修现有 5 件确定事项；Sentinel 自动调度另开后续 worklist。

## 1. 配置归位

- [x] 确认 OHHF 当前未正式运行。
- [x] 确认 OHHF `.env.local` 实际属于恭喜发财历史配置。
- [x] 将 `.env.local` 迁回 `/Volumes/Aino Kishi/AI/workflows/恭喜发财/.env.local`。
- [x] 不在报告或日志中暴露任何 key/token/webhook 内容。
- [x] FastAPI 启动日志不再打印 Webhook URL 片段。

验收：

- 恭喜发财可读取 DeepSeek、Qwen、Tushare 和 Feishu Webhook 配置。
- 不再依赖 OHHF 路径加载运行配置。
- 原 OHHF `.env.local` 未删除，仅完成恭喜发财侧配置归位，避免误伤未确认文件。

## 2. 账户运行态

- [x] 新建 `data/user_portfolio.json`。
- [x] 当前账户状态设为无持仓。
- [x] 可用现金设为 `3085.6` 元。
- [x] 修复 SQLite 默认库父目录缺失导致的持仓同步失败。

验收：

- 日报脚本不再因缺少 `data/user_portfolio.json` 在启动阶段退出。
- AI 报告中的账户约束按小账户空仓处理。
- `sync_db_from_user_portfolio` 可同步空仓账户：`positions_synced=0`，`available_cash=3085.6`。

## 3. 飞书 Webhook-only

- [x] 新增 `FEISHU_WEBHOOK_ONLY=true` 配置。
- [x] 多维表格写入器在 Webhook-only 下不可用。
- [x] 报告引擎在 Webhook-only 下跳过飞书文档创建。
- [x] 保留群机器人 Webhook 摘要推送。

验收：

- 不触发 lark-cli、飞书文档、多维表格、图片上传等 API 通道。
- Webhook 失败时，本地 Markdown 仍然落地。
- `2026-06-28` 日报已完成一次真实 Webhook 卡片推送。

## 4. Tushare 高频新闻真实路径

- [x] 确认高频滚动新闻主目录：
  `/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/Serenity研究/数据采集/tushare-news`
- [x] 确认目录结构：
  - `raw/YYYY-MM-DD/`：原始新闻 JSONL，按来源分文件。
  - `index/latest-status.json`：最近一次采集状态。
  - `digest/`：阶段新闻摘要 Markdown。
- [x] 增加 `CONGXI_TUSHARE_NEWS_ROOT` 配置。
- [x] 默认导入 root 指向司库 Tushare 归档，而不是普通 Horizon 日报目录。
- [x] README/CHANGELOG 记录 Tushare 已购买 2000 积分，数据权限增强。

验收：

- `2026-06-28` 可从默认 root 导入 5674 条原始新闻。
- 其中关键新闻标记 2558 条。
- `index/latest-status.json` 显示最近状态为 `ok`，更新时间约 `2026-06-28T21:30:00+08:00`。

## 5. 交易日报告 Markdown 归档

- [x] 新增统一归档工具 `backend/app/services/report_archive.py`。
- [x] 归档根目录：
  `/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告`
- [x] 归档结构改为：

```text
恭喜发财报告/
└── YYYY/
    └── MM/
        └── YYYY-MM-DD/
            ├── YYYY-MM-DD_日报.md
            ├── YYYY-MM-DD_盘前策略.md
            ├── YYYY-MM-DD_盘中分析.md
            ├── YYYY-MM-DD_盘中分析-午后风控.md
            ├── YYYY-MM-DD_收盘复盘.md
            ├── YYYY-MM-DD_系统状态.md
            └── 日报索引.md
```

验收：

- 日报、盘前策略、盘中分析、收盘复盘和系统状态均落当天日期目录。
- 盘中分析允许按阶段拆分为额外文件，例如午后风控。
- 当天 `日报索引.md` 自动更新。

实跑结果：

- `2026-06-28` 日报已生成：
  `/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告/2026/06/2026-06-28/2026-06-28_日报.md`
- 当天 `日报索引.md` 已包含日报条目。
- `delivery_status.json` 仍保留在归档根目录，用于外部推送状态追踪。

## 6. 数据库与调度复核

- [x] 修复 `backend/data/stock_data.db` 父目录不存在时 SQLite 无法打开的问题。
- [x] 验证辩论快照可写入数据库；smoke 记录已清理。
- [x] 检查 `central` 关键词：当前仓库未发现名为 `central` 的模块或调度配置。
- [x] 检查实际调度：`backend/app/main.py` 内已有 APScheduler 5 个交易时段任务 + Bot 轮询。
- [x] 检查系统层守护：本机 `launchctl list` 未发现恭喜发财相关常驻服务。

结论：

- “自动调度不完全”的原因不是报告引擎没有注册 job，而是应用没有被 launchd 常驻拉起。
- 后续若要全自动运行，应新增/恢复 `~/Library/LaunchAgents/com.zhuchenyuan.congxicai-v7.plist`，让 FastAPI 应用在交易日保持运行，由应用内 APScheduler 触发各报告。

## 验证记录

- `env PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/ -q`：`163 passed`。
- `.venv/bin/python -m ruff check backend scripts`：通过。
- `scripts/daily_report.py`：行情、DeepSeek 四角色、裁判、Markdown 落地、Webhook 推送均成功。
- `import_default_tushare_news_events("2026-06-28")`：导入 5674 条，关键 2558 条。

## 后续不在本次范围

- Sentinel 每日自动调度。
- Sentinel 周报/月报。
- 自动回看 1/3/5/20 日角色预测结果。
- 飞书多维表格、文档和图片通道恢复。
- launchd 常驻服务恢复与交易日开机自启动。
