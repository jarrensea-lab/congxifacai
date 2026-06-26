# 日报与组合状态工作清单

> 对应提交：`c52077c fix: harden reporting and portfolio state`

## 目标

把 Serenity 之外的主流程做稳：日报要能完整归档，飞书只发可控摘要，组合状态以 `data/user_portfolio.json` 为准，小账户风控要能拦住不一致、不可执行的操作建议。

## 完成状态

- [x] 将这组工作拆成独立提交，不和 Serenity 混在一起。
- [x] 提交前单独校验 staged patch。
- [x] 只保留这组 staged 变更时运行 `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q`。
- [x] 提交为 `c52077c fix: harden reporting and portfolio state`。

## 工作清单

### 1. 日报归档与飞书投递

- [x] 在 `scripts/daily_report.py` 中新增 `DEFAULT_ARCHIVE_DIR`、`ARCHIVE_DIR`、`INDEX_FILENAME`、`DELIVERY_STATUS_FILENAME` 常量。
- [x] 支持用 `CONGXI_REPORT_ARCHIVE_DIR` 覆盖报告归档目录，方便测试和 dry run。
- [x] 新增 `save_report_to_obsidian()`，把完整 Markdown 报告写入归档目录。
- [x] 每次保存报告后更新 Obsidian 报告索引。
- [x] 维护 `delivery_status.json`，记录最新投递状态和最近历史。
- [x] 新增 `build_feishu_summary()`，飞书只发送长度受控的摘要，本地保留全文。
- [x] 新增 `push_daily_report_to_feishu()`，统一处理 webhook 推送状态和错误。
- [x] 飞书推送前先保存本地完整报告。
- [x] 飞书推送后再次保存报告状态，确保投递结果写入 `delivery_status.json`。
- [x] 增加回归测试，覆盖报告文件、索引、投递状态。
- [x] 增加回归测试，覆盖飞书摘要截断和“完整报告已保存”提示。

### 2. 小账户执行风控

- [x] 新增 `build_execution_guard()`，计算现金安全垫、可执行预算和一手约束。
- [x] 加入 30% 现金底线逻辑。
- [x] 对 5000 元以下小账户加入单票预算约束。
- [x] 新标的必须买得起 A 股一手 100 股。
- [x] 检测小账户持仓集中度。
- [x] 把不可执行的零碎股建议替换成机器可校验的替代方案。
- [x] 新增 `build_final_action_summary()`，用确定性账户事实覆盖 AI 原始文本里的矛盾数量。
- [x] 空仓组合不继承旧持仓动作。
- [x] 增加报告测试，覆盖零碎股和现金限制。
- [x] 增加报告测试，覆盖空仓最终动作文本。

### 3. 组合 JSON 作为状态源头

- [x] 新增 `backend/app/services/portfolio_store.py`。
- [x] 新增 `default_portfolio_path()`，支持 `CONGXI_PORTFOLIO_PATH` 覆盖，默认指向 `data/user_portfolio.json`。
- [x] 新增 `load_user_portfolio()` 和 `save_user_portfolio()`。
- [x] 新增 `recalculate_portfolio()`，重算成本、市值、浮盈、总值和更新时间。
- [x] 新增 `sync_db_from_user_portfolio()`，把 JSON 组合同步到 SQL 的 `Position` 和 `SimAccount`。
- [x] 清理 SQL 中已经不在 JSON 里的旧持仓。
- [x] 用 JSON 推导出的状态更新 SQL 账户现金和总资产。
- [x] 新增 `apply_trade_to_user_portfolio()`，支持手工买卖更新 JSON。
- [x] 卖出时记录交易和已清仓标的。
- [x] 手工交易后重算组合总额。
- [x] 增加测试，覆盖 JSON 活跃持仓同步到 SQL。
- [x] 增加测试，覆盖空 JSON 组合清理 SQL 旧持仓。
- [x] 增加测试，覆盖卖出交易写回 JSON。

### 4. 运行时接入

- [x] 在 `backend/app/main.py` 中引入 `sync_db_from_user_portfolio()`。
- [x] `_get_holdings_data()` 读取持仓前先同步 JSON 到 SQL。
- [x] 在 `_get_holdings_data()` 中把账户现金从“分”转换成“元”。
- [x] `_get_holdings_data()` 返回 `total_assets`。
- [x] `_fetch_market_data()` 透传 `total_assets`。
- [x] `run_analysis()` 透传 `holdings`、`available_cash`、`total_assets`。
- [x] bot 买入命令同步更新 `data/user_portfolio.json`。
- [x] bot 卖出命令同步更新 `data/user_portfolio.json`。
- [x] 保留 bot 原有数据库行为，同时增加 JSON 同步。

### 5. AI 辩论与风险修复

- [x] 增加 prompt 铁律：现金受限、小账户风险、一手约束、数量自洽。
- [x] `run_debate()` 向角色上下文传入总资产。
- [x] 新增 `_repair_final_decision()`，裁判 JSON 坏掉时生成保守可用决策。
- [x] 新增 `_derive_risk_level()`，数据不足或小账户仍给执行动作时提高风险等级。
- [x] 新增 `_derive_portfolio_risk()`，小账户持仓过度集中时提高风险等级。
- [x] 新增 `_extract_holding_codes()`，用于区分已有持仓和新标的。
- [x] 新增 `_extract_total_assets()`，避免把现金误当总资产。
- [x] 新增 `_extract_first_price()`，从推荐区间提取价格做一手金额校验。
- [x] 新增 `_apply_account_constraints()`，把买不起一手的新标的移入观察名单。
- [x] 保留已有持仓相关建议，同时过滤买不起的新标的。
- [x] 增加回归测试，覆盖数据不足时风险升高。
- [x] 增加回归测试，覆盖小账户仍给执行动作时风险升高。
- [x] 增加回归测试，确认总资产和可用现金分别传入。
- [x] 增加回归测试，覆盖裁判 JSON 损坏后的修复逻辑。
- [x] 增加回归测试，覆盖集中持仓风险。
- [x] 增加回归测试，覆盖买不起一手的新标的过滤。

### 6. Codex 咨询归档

- [x] 新增 `scripts/save_codex_consultation.py`。
- [x] 咨询纪要包含生成时间、来源、讨论摘要和投资免责声明。
- [x] 复用 `save_report_to_obsidian()` 归档咨询纪要。
- [x] CLI 支持日期、标题、归档目录和 stdin 输入。
- [x] 增加回归测试，覆盖咨询纪要归档输出。

## 验证记录

- [x] `git diff --cached --check`
- [x] 第一组单独 staged 时测试：`113 passed in 0.46s`
- [x] 两组提交后最终全量测试：`127 passed in 0.43s`

## 运营核验结果

- [x] 已检查飞书 webhook 配置：当前工作区没有 `.env.local` / `.env`，`FEISHU_WEBHOOK_URL` 未配置，因此无法发送真实飞书测试消息。
- [x] 已检查 AI/API 密钥配置：当前 `DEEPSEEK_API_KEY`、`QWEN_API_KEY`、`TUSHARE_TOKEN` 均未配置，因此不能声明 DeepSeek、Qwen、Tushare 在当前机器上可用。
- [x] 已检查腾讯行情：`TencentDataSource.fetch_batch()` 实测可返回 `sh000001`、`sz399001`、`sz000100` 的现价和涨跌幅。
- [x] 已检查 cron：当前 crontab 存在交易日 15:40 运行 `scripts/daily_report.py` 的日报任务。
- [x] 已检查 launchd：当前 `launchctl list` 未发现恭喜发财相关常驻服务项。
- [x] 已检查日报历史日志：`/tmp/congxi-daily-report.log` 中有旧版 `scripts/daily_report.py` 语法错误记录；当前文件已通过 `py_compile`，该日志不能代表当前代码仍有语法错误。
- [x] 已运行当前代码语法检查：`py_compile` 覆盖 `scripts/daily_report.py`、`backend/app/engine/workshop.py`、`backend/app/main.py`、`backend/app/services/portfolio_store.py`。
- [x] 已准备进入 PR 流程：本 PR 只包含 reporting/portfolio state 相关工作，不包含 Serenity 提交。
- [x] 已尝试推送 PR 分支：`git push -u origin codex/reporting-portfolio-state-pr` 被 GitHub HTTPS 认证拦截，当前机器 `gh auth status` 显示未登录。
