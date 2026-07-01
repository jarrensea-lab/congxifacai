# v7.5 代码与文件审查报告

日期：2026-07-01

## 目标

围绕“选出标的 -> 给出策略 -> 用户按策略操作 -> 盈利复盘”的主目标，审查项目文件、代码入口和运行链路，清理会误导生产运行的历史残留，并标出后续必须优化的结构问题。

## 已处理

### 1. 删除 knowX/教程残留

已删除：

- `data/mypkg/*`
- `data/test_utils.py`
- `data/briefing_state.json`
- `data/last_processed_count`
- `data/news_cache/github_trending.json`
- `data/news_cache/hn_top.json`
- `scripts/knowx-news.sh`
- `scripts/polling-agent.sh`
- `config.json`

原因：这些文件来自 knowX 或 Python 教程样例，不服务 A 股策略、标的池、报告、预警和复盘目标；继续保留会让数据目录和脚本入口混乱。

### 1.1 修正项目级 Agent 身份文件

已替换：

- `AGENTS.md`
- `SYSTEM.md`

原因：这两个文件原本完整描述 knowX 学习助手，会让 Codex/Claude 打开项目时把恭喜发财误识别为 knowX，直接影响后续 agent 行为、脚本入口和数据目录判断。现已改为恭喜发财的策略、标的池、Sentinel/Serenity、报告和验证规则。

### 2. 删除旧生产误导入口

已删除：

- `backend/debate_pool.py`
- `backend/test_reports.py`
- `scripts/guardian.plist`
- `scripts/start_all.sh`

原因：

- `backend/debate_pool.py` 写死旧路径和旧资金样例，容易被误当作真实策略入口。
- `backend/test_reports.py` 是历史测速脚本，存在未定义变量，且 `pyproject.toml` 曾为它保留 lint 豁免。
- `scripts/guardian.plist` 指向 v6 旧路径。
- `scripts/start_all.sh` 仍按旧 Ollama/frontend 流程启动，不符合当前 DeepSeek/Qwen + FastAPI/launchd 主线。

### 3. 修正保留入口

- `scripts/guardian.sh` 已改为 v7/8000 口径，只做监控和本地日志，不管理 API 进程。
- `scripts/QUICKSTART.md` 已改成当前 v7 真实启动、主报告和验证命令。
- `pyproject.toml` 删除了 `backend/test_reports.py` 的历史 lint 豁免。

## 本轮新增主线能力

### 1. 标的池执行分层

- `TargetPoolStore` 支持 `research_reference` 和 `executable` 状态。
- Sentinel/Serenity 研究候选默认进入研究参照，不直接进入可执行策略池。
- 候选池扫描跳过 `research_reference`，避免买不起或仅有产业锚点的标的触发买入预警。

### 2. 最小交易单位与账户可执行性

- 新增 `lot_size_for_code()`。
- 科创板 `688/689` 按 200 股一手计算，其余 A 股默认 100 股。
- 标的入池时记录一手金额、账户预算、阻断原因。

### 3. 标的数据快照与评分

新增：

- `backend/app/services/target_snapshot.py`
- `backend/app/services/target_scoring.py`

覆盖：

- 实时行情
- K 线
- 个股资金流
- 北向/沪深港通资金
- 个股新闻/公告类新闻
- 财务指标
- Sentinel 证据
- Serenity 深挖结果

评分输出不再泛化写“数据不足，建议观望”，而是明确写缺哪一类数据、为什么不能买、什么条件后才能迁移。

### 4. 主报告重构

`scripts/daily_report.py` 默认输出 v7.5 七段主报告：

1. 今日账户操作策略
2. 持仓处理策略
3. 新开仓策略
4. 标的池分层
5. 数据覆盖与评分
6. 复盘与自迭代
7. 研究归档链接

默认不再追加旧的“市场概况/AI 多维度/个股建议”长结构。旧结构仅通过 `CONGXI_REPORT_LEGACY_SECTIONS=1` 保留为兼容路径。

## 保留但需要后续优化

### P1. 报告引擎边界仍不彻底

现状：

- `backend/app/report_engine/` 已存在，但主报告仍主要在 `scripts/daily_report.py` 内构建。
- `scripts/daily_report.py` 仍有 1300+ 行，包含数据抓取、AI 辩论、标的评分、Markdown 渲染、归档、飞书推送。

影响：

- 后续新增盘前/盘中/收盘模板时容易重复逻辑。
- 报告结构修改风险仍集中在单文件。

建议：

- v7.6 将 `build_next_day_strategy_sections()`、标的池表格、数据覆盖表和归档链接迁入 `backend/app/report_engine/templates/next_day.py`。
- `scripts/daily_report.py` 只保留 CLI 编排。

### P1. Serenity 分析模块过大

现状：

- `backend/app/ai/serenity_analyst.py` 约 1592 行。

建议拆分：

- `serenity_candidate_extraction.py`
- `serenity_scoring.py`
- `serenity_financial_bridge.py`
- `serenity_report_renderer.py`

目的：让 Serenity 的“研究证据”与“策略评分影响”更容易审计。

### P1. FastAPI 主入口过重

现状：

- `backend/app/main.py` 约 936 行，混合应用启动、调度任务、报告任务、生命周期扫描和推送状态。

建议：

- 把 APScheduler jobs 拆到 `backend/app/scheduler/jobs.py`。
- 把盘前/午盘/午后/收盘任务拆为独立 service。
- `main.py` 保留 app 初始化、路由注册和健康检查。

### P2. 真实报告运行中财务接口输出进度条

现象：

- 真实运行 `scripts/daily_report.py` 时，财务证据抓取会输出 `tqdm` 进度条。

影响：

- launchd 日志和 Codex 输出噪声增加。

建议：

- 在财务 fetcher 增加 quiet 参数，或在 daily report 调用时禁用进度输出。

### P2. 数据源缺口需要进入自迭代看板

真实报告验证中出现：

- 部分标的缺 `kline`
- 部分标的缺 `fund_flow`
- 买不起的一手金额已正确归为 `research_reference`

建议：

- 建 `data/source_coverage.json` 或数据库表，按标的/数据源/日期记录缺口。
- 连续缺口超过阈值时发送飞书运维预警，而不是让策略层沉默。

### P2. launchd 模板仍是个人绝对路径

现状：

- `scripts/com.zhuchenyuan.congxicai-v7.plist` 保留本机绝对路径。

判断：

- 当前项目是个人工作流，可以接受。
- 若后续迁移到多机器，应改成由 install 脚本生成 plist，不直接维护固定路径模板。

## 本轮不删除的内容

- `.env.local`：敏感配置，已被 Git 忽略，不能提交。
- `.venv/`、`.pytest_cache/`、`.ruff_cache/`、`__pycache__/`：本地缓存，已被 Git 忽略，本轮不纳入 PR。
- `backend/data/stock_data.db*`：本地数据库，已被 Git 忽略，本轮不删除。
- 历史 docs/worklists：保留作为项目演进依据，但后续可做归档索引。

## 验证记录

- 单元测试：`PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q`
- 静态检查：`.venv/bin/python -m ruff check backend scripts`
- 脚本语法：`bash -n scripts/congxicai-v7-service.sh scripts/guardian.sh scripts/install-congxicai-v7-launchd.sh`
- launchd 模板：`plutil -lint scripts/com.zhuchenyuan.congxicai-v7.plist`
- 应用烟测：`app.version == 7.5.0-dev`，`/api/market/health` handler 返回 `v7.5.0-dev`
- 真实报告脚本：使用临时持仓、临时标的池、临时报告目录跑通，生成 v7.5 七段主报告。
- 生成报告确认：无旧“市场概况/AI 多维度”标题，无“数据不足，建议观望”泛化兜底。

## 结论

本轮已清理会直接干扰生产主线的历史残留，并把报告主线、标的池分层、账户可执行性和数据评分接入同一目标：只输出能服务“买/不买/何时买/买多少/错了怎么止损/后续如何复盘”的信息。
