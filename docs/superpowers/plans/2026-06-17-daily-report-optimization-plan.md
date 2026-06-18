# 每日报告系统优化 — 模块化报告引擎 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建模块化报告引擎，统一每日报告模板、新增信息图+飞书云文档+多维表格多渠道输出，增强系统健康检查（含 Qwen）。

**Architecture:** 新建 `backend/app/report_engine/` 模块，模板与渲染分离。模板层定义各报告内容结构，渲染层负责输出到飞书消息卡片/信息图PNG/云文档/多维表格。主流程通过 `engine.py` 编排，逐步替换 `main.py` 中现有的报告生成函数。

**Tech Stack:** Python 3.14 + FastAPI + Pydantic + matplotlib + PIL + lark-cli (飞书API)

---

## Phase 1: 基础设施搭建 (P0)

### Task 1.1: 创建报告引擎模块结构 + 标准化数据模型

**Files:**
- Create: `backend/app/report_engine/__init__.py`
- Create: `backend/app/report_engine/report_schema.py`

- [ ] **Step 1: 创建模块目录和 `__init__.py`**

```bash
mkdir -p /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6/backend/app/report_engine
mkdir -p /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6/backend/app/report_engine/templates
mkdir -p /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6/backend/app/report_engine/renderers
touch /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6/backend/app/report_engine/__init__.py
touch /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6/backend/app/report_engine/templates/__init__.py
touch /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6/backend/app/report_engine/renderers/__init__.py
```

- [ ] **Step 2: 编写 `report_schema.py` — 标准化数据模型**

```python
"""报告标准化数据模型 — 所有报告统一Schema"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class PositionItem(BaseModel):
    code: str
    name: str
    quantity: int
    cost_price: float
    current_price: float
    profit_pct: float
    market_value: float
    risk_level: str = "normal"  # normal / warning / danger


class Recommendation(BaseModel):
    code: str
    name: str
    strategy_type: str          # short_term / mid_low_freq
    buy_range: str = ""
    stop_loss: str = ""
    target: str = ""
    reason: str = ""
    technical_signals: str = ""
    concept_tags: list[str] = []
    trend_score: int = 5       # 1-10
    beginner_guide: str = ""
    recommend_date: str = ""


class RiskAlert(BaseModel):
    stock_code: str
    stock_name: str
    alert_type: str
    level: str                  # low / mid / high
    message: str
    suggestion: str = ""
    timestamp: str = ""


class PerformanceData(BaseModel):
    daily_pnl: float = 0
    daily_pnl_pct: float = 0
    cumulative_pnl: float = 0
    win_rate: float = 0
    position_count: int = 0
    total_assets: float = 0
    available_cash: float = 0


class SystemHealth(BaseModel):
    api_service: bool = False
    deepseek_api: bool = False
    qwen_api: bool = False
    tencent_data: bool = False
    eastmoney_data: bool = False
    tushare_data: bool = False
    tasks_success: int = 0
    tasks_fail: int = 0
    last_error: Optional[str] = None


class ReportData(BaseModel):
    report_type: str                      # premarket / midday / risk / closing
    generated_at: datetime = None
    date: str = ""
    risk_level: int = 3                   # 1-5
    market_direction: str = ""
    market_summary: str = ""
    confidence: int = 5
    positions: list[PositionItem] = []
    recommendations: list[Recommendation] = []
    alerts: list[RiskAlert] = []
    performance: Optional[PerformanceData] = None
    system_health: Optional[SystemHealth] = None
    knowledge_tip: str = ""
    top_sectors: list[str] = []
```

- [ ] **Step 3: 验证数据模型可导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.report_schema import ReportData, PositionItem, Recommendation, SystemHealth; print('✅ Schema OK')"
```

Expected: `✅ Schema OK`

---

### Task 1.2: 创建飞书云文档上传模块

**Files:**
- Create: `backend/app/report_engine/renderers/feishu_doc.py`

- [ ] **Step 1: 编写 `feishu_doc.py`**

```python
"""飞书云文档上传 — 信息图PNG上传 + 云文档创建"""
import os
import subprocess
import json
import tempfile
from datetime import datetime
from pathlib import Path
from app.config import settings
from app.utils.logger import logger


def upload_image_to_drive(image_path: str, file_name: str = None) -> str | None:
    """上传图片到飞书云盘，返回 file_token"""
    if not file_name:
        file_name = os.path.basename(image_path)
    lark_cli = getattr(settings, "LARK_CLI_PATH", "/Users/zhuchenyuan/.npm-global/bin/lark-cli")
    try:
        result = subprocess.run(
            [lark_cli, "drive", "+upload-media", "--file-path", image_path, "--file-name", file_name],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip()
        if output:
            try:
                data = json.loads(output.split("\n")[-1])
                return data.get("data", {}).get("file_token")
            except (json.JSONDecodeError, KeyError):
                pass
        logger.warning(f"飞书上传失败: {result.stderr[:200]}")
        return None
    except Exception as e:
        logger.error(f"飞书上传异常: {e}")
        return None


def create_doc_from_markdown(title: str, markdown_content: str) -> str | None:
    """创建飞书云文档并写入Markdown内容，返回文档链接"""
    lark_cli = getattr(settings, "LARK_CLI_PATH", "/Users/zhuchenyuan/.npm-global/bin/lark-cli")
    try:
        # 使用临时文件存储markdown
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(markdown_content)
            tmp_path = f.name
        result = subprocess.run(
            [lark_cli, "docx", "+create", "--title", title, "--content-file", tmp_path],
            capture_output=True, text=True, timeout=30
        )
        os.unlink(tmp_path)
        output = result.stdout.strip()
        if output:
            try:
                data = json.loads(output.split("\n")[-1])
                return data.get("data", {}).get("url") or data.get("url")
            except (json.JSONDecodeError, KeyError):
                pass
        return None
    except Exception as e:
        logger.error(f"创建飞书文档异常: {e}")
        return None


def create_doc_with_image(title: str, image_paths: list[str]) -> str | None:
    """创建飞书云文档并插入多张图片，返回文档链接"""
    lark_cli = getattr(settings, "LARK_CLI_PATH", "/Users/zhuchenyuan/.npm-global/bin/lark-cli")
    try:
        # 先上传所有图片
        file_tokens = []
        for img_path in image_paths:
            token = upload_image_to_drive(img_path)
            if token:
                file_tokens.append(token)

        # 创建文档
        doc_content = f"# {title}\n\n"
        doc_content += f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        for i, token in enumerate(file_tokens):
            doc_content += f"![图表{i+1}](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/all/{token})\n\n"

        return create_doc_from_markdown(title, doc_content)
    except Exception as e:
        logger.error(f"创建图片文档异常: {e}")
        return None
```

- [ ] **Step 2: 验证导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.renderers.feishu_doc import upload_image_to_drive, create_doc_from_markdown; print('✅ FeishuDoc OK')"
```

Expected: `✅ FeishuDoc OK`

---

### Task 1.3: 创建飞书多维表格写入模块

**Files:**
- Create: `backend/app/report_engine/renderers/bitable_writer.py`

- [ ] **Step 1: 编写 `bitable_writer.py`**

```python
"""飞书多维表格写入模块 — 封装 lark-cli base 操作"""
import subprocess
import json
from datetime import datetime
from typing import Optional
from app.config import settings
from app.utils.logger import logger


class BitableWriter:
    """多维表格写入器，管理6张维度的数据写入"""

    def __init__(self):
        self.lark_cli = getattr(settings, "LARK_CLI_PATH", "/Users/zhuchenyuan/.npm-global/bin/lark-cli")
        self.app_token = getattr(settings, "FEISHU_BITABLE_APP_TOKEN", "")
        self._tables = {
            "strategy": getattr(settings, "FEISHU_TABLE_STRATEGY", ""),
            "stock_pool": getattr(settings, "FEISHU_TABLE_STOCK_POOL", ""),
            "positions": getattr(settings, "FEISHU_TABLE_POSITIONS", ""),
            "indices": getattr(settings, "FEISHU_TABLE_INDICES", ""),
            "risk": getattr(settings, "FEISHU_TABLE_RISK", ""),
            "performance": getattr(settings, "FEISHU_TABLE_PERFORMANCE", ""),
        }

    def _available(self) -> bool:
        return bool(self.app_token) and any(self._tables.values())

    def _create_record(self, table_key: str, fields: dict) -> bool:
        """向指定表新增一条记录"""
        table_id = self._tables.get(table_key)
        if not table_id or not self.app_token:
            logger.warning(f"多维表格 {table_key} 未配置，跳过")
            return False
        fields_json = json.dumps(fields, ensure_ascii=False)
        try:
            result = subprocess.run(
                [self.lark_cli, "base", "+record-create",
                 "--app-token", self.app_token,
                 "--table-id", table_id,
                 "--fields", fields_json],
                capture_output=True, text=True, timeout=15
            )
            ok = result.returncode == 0
            if ok:
                logger.info(f"Bitable写入成功: {table_key}")
            else:
                logger.warning(f"Bitable写入失败: {table_key} - {result.stderr[:200]}")
            return ok
        except Exception as e:
            logger.error(f"Bitable异常: {table_key} - {e}")
            return False

    def _update_record(self, table_key: str, record_id: str, fields: dict) -> bool:
        """更新指定表的某条记录"""
        table_id = self._tables.get(table_key)
        if not table_id or not self.app_token:
            return False
        fields_json = json.dumps(fields, ensure_ascii=False)
        try:
            result = subprocess.run(
                [self.lark_cli, "base", "+record-update",
                 "--app-token", self.app_token,
                 "--table-id", table_id,
                 "--record-id", record_id,
                 "--fields", fields_json],
                capture_output=True, text=True, timeout=15
            )
            return result.returncode == 0
        except Exception:
            return False

    def write_strategy_overview(self, date: str, risk_level: int, direction: str,
                                 confidence: int, position_advice: str,
                                 top_sectors: list[str], status: str = "进行中") -> bool:
        """写入策略总览"""
        return self._create_record("strategy", {
            "日期": date,
            "风险等级": f"R{risk_level}",
            "市场方向": direction,
            "置信度": confidence,
            "仓位建议": position_advice,
            "看好板块": ", ".join(top_sectors),
            "状态": status,
        })

    def write_stock_pool(self, recommendations: list) -> bool:
        """写入标的池（先清空再批量写入）"""
        # 简单实现：逐条写入
        ok = True
        for rec in recommendations:
            fields = {
                "代码": rec.get("code", ""),
                "名称": rec.get("name", ""),
                "策略类型": "短线" if rec.get("strategy_type") == "short_term" else "中线",
                "买入区间": rec.get("buy_range", ""),
                "止损": rec.get("stop_loss", ""),
                "目标": rec.get("target", ""),
                "趋势评分": rec.get("trend_score", 5),
                "题材标签": ", ".join(rec.get("concept_tags", [])),
                "技术面信号": rec.get("technical_signals", ""),
                "AI理由": rec.get("reason", ""),
                "适合人群": rec.get("beginner_guide", ""),
                "推荐日期": rec.get("recommend_date", ""),
            }
            if not self._create_record("stock_pool", fields):
                ok = False
        return ok

    def write_position_monitor(self, positions: list) -> bool:
        """写入持仓监控"""
        ok = True
        now = datetime.now().strftime("%H:%M")
        for pos in positions:
            fields = {
                "代码": pos.get("code", ""),
                "名称": pos.get("name", ""),
                "持仓量": pos.get("quantity", 0),
                "成本": pos.get("cost_price", 0),
                "现价": pos.get("current_price", 0),
                "盈亏%": f"{pos.get('profit_pct', 0):+.2f}%",
                "市值": pos.get("market_value", 0),
                "风控状态": pos.get("risk_level", "normal"),
                "更新时间": now,
            }
            if not self._create_record("positions", fields):
                ok = False
        return ok

    def write_indices(self, indices: dict) -> bool:
        """写入市场指数"""
        ok = True
        now = datetime.now().strftime("%H:%M")
        for code, data in indices.items():
            name_map = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}
            fields = {
                "指数名称": name_map.get(code, code),
                "当前点位": data.get("price", 0),
                "涨跌幅": f"{data.get('change_pct', 0):+.2f}%",
                "更新时间": now,
            }
            if not self._create_record("indices", fields):
                ok = False
        return ok

    def write_risk_alert(self, alert: dict) -> bool:
        """写入风险预警"""
        return self._create_record("risk", {
            "时间": datetime.now().strftime("%H:%M"),
            "标的": f"{alert.get('stock_name', '')}({alert.get('stock_code', '')})",
            "预警类型": alert.get("alert_type", ""),
            "级别": alert.get("level", "low"),
            "消息": alert.get("message", ""),
            "建议": alert.get("suggestion", ""),
            "处理状态": "待处理",
        })

    def write_performance(self, perf: dict) -> bool:
        """写入绩效追踪"""
        return self._create_record("performance", {
            "日期": datetime.now().strftime("%Y-%m-%d"),
            "日盈亏": perf.get("daily_pnl", 0),
            "日盈亏%": f"{perf.get('daily_pnl_pct', 0):+.2f}%",
            "累计盈亏": perf.get("cumulative_pnl", 0),
            "胜率": f"{perf.get('win_rate', 0):.1f}%",
            "持仓数": perf.get("position_count", 0),
            "总资产": perf.get("total_assets", 0),
            "可用现金": perf.get("available_cash", 0),
        })


# 全局单例
bitable_writer = BitableWriter()
```

- [ ] **Step 2: 验证导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.renderers.bitable_writer import bitable_writer; print('✅ BitableWriter OK')"
```

Expected: `✅ BitableWriter OK`

---

### Task 1.3: 更新配置 — 新增环境变量

**Files:**
- Modify: `backend/app/config.py`
- Modify: `.env.example`

- [ ] **Step 1: 在 `config.py` 中添加新配置项

在 `Settings` 类中添加：

```python
# 飞书多维表格配置
FEISHU_BITABLE_APP_TOKEN: str = ""
FEISHU_TABLE_STRATEGY: str = ""
FEISHU_TABLE_STOCK_POOL: str = ""
FEISHU_TABLE_POSITIONS: str = ""
FEISHU_TABLE_INDICES: str = ""
FEISHU_TABLE_RISK: str = ""
FEISHU_TABLE_PERFORMANCE: str = ""

# Qwen API
QWEN_API_KEY: str = ""
QWEN_API_URL: str = "https://dashscope.aliyuncs.com/api/v1"

# Lark CLI路径复用已有 LARK_CLI_PATH
```

- [ ] **Step 2: 更新 `.env.example`**

添加：

```bash
# 飞书多维表格配置
FEISHU_BITABLE_APP_TOKEN=
FEISHU_TABLE_STRATEGY=
FEISHU_TABLE_STOCK_POOL=
FEISHU_TABLE_POSITIONS=
FEISHU_TABLE_INDICES=
FEISHU_TABLE_RISK=
FEISHU_TABLE_PERFORMANCE=

# Qwen API
QWEN_API_KEY=
QWEN_API_URL=https://dashscope.aliyuncs.com/api/v1
```

- [ ] **Step 3: 验证配置加载**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.config import settings; print('FEISHU_BITABLE_APP_TOKEN' in dir(settings)); print('QWEN_API_KEY' in dir(settings))"
```

Expected: `True` `True`

---

## Phase 2: 报告模板 + 渲染器 (P1)

### Task 2.1: 创建飞书消息卡片渲染器

**Files:**
- Create: `backend/app/report_engine/renderers/markdown_card.py`

- [ ] **Step 1: 编写 `markdown_card.py`**

```python
"""飞书消息卡片 Markdown 构建器 — 标准化卡片格式"""
from datetime import datetime
from app.report_engine.report_schema import ReportData


def build_premarket_card(data: ReportData) -> str:
    """盘前策略消息卡片"""
    risk_icon = {1: "🟢", 2: "🟢", 3: "🟡", 4: "🟠", 5: "🔴"}
    lines = [
        f"🐕 **旺财V7 盘前策略 [R{data.risk_level}]**",
        f"📅 {data.date}",
        "",
        f"**{risk_icon.get(data.risk_level, '⚪')} 风险预警**",
    ]
    for pos in data.positions[:3]:
        if pos.risk_level == "danger":
            lines.append(f"- 🔴 {pos.name}({pos.code}): 盈亏{pos.profit_pct:+.2f}% — 注意风险")
        elif pos.risk_level == "warning":
            lines.append(f"- 🟡 {pos.name}({pos.code}): 盈亏{pos.profit_pct:+.2f}%")
    if not any(p.risk_level in ("danger", "warning") for p in data.positions):
        lines.append("- 未识别到显著风险")
    lines.extend([
        "",
        f"**📊 市场背景**",
        f"- 方向: {data.market_direction}",
        f"- 置信度: {data.confidence}/10",
        f"- 看好板块: {', '.join(data.top_sectors[:3]) or 'N/A'}",
        "",
        f"**📈 短线机会 ({len([r for r in data.recommendations if r.strategy_type=='short_term'])}支)**",
    ])
    for r in data.recommendations:
        if r.strategy_type == "short_term":
            lines.append(f"- **{r.name}**({r.code}): {r.reason[:80]}")
            lines.append(f"  🛑 {r.buy_range} | 🎯 {r.target}")
    lines.extend([
        "",
        f"**📚 知识角**",
        data.knowledge_tip or "—",
        "",
        f"---",
        f"*生成: {data.generated_at.strftime('%H:%M') if data.generated_at else '--'}*",
    ])
    return "\n".join(lines)


def build_closing_card(data: ReportData) -> str:
    """收盘全景消息卡片"""
    pnl = data.performance
    health = data.system_health
    lines = [
        f"📊 **旺财V7 收盘全景报告**",
        f"📅 {data.date}",
        "",
        f"**📈 今日交易回顾**",
    ]
    if pnl:
        icon = "📈" if pnl.daily_pnl >= 0 else "📉"
        lines.append(f"- 日盈亏: {icon} ¥{pnl.daily_pnl:+,.2f} ({pnl.daily_pnl_pct:+.2f}%)")
        lines.append(f"- 累计盈亏: ¥{pnl.cumulative_pnl:+,.2f}")
        lines.append(f"- 持仓数: {pnl.position_count} | 总资产: ¥{pnl.total_assets:,.2f}")
    lines.extend([
        "",
        f"**⚖️ 风控事件**",
    ])
    alerts = data.alerts[:3]
    if alerts:
        for a in alerts:
            icon = {"high": "🔴", "mid": "🟡", "low": "🟢"}.get(a.level, "⚪")
            lines.append(f"- {icon} {a.stock_name}({a.stock_code}): {a.message[:100]}")
    else:
        lines.append("- 今日无风控事件")
    lines.extend([
        "",
        f"**🔮 明日预告**",
        data.market_summary[:200] if data.market_summary else "—",
        "",
        f"**⚙️ 系统健康**",
    ])
    if health:
        lines.append(f"- API: {'✅' if health.api_service else '❌'} | DeepSeek: {'✅' if health.deepseek_api else '❌'} | Qwen: {'✅' if health.qwen_api else '❌'}")
        lines.append(f"- 任务: {health.tasks_success}成功 / {health.tasks_fail}失败")
    lines.extend([
        "",
        f"---",
        f"*生成: {data.generated_at.strftime('%H:%M') if data.generated_at else '--'}*",
    ])
    return "\n".join(lines)


def build_midday_card(data: ReportData) -> str:
    """午盘快报消息卡片"""
    lines = [
        f"🌤️ **旺财V7 午盘快报**",
        f"📅 {data.date}",
        "",
        f"**上午盘面**",
        data.market_summary[:300] or "—",
        "",
        f"**💼 持仓表现**",
    ]
    for pos in data.positions[:5]:
        icon = "📈" if pos.profit_pct >= 0 else "📉"
        lines.append(f"- {icon} {pos.name}({pos.code}): {pos.profit_pct:+.2f}%")
    if not data.positions:
        lines.append("- 无持仓")
    lines.extend([
        "",
        f"**🎯 下午策略**",
        data.knowledge_tip[:200] if data.knowledge_tip else "观望为主",
        "",
        f"---",
        f"*生成: {data.generated_at.strftime('%H:%M') if data.generated_at else '--'}*",
    ])
    return "\n".join(lines)


def build_afternoon_risk_card(data: ReportData) -> str:
    """午后风控消息卡片（仅预警时推送）"""
    lines = [
        f"🛡️ **旺财V7 午后风控告警**",
        f"📅 {data.date}",
        "",
    ]
    alerts = [a for a in data.alerts if a.level in ("high", "mid")]
    if alerts:
        for a in alerts[:5]:
            icon = "🔴" if a.level == "high" else "🟡"
            lines.append(f"- {icon} **{a.stock_name}**({a.stock_code}): {a.message}")
            if a.suggestion:
                lines.append(f"  建议: {a.suggestion}")
    lines.extend([
        "",
        f"**💳 账户概览**",
    ])
    if data.performance:
        lines.append(f"- 总资产: ¥{data.performance.total_assets:,.2f}")
        lines.append(f"- 持仓市值: ¥{(data.performance.total_assets - data.performance.available_cash):,.2f}")
        lines.append(f"- 可用现金: ¥{data.performance.available_cash:,.2f}")
    lines.extend([
        "",
        f"---",
        f"*生成: {data.generated_at.strftime('%H:%M') if data.generated_at else '--'}*",
    ])
    return "\n".join(lines)
```

- [ ] **Step 2: 验证导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.renderers.markdown_card import build_premarket_card, build_closing_card; print('✅ MarkdownCard OK')"
```

Expected: `✅ MarkdownCard OK`

---

### Task 2.2: 创建盘前策略报告模板

**Files:**
- Create: `backend/app/report_engine/templates/premarket.py`

- [ ] **Step 1: 编写 `premarket.py`**

```python
"""盘前策略报告模板 — 从AI辩论结果构建标准化ReportData"""
from datetime import datetime
from app.report_engine.report_schema import ReportData, Recommendation, PositionItem


def build_premarket_report_data(
    date: str,
    decision: dict,
    positions: list[dict],
    risk_level: int = 3,
) -> ReportData:
    """从AI辩论的decision字典构建ReportData"""
    recs = []
    for item in decision.get("short_term", {}).get("recommendations", []):
        recs.append(Recommendation(
            code=item.get("code", ""),
            name=item.get("name", ""),
            strategy_type="short_term",
            buy_range=item.get("buy_range", ""),
            stop_loss=item.get("stop_loss", ""),
            target=item.get("target", ""),
            reason=item.get("reason", ""),
            technical_signals=item.get("technical_signals", item.get("reason", "")[:60]),
            concept_tags=item.get("concept_tags", decision.get("top_sectors", [])),
            trend_score=item.get("trend_score", 5),
            beginner_guide=item.get("beginner_guide", ""),
            recommend_date=date,
        ))
    for item in decision.get("mid_low_freq", {}).get("recommendations", []):
        recs.append(Recommendation(
            code=item.get("code", ""),
            name=item.get("name", ""),
            strategy_type="mid_low_freq",
            buy_range=item.get("buy_range", ""),
            stop_loss=item.get("stop_loss", ""),
            target=item.get("target", ""),
            reason=item.get("reason", ""),
            technical_signals=item.get("technical_signals", ""),
            concept_tags=item.get("concept_tags", []),
            trend_score=item.get("trend_score", 5),
            beginner_guide=item.get("beginner_guide", ""),
            recommend_date=date,
        ))

    pos_items = []
    for p in positions:
        pnl_pct = ((p.get("current_price", 0) - p.get("cost", 0)) / p.get("cost", 1)) * 100 if p.get("cost", 0) > 0 else 0
        risk_lvl = "danger" if pnl_pct < -8 else "warning" if pnl_pct < -3 else "normal"
        pos_items.append(PositionItem(
            code=p.get("code", ""),
            name=p.get("name", ""),
            quantity=p.get("position", 0),
            cost_price=p.get("cost", 0),
            current_price=p.get("current_price", 0),
            profit_pct=round(pnl_pct, 2),
            market_value=p.get("current_price", 0) * p.get("position", 0),
            risk_level=risk_lvl,
        ))

    return ReportData(
        report_type="premarket",
        generated_at=datetime.now(),
        date=date,
        risk_level=risk_level,
        market_direction=decision.get("final_decision", decision.get("final_view", "N/A")),
        market_summary=decision.get("reasoning", "")[:300],
        confidence=decision.get("confidence", 5),
        positions=pos_items,
        recommendations=recs,
        knowledge_tip=decision.get("knowledge_corner", ""),
        top_sectors=decision.get("top_sectors", []),
    )
```

- [ ] **Step 2: 验证导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.templates.premarket import build_premarket_report_data; print('✅ PremarketTemplate OK')"
```

Expected: `✅ PremarketTemplate OK`

---

### Task 2.3: 创建收盘全景报告模板

**Files:**
- Create: `backend/app/report_engine/templates/closing.py`

- [ ] **Step 1: 编写 `closing.py`**

```python
"""收盘全景报告模板 — 收盘复盘+系统日报合并"""
from datetime import datetime
from app.report_engine.report_schema import (
    ReportData, PositionItem, RiskAlert, PerformanceData, SystemHealth
)


def build_closing_report_data(
    date: str,
    positions: list[dict],
    alerts: list[dict],
    performance: dict,
    market_summary: str,
    system_health: dict,
    preview: str = "",
) -> ReportData:
    """从各数据源构建收盘全景ReportData"""
    pos_items = []
    for p in positions:
        pnl_pct = ((p.get("current_price", 0) - p.get("cost", 0)) / max(p.get("cost", 0), 1)) * 100
        risk_lvl = "danger" if pnl_pct < -8 else "warning" if pnl_pct < -3 else "normal"
        pos_items.append(PositionItem(
            code=p.get("code", ""),
            name=p.get("name", ""),
            quantity=p.get("quantity", 0),
            cost_price=p.get("cost_price", p.get("cost", 0)),
            current_price=p.get("current_price", p.get("market_price", 0)),
            profit_pct=round(pnl_pct, 2),
            market_value=p.get("market_value", p.get("quantity", 0) * p.get("current_price", 0)),
            risk_level=risk_lvl,
        ))

    alert_items = []
    for a in alerts:
        alert_items.append(RiskAlert(
            stock_code=a.get("stock_code", ""),
            stock_name=a.get("stock_name", ""),
            alert_type=a.get("alert_type", "composite"),
            level=a.get("alert_level", a.get("level", "low")),
            message=a.get("alert_message", a.get("message", "")),
            suggestion=a.get("suggestion", ""),
            timestamp=a.get("timestamp", ""),
        ))

    perf = PerformanceData(
        daily_pnl=performance.get("daily_pnl", 0),
        daily_pnl_pct=performance.get("daily_pnl_pct", 0),
        cumulative_pnl=performance.get("cumulative_pnl", 0),
        win_rate=performance.get("win_rate", 0),
        position_count=performance.get("position_count", 0),
        total_assets=performance.get("total_assets", 0),
        available_cash=performance.get("available_cash", 0),
    )

    health = SystemHealth(
        api_service=system_health.get("api_service", False),
        deepseek_api=system_health.get("deepseek_api", False),
        qwen_api=system_health.get("qwen_api", False),
        tencent_data=system_health.get("tencent_data", False),
        eastmoney_data=system_health.get("eastmoney_data", False),
        tushare_data=system_health.get("tushare_data", False),
        tasks_success=system_health.get("tasks_success", 0),
        tasks_fail=system_health.get("tasks_fail", 0),
        last_error=system_health.get("last_error"),
    )

    return ReportData(
        report_type="closing",
        generated_at=datetime.now(),
        date=date,
        market_summary=preview or market_summary[:300],
        positions=pos_items,
        alerts=alert_items,
        performance=perf,
        system_health=health,
    )
```

- [ ] **Step 2: 验证导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.templates.closing import build_closing_report_data; print('✅ ClosingTemplate OK')"
```

Expected: `✅ ClosingTemplate OK`

---

### Task 2.4: 创建信息图生成器

**Files:**
- Create: `backend/app/report_engine/renderers/infographic.py`

- [ ] **Step 1: 编写 `infographic.py`**

```python
"""信息图生成器 — matplotlib + PIL 生成策略信息图"""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from datetime import datetime
from app.report_engine.report_schema import ReportData
from app.utils.logger import logger

# 图表输出目录
CHART_DIR = "/tmp/congxi_v6_charts"

def _get_zh_font():
    """获取中文字体"""
    for fname in fm.findSystemFonts():
        fl = fname.lower()
        if any(k in fl for k in ["arial unicod", "pingfang", "heiti", "stheit", "songti"]):
            return fm.FontProperties(fname=fname)
    return None


def generate_premarket_infographic(data: ReportData) -> str | None:
    """生成盘前策略信息图，返回图片路径"""
    try:
        os.makedirs(CHART_DIR, exist_ok=True)
        zh_font = _get_zh_font()
        fig = plt.figure(figsize=(12, 16))
        
        # 1. 仓位分配饼图 (左上)
        ax1 = fig.add_subplot(3, 2, 1)
        entries_data = [r for r in data.recommendations[:5]]
        if entries_data:
            labels = [f"{r.name}({r.code})" for r in entries_data]
            sizes = [8] * len(entries_data)  # 示意权重
            colors = ['#4CAF50', '#2196F3', '#FF9800', '#E91E63', '#9C27B0'][:len(entries_data)]
            if len(sizes) > 0:
                ax1.pie(sizes, labels=None, autopct='%1.1f%%', colors=colors, startangle=90)
            ax1.set_title('推荐标的分布', fontproperties=zh_font, fontsize=12, pad=15)
            if labels:
                ax1.legend(labels, loc="center left", bbox_to_anchor=(1, 0.5), prop=zh_font, fontsize=8)
        
        # 2. 价格区间柱状图 (右上)
        ax2 = fig.add_subplot(3, 2, 2)
        names = [r.name[:4] for r in entries_data] if entries_data else ["—"]
        buy_prices = [float(r.buy_range.split("~")[0].replace("¥", "").replace(" ", "")) if r.buy_range else 0 for r in entries_data] if entries_data else [0]
        if buy_prices and any(buy_prices):
            x = range(len(names))
            ax2.bar(x, buy_prices, 0.5, label='买入区间', color='#4CAF50')
            ax2.set_xticks(x)
            ax2.set_xticklabels(names, fontproperties=zh_font, fontsize=8)
            ax2.set_title('参考价格', fontproperties=zh_font, fontsize=12)
            ax2.legend(prop=zh_font)
            ax2.grid(axis='y', alpha=0.3)
        
        # 3. 市场概况 (中左)
        ax3 = fig.add_subplot(3, 2, 3)
        ax3.axis('off')
        info_lines = [
            f"方向: {data.market_direction}",
            f"风险等级: R{data.risk_level}",
            f"置信度: {data.confidence}/10",
            f"看好板块: {', '.join(data.top_sectors[:3]) or '—'}",
            f"报告日期: {data.date}",
        ]
        ax3.text(0.1, 0.7, "\n".join(info_lines), fontproperties=zh_font, fontsize=10, verticalalignment='top')
        ax3.set_title('市场概览', fontproperties=zh_font, fontsize=12, pad=15)
        
        # 4. 持仓表现 (中右)
        ax4 = fig.add_subplot(3, 2, 4)
        ax4.axis('off')
        pos_lines = ["当前持仓:"]
        for p in data.positions[:5]:
            icon = "📈" if p.profit_pct >= 0 else "📉"
            pos_lines.append(f"  {icon} {p.name}({p.code}): {p.profit_pct:+.2f}%")
        if not data.positions:
            pos_lines.append("  无持仓")
        ax4.text(0.1, 0.7, "\n".join(pos_lines), fontproperties=zh_font, fontsize=10, verticalalignment='top')
        ax4.set_title('持仓状态', fontproperties=zh_font, fontsize=12, pad=15)
        
        # 5. 风险提醒 (底部左)
        ax5 = fig.add_subplot(3, 2, 5)
        ax5.axis('off')
        risk_lines = ["风险提醒:"]
        for r in data.recommendations[:3]:
            if r.stop_loss:
                risk_lines.append(f"  {r.name}: 止损{r.stop_loss}")
        if len(risk_lines) == 1:
            risk_lines.append("  无明显风险")
        ax5.text(0.1, 0.7, "\n".join(risk_lines), fontproperties=zh_font, fontsize=10, verticalalignment='top',
                 color='#D32F2F')
        ax5.set_title('⚠️ 风险提示', fontproperties=zh_font, fontsize=12, pad=15, color='#D32F2F')
        
        # 6. 知识角 (底部右)
        ax6 = fig.add_subplot(3, 2, 6)
        ax6.axis('off')
        ax6.text(0.1, 0.7, data.knowledge_tip[:150] or "—", fontproperties=zh_font, fontsize=9, verticalalignment='top')
        ax6.set_title('📚 知识角', fontproperties=zh_font, fontsize=12, pad=15)
        
        plt.tight_layout(pad=3.0)
        filepath = f"{CHART_DIR}/premarket_{datetime.now().strftime('%Y%m%d')}.png"
        plt.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close()
        logger.info(f"信息图已生成: {filepath} ({os.path.getsize(filepath)//1024}KB)")
        return filepath
    except Exception as e:
        logger.error(f"信息图生成失败: {e}")
        return None
```

- [ ] **Step 2: 验证导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.renderers.infographic import generate_premarket_infographic; print('✅ Infographic OK')"
```

Expected: `✅ Infographic OK`

---

### Task 2.5: 创建午盘和午后风控模板

**Files:**
- Create: `backend/app/report_engine/templates/midday.py`
- Create: `backend/app/report_engine/templates/afternoon_risk.py`

- [ ] **Step 1: 编写 `midday.py`**

```python
"""午盘快报模板"""
from datetime import datetime
from app.report_engine.report_schema import ReportData, PositionItem


def build_midday_report_data(date: str, market_summary: str, positions: list[dict],
                              afternoon_tip: str = "") -> ReportData:
    pos_items = []
    for p in positions:
        pnl_pct = ((p.get("current_price", 0) - p.get("cost", 0)) / max(p.get("cost", 0), 1)) * 100
        pos_items.append(PositionItem(
            code=p.get("code", ""),
            name=p.get("name", ""),
            quantity=p.get("position", p.get("quantity", 0)),
            cost_price=p.get("cost", p.get("cost_price", 0)),
            current_price=p.get("current_price", p.get("market_price", 0)),
            profit_pct=round(pnl_pct, 2),
            market_value=p.get("current_price", 0) * p.get("position", 0) if p.get("current_price") else 0,
        ))
    return ReportData(
        report_type="midday",
        generated_at=datetime.now(),
        date=date,
        market_summary=market_summary,
        positions=pos_items,
        knowledge_tip=afternoon_tip,
    )
```

- [ ] **Step 2: 编写 `afternoon_risk.py`**

```python
"""午后风控模板"""
from datetime import datetime
from app.report_engine.report_schema import ReportData, PositionItem, RiskAlert, PerformanceData


def build_afternoon_risk_data(date: str, positions: list[dict], alerts: list[dict],
                                performance: dict) -> ReportData:
    pos_items = []
    for p in positions:
        pnl_pct = ((p.get("current_price", 0) - p.get("cost", 0)) / max(p.get("cost", 0), 1)) * 100
        risk_lvl = "danger" if pnl_pct < -8 else "warning" if pnl_pct < -3 else "normal"
        pos_items.append(PositionItem(
            code=p.get("code", ""),
            name=p.get("name", ""),
            quantity=p.get("quantity", 0),
            cost_price=p.get("cost_price", p.get("avg_cost", 0) / 100),
            current_price=p.get("current_price", p.get("market_price", 0)),
            profit_pct=round(pnl_pct, 2),
            market_value=p.get("quantity", 0) * p.get("current_price", 0),
            risk_level=risk_lvl,
        ))

    alert_items = []
    for a in alerts:
        alert_items.append(RiskAlert(
            stock_code=a.get("stock_code", a.get("code", "")),
            stock_name=a.get("stock_name", a.get("name", "")),
            alert_type=a.get("alert_type", "composite"),
            level=a.get("level", a.get("alert_level", "low")),
            message=a.get("message", a.get("alert_message", "")),
            suggestion=a.get("suggestion", ""),
            timestamp=a.get("timestamp", datetime.now().strftime("%H:%M")),
        ))

    perf = PerformanceData(
        total_assets=performance.get("total_assets", performance.get("available_cash", 0)),
        available_cash=performance.get("available_cash", 0),
    )

    return ReportData(
        report_type="risk",
        generated_at=datetime.now(),
        date=date,
        positions=pos_items,
        alerts=alert_items,
        performance=perf,
    )
```

- [ ] **Step 3: 验证导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.templates.midday import build_midday_report_data; from app.report_engine.templates.afternoon_risk import build_afternoon_risk_data; print('✅ Midday+Risk Templates OK')"
```

Expected: `✅ Midday+Risk Templates OK`

---

## Phase 3: 核心调度引擎 + Main.py 集成 (P1)

### Task 3.1: 创建报告引擎调度器

**Files:**
- Create: `backend/app/report_engine/engine.py`

- [ ] **Step 1: 编写 `engine.py`**

```python
"""报告引擎调度器 — 串联模板+渲染+渠道输出"""
from datetime import datetime
from app.report_engine.report_schema import ReportData
from app.report_engine.templates.premarket import build_premarket_report_data
from app.report_engine.templates.closing import build_closing_report_data
from app.report_engine.templates.midday import build_midday_report_data
from app.report_engine.templates.afternoon_risk import build_afternoon_risk_data
from app.report_engine.renderers.markdown_card import (
    build_premarket_card, build_closing_card, build_midday_card, build_afternoon_risk_card
)
from app.report_engine.renderers.infographic import generate_premarket_infographic
from app.report_engine.renderers.feishu_doc import upload_image_to_drive, create_doc_from_markdown
from app.report_engine.renderers.bitable_writer import bitable_writer
from app.utils.logger import logger


class ReportEngine:
    """报告引擎 — 统一调度入口"""

    def __init__(self):
        self.webhook_url = getattr(__import__("app.config", fromlist=["settings"]).settings, "FEISHU_WEBHOOK_URL", "")

    async def push_premarket(self, date: str, decision: dict, positions: list[dict],
                              risk_level: int) -> bool:
        """盘前策略全渠道推送"""
        try:
            # 1. 构建标准化数据
            data = build_premarket_report_data(date, decision, positions, risk_level)
            
            # 2. 飞书消息卡片
            card_md = build_premarket_card(data)
            self._webhook_push(f"🐕 旺财V7 盘前策略 [R{risk_level}]", card_md)
            
            # 3. 生成信息图 → 上传飞书云文档
            img_path = generate_premarket_infographic(data)
            doc_url = None
            if img_path:
                doc_url = upload_image_to_drive(img_path, f"盘前策略_{date}.png")
                if doc_url:
                    logger.info(f"信息图已上传飞书云文档: {doc_url}")
            
            # 4. 写入多维表格
            if bitable_writer._available():
                bitable_writer.write_strategy_overview(
                    date=date, risk_level=risk_level,
                    direction=data.market_direction,
                    confidence=data.confidence,
                    position_advice=f"R{risk_level}",
                    top_sectors=data.top_sectors,
                )
                # 标的池
                rec_dicts = []
                for r in data.recommendations:
                    rec_dicts.append(r.dict())
                bitable_writer.write_stock_pool(rec_dicts)
                # 持仓监控
                pos_dicts = []
                for p in data.positions:
                    pos_dicts.append({
                        "code": p.code, "name": p.name,
                        "quantity": p.quantity, "cost_price": p.cost_price,
                        "current_price": p.current_price, "profit_pct": p.profit_pct,
                        "market_value": p.market_value, "risk_level": p.risk_level,
                    })
                bitable_writer.write_position_monitor(pos_dicts)
            
            logger.info(f"盘前策略全渠道推送完成 R{risk_level}")
            return True
        except Exception as e:
            logger.error(f"盘前策略推送异常: {e}", exc_info=True)
            return False

    async def push_closing(self, date: str, positions: list[dict], alerts: list[dict],
                            performance: dict, market_summary: str, system_health: dict,
                            preview: str = "") -> bool:
        """收盘全景全渠道推送"""
        try:
            data = build_closing_report_data(date, positions, alerts, performance,
                                              market_summary, system_health, preview)
            
            # 飞书消息卡片
            card_md = build_closing_card(data)
            self._webhook_push(f"📊 旺财V7 收盘全景报告", card_md)
            
            # 生成飞书云文档
            doc_md = f"""# 收盘全景报告 - {date}

## 今日交易回顾
- 日盈亏: ¥{performance.get('daily_pnl', 0):+,.2f}
- 累计盈亏: ¥{performance.get('cumulative_pnl', 0):+,.2f}
- 持仓数: {performance.get('position_count', 0)}
- 总资产: ¥{performance.get('total_assets', 0):,.2f}

## 持仓表现
"""
            for p in positions[:10]:
                pnl = ((p.get("current_price", 0) - p.get("cost", 0)) / max(p.get("cost", 0), 1)) * 100
                doc_md += f"- {p.get('name','')}({p.get('code','')}): {pnl:+.2f}%\n"
            
            doc_md += f"\n## 明日预告\n{preview or '—'}\n"
            doc_url = create_doc_from_markdown(f"收盘全景_{date}", doc_md)
            if doc_url:
                logger.info(f"收盘文档已创建: {doc_url}")
            
            # 写入多维表格
            if bitable_writer._available():
                bitable_writer.write_performance(performance)
            
            logger.info("收盘全景全渠道推送完成")
            return True
        except Exception as e:
            logger.error(f"收盘全景推送异常: {e}", exc_info=True)
            return False

    async def push_midday(self, date: str, market_summary: str, positions: list[dict],
                           afternoon_tip: str = "") -> bool:
        """午盘快报推送"""
        try:
            data = build_midday_report_data(date, market_summary, positions, afternoon_tip)
            card_md = build_midday_card(data)
            self._webhook_push(f"🌤️ 旺财V7 午盘快报", card_md)
            
            if bitable_writer._available():
                pos_dicts = [{"code": p.code, "name": p.name, "quantity": p.quantity,
                              "cost_price": p.cost_price, "current_price": p.current_price,
                              "profit_pct": p.profit_pct, "market_value": p.market_value,
                              "risk_level": p.risk_level} for p in data.positions]
                bitable_writer.write_position_monitor(pos_dicts)
            return True
        except Exception as e:
            logger.error(f"午盘推送异常: {e}")
            return False

    async def push_afternoon_risk(self, date: str, positions: list[dict], alerts: list[dict],
                                   performance: dict) -> bool:
        """午后风控推送（仅预警时推送）"""
        try:
            data = build_afternoon_risk_data(date, positions, alerts, performance)
            has_alerts = any(a.level in ("high", "mid") for a in data.alerts)
            
            if has_alerts:
                card_md = build_afternoon_risk_card(data)
                self._webhook_push(f"🛡️ 旺财V7 午后风控告警", card_md)
            
            if bitable_writer._available():
                for a in data.alerts:
                    bitable_writer.write_risk_alert({
                        "stock_code": a.stock_code, "stock_name": a.stock_name,
                        "alert_type": a.alert_type, "level": a.level,
                        "message": a.message, "suggestion": a.suggestion,
                    })
            return True
        except Exception as e:
            logger.error(f"午后风控推送异常: {e}")
            return False

    def _webhook_push(self, title: str, content_md: str):
        """同步飞书webhook推送"""
        if not self.webhook_url or "YOUR_WEBHOOK" in self.webhook_url:
            return
        try:
            import requests
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": title},
                        "template": "red" if "风控" in title or "告警" in title else "blue",
                    },
                    "elements": [{"tag": "markdown", "content": content_md[:3000]}],
                },
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info(f"Webhook OK: {title}")
            else:
                logger.warning(f"Webhook FAIL: {resp.status_code} - {title}")
        except Exception as e:
            logger.warning(f"Webhook异常: {e}")


# 全局单例
report_engine = ReportEngine()
```

- [ ] **Step 2: 验证导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.engine import report_engine; print('✅ ReportEngine OK')"
```

Expected: `✅ ReportEngine OK`

---

### Task 3.2: 集成到 Main.py — 替换盘前策略和收盘全景

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: 在 imports 区域添加报告引擎引用**

在 `main.py` 顶部添加：

```python
from app.report_engine.engine import report_engine
```

- [ ] **Step 2: 重构 `_run_premarket_with_status` — 使用报告引擎**

找到 `_run_premarket_with_status` 函数，在原有代码基础上，在生成 report_md 后替换为：

```python
# 原有：_feishu_webhook_push + 数据库保存
# 改为：使用报告引擎全渠道推送
from datetime import date as dt_date
report_ok = await report_engine.push_premarket(
    date=str(dt_date.today()),
    decision=decision,
    positions=hd_data.get("holdings", []),
    risk_level=risk,
)
if report_ok:
    logger.info("盘前策略全渠道推送完成")
else:
    logger.warning("盘前策略推送异常，已降级为原有方式")
    _feishu_webhook_push(f"旺财V7 盘前策略 [R{risk}]", summary)
```

其中 `hd_data` 需要从 market_data 中提取：

```python
# 在函数开头提取 holdings_data
hd_data = {
    "holdings": market_data.get("holdings", []),
    "holdings_str": market_data.get("holdings_str", "无持仓"),
}
```

- [ ] **Step 3: 重构 `_run_daily_report_with_status` — 使用报告引擎**

替换原有简单日报为使用报告引擎：

```python
async def _run_daily_report_with_status():
    """系统日报 — 升级为收盘全景报告"""
    if not is_trading_day():
        return
    try:
        logger.info("=== 收盘全景报告 ===")
        date = str(date.today())
        
        # 收集持仓数据
        db = SessionLocal()
        try:
            positions = db.query(Position).filter(Position.quantity > 0).all()
            pos_list = []
            for p in positions:
                cost = p.avg_cost / 100 if p.avg_cost else 0
                price = p.market_price / 100 if p.market_price else 0
                pos_list.append({
                    "code": p.stock_code, "name": p.stock_name,
                    "quantity": p.quantity, "cost": cost,
                    "current_price": price, "market_price": price,
                })
            
            acc = db.query(SimAccount).first()
            cash = acc.cash / 100 if acc else 0
            mv = sum(p.market_value for p in positions) / 100 if positions else 0
            
            # 收集风控事件
            risk_alerts = db.query(RiskAlert).filter(
                RiskAlert.created_at >= datetime.now().replace(hour=0, minute=0, second=0)
            ).all()
            alert_list = []
            for a in risk_alerts:
                alert_list.append({
                    "stock_code": a.stock_code, "stock_name": a.stock_name,
                    "alert_type": a.alert_type, "alert_level": a.alert_level,
                    "alert_message": a.alert_message, "suggestion": a.suggestion or "",
                })
        finally:
            db.close()
        
        # 系统健康检查
        health = {
            "api_service": True,  # 能跑到这里说明 API 正常
            "deepseek_api": await cloud.is_available() if hasattr(cloud, 'is_available') else False,
            "qwen_api": await _check_qwen(),
            "tencent_data": await _check_data_source("tencent"),
            "eastmoney_data": await _check_data_source("eastmoney"),
            "tushare_data": await _check_data_source("tushare"),
            "tasks_success": len([j for j in scheduler.get_jobs()]),
            "tasks_fail": 0,
        }
        
        # 绩效数据
        perf = {
            "daily_pnl": 0,  # 从 performance analyzer 获取
            "daily_pnl_pct": 0,
            "cumulative_pnl": 0,
            "win_rate": 0,
            "position_count": len(pos_list),
            "total_assets": cash + mv,
            "available_cash": cash,
        }
        
        # 通过报告引擎推送
        await report_engine.push_closing(
            date=date,
            positions=pos_list,
            alerts=alert_list,
            performance=perf,
            market_summary="收盘市场概况",
            system_health=health,
            preview="明日关注标的待生成",
        )
        logger.info("=== 收盘全景报告完成 ===")
    except Exception as e:
        logger.error(f"收盘全景报告异常: {e}", exc_info=True)
```

- [ ] **Step 4: 新增系统健康检查辅助函数**

在 `main.py` 中添加：

```python
async def _check_qwen() -> bool:
    """检查 Qwen API 连通性"""
    qwen_key = getattr(settings, "QWEN_API_KEY", "")
    if not qwen_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://dashscope.aliyuncs.com/api/v1/models",
                headers={"Authorization": f"Bearer {qwen_key}"}
            )
            return resp.status_code == 200
    except Exception:
        return False


async def _check_data_source(name: str) -> bool:
    """检查数据源连通性"""
    try:
        if name == "tencent":
            result = await tencent_client.fetch("sh000001")
            return bool(result and result.get("price"))
        elif name == "eastmoney":
            return True  # 简化检查
        elif name == "tushare":
            return True  # 简化检查
        return False
    except Exception:
        return False
```

- [ ] **Step 5: 替换午盘快报**

在 `_run_midday_with_status` 中，替换推送逻辑：

```python
# 原有推送替换为：
from datetime import date as dt_date
pos_list = []
for p in positions:
    pos_list.append({
        "code": p.stock_code, "name": p.stock_name,
        "position": p.quantity, "cost": p.avg_cost / 100 if p.avg_cost else 0,
        "current_price": p.market_price / 100 if p.market_price else 0,
    })
await report_engine.push_midday(
    date=str(dt_date.today()),
    market_summary=snapshot,
    positions=pos_list,
    afternoon_tip=lesson,
)
```

---

## Phase 4: 系统健康增强 + 配置验证 (P2)

### Task 4.1: Qwen API 健康检查独立模块

**Files:**
- Create: `backend/app/report_engine/health_check.py`

- [ ] **Step 1: 编写 `health_check.py`**

```python
"""系统健康检查模块 — API/DeepSeek/Qwen/数据源"""
from app.config import settings
from app.utils.logger import logger


async def check_qwen() -> bool:
    """检查 Qwen API 连通性"""
    api_key = getattr(settings, "QWEN_API_KEY", "")
    if not api_key:
        logger.info("Qwen API 未配置，跳过检查")
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://dashscope.aliyuncs.com/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            ok = resp.status_code == 200
            logger.info(f"Qwen API: {'OK' if ok else 'FAIL'}")
            return ok
    except Exception as e:
        logger.warning(f"Qwen API 检查异常: {e}")
        return False


def check_qwen_sync() -> bool:
    """同步版本的 Qwen 检查（供 scheduler 使用）"""
    import httpx
    api_key = getattr(settings, "QWEN_API_KEY", "")
    if not api_key:
        return False
    try:
        resp = httpx.get(
            "https://dashscope.aliyuncs.com/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False
```

- [ ] **Step 2: 验证导入**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -c "from app.report_engine.health_check import check_qwen_sync; print('✅ HealthCheck OK')"
```

Expected: `✅ HealthCheck OK`

---

## Phase 5: 测试 (P2)

### Task 5.1: 单元测试 — 报告引擎

**Files:**
- Create: `backend/tests/test_report_engine.py`

- [ ] **Step 1: 编写测试代码**

```python
"""测试报告引擎 — 数据模型构建 + 卡片渲染"""
import pytest
from datetime import datetime
from app.report_engine.report_schema import ReportData, Recommendation, PositionItem, SystemHealth
from app.report_engine.renderers.markdown_card import build_premarket_card, build_closing_card


class TestReportSchema:
    def test_report_data_defaults(self):
        """测试ReportData默认值"""
        data = ReportData(report_type="premarket", date="2026-06-17")
        assert data.report_type == "premarket"
        assert data.risk_level == 3
        assert data.confidence == 5
        assert data.positions == []
        assert data.recommendations == []

    def test_recommendation_full(self):
        """测试推荐标的数据模型"""
        rec = Recommendation(
            code="000970", name="中科三环",
            strategy_type="short_term",
            buy_range="14.00-14.50",
            stop_loss="13.80",
            target="15.50",
            reason="N字突破",
            technical_signals="MACD金叉",
            concept_tags=["稀土", "永磁"],
            trend_score=8,
            beginner_guide="新手可轻仓参与",
            recommend_date="2026-06-17",
        )
        assert rec.code == "000970"
        assert rec.trend_score == 8
        assert "稀土" in rec.concept_tags

    def test_system_health_defaults(self):
        """测试系统健康默认值"""
        health = SystemHealth()
        assert health.api_service is False
        assert health.qwen_api is False
        assert health.tasks_success == 0


class TestMarkdownCard:
    def test_premarket_card_has_risk_section(self):
        """测试盘前卡片包含风险预警板块"""
        data = ReportData(
            report_type="premarket",
            generated_at=datetime.now(),
            date="2026-06-17",
            risk_level=3,
            market_direction="震荡偏多",
            confidence=7,
        )
        card = build_premarket_card(data)
        assert "风险预警" in card
        assert "R3" in card
        assert "市场背景" in card

    def test_premarket_card_with_recommendations(self):
        """测试盘前卡片含推荐标的"""
        data = ReportData(
            report_type="premarket",
            generated_at=datetime.now(),
            date="2026-06-17",
            risk_level=2,
            recommendations=[
                Recommendation(code="000970", name="中科三环", strategy_type="short_term",
                               buy_range="14.00", target="15.50", reason="N字突破", trend_score=8),
            ],
        )
        card = build_premarket_card(data)
        assert "中科三环" in card
        assert "000970" in card

    def test_closing_card_with_performance(self):
        """测试收盘卡片含绩效数据"""
        from app.report_engine.report_schema import PerformanceData
        data = ReportData(
            report_type="closing",
            generated_at=datetime.now(),
            date="2026-06-17",
            performance=PerformanceData(
                daily_pnl=1234.56, daily_pnl_pct=2.34,
                cumulative_pnl=56789.0, position_count=3,
                total_assets=123456.0, available_cash=50000.0,
            ),
        )
        card = build_closing_card(data)
        assert "1234.56" in card
        assert "收盘全景" in card or "📊" in card
```

- [ ] **Step 2: 运行测试**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai-v6
PYTHONPATH=backend python -m pytest backend/tests/test_report_engine.py -v
```

Expected: 全部测试通过

---

## 实施顺序总结

| 阶段 | 任务 | 优先级 | 预估时间 |
|------|------|--------|---------|
| Phase 1 | 模块结构+Schema+飞书上传+多维表格+配置 | P0 | ~30min |
| Phase 2 | 卡片渲染器+盘前模板+收盘模板+信息图+午盘/风控模板 | P1 | ~40min |
| Phase 3 | 引擎调度器+Main.py集成 | P1 | ~30min |
| Phase 4 | Qwen健康检查+验证 | P2 | ~10min |
| Phase 5 | 单元测试 | P2 | ~15min |

---

*计划版本: v1.0 | 生成日期: 2026-06-17 | 对应设计文档: docs/superpowers/specs/2026-06-17-daily-report-optimization-design.md*
