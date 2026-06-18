# 旺财V4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将「恭喜发财」迭代升级为「旺财V4」——策略生命周期闭环引擎，重建 Web 前端为 4+1 页面架构。

**Architecture:** 新建 `engine/` 包作为策略生命周期协调层，协调现有的数据源、AI 辩论引擎、交易引擎。新建 `ollama_pool.py` 管理 7 个本地模型的连接池。云端 DeepSeek 集成通过 `cloud_client.py`。前端从 6 页精简为 4+1 页（策略看板/策略工坊/持仓总览/绩效回顾/设置）。

**Tech Stack:** Python 3.14 + FastAPI + SQLAlchemy + Ollama + Vue3 + Vite + Pinia

---

## Phase 1: Foundation — 数据模型 + 连接池 + 清理 (P0)

### Task 1.1: 升级模型矩阵配置

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: 更新模型配置**

将 `config.py` 中的模型配置更新为旺财V4矩阵：

```python
# 本地模型矩阵
OLLAMA_MODELS = {
    "hunter": "qwen3.6:35b-mlx",       # 猎手 — 深度短线技术分析
    "judge": "qwen3.6:27b-mlx",        # 裁判 — 多角色辩论综合决策
    "accountant": "qwen3.5:9b",        # 账房 — 中低频估值 / 盘中快速简报
    "coder": "deepseek-r1:14b",        # 码农 — 策略代码生成 / 推理链
    "guardian": "qwen3.5:4b",          # 守夜人 — 风控审核 / 输出校验
    "gatekeeper": "qwen3.5:2b",        # 门卫 — 格式化校验 / JSON 修复
    "archivist": "bge-m3",             # 档案员 — 中文语义检索 / RAG
}

# 云端模型
CLOUD_MODELS = {
    "analyst": "deepseek-v4-pro",      # 分析师 — 研报深度解读 / 多维度估值
    "reporter": "deepseek-v4-flash",   # 记者 — 新闻情绪分析 / 题材归因
}

# 模型 keep_alive 时间 (秒)
OLLAMA_KEEP_ALIVE = 300  # 5分钟

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
```

- [ ] **Step 2: 验证配置加载**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai
PYTHONPATH=backend python -c "from app.config import OLLAMA_MODELS, CLOUD_MODELS; print(OLLAMA_MODELS); print(CLOUD_MODELS)"
```

Expected: 打印出 7 个本地模型 + 2 个云端模型的配置字典。

---

### Task 1.2: 新建 StrategyInstance 数据库模型

**Files:**
- Modify: `backend/app/models.py`

- [ ] **Step 1: 在 models.py 末尾追加新模型**

```python
class StrategyInstance(Base):
    """策略实例 — 策略生命周期核心模型"""
    __tablename__ = "strategy_instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    status = Column(String(20), default="draft")
    # draft → confirmed → planned → executing → completed → reviewed

    # 风险等级 R1-R5
    risk_level = Column(Integer, default=3)

    # 决策参数
    position_limit_pct = Column(Float, default=30.0)
    single_stock_limit_pct = Column(Float, default=15.0)
    stop_loss_pct = Column(Float, default=-5.0)
    holding_period_days = Column(Integer, default=5)

    # 标的池 JSON: [{"code":"000001","name":"平安银行","weight":0.3},...]
    stock_pool = Column(JSON)

    # 各阶段输出
    analysis_report = Column(JSON)      # ① 分析研判输出
    debate_summary = Column(JSON)       # ② 策略工坊辩论摘要
    execution_plan = Column(JSON)       # ③ 执行规划输出

    # 绩效数据
    expected_return_best = Column(Float)
    expected_return_neutral = Column(Float)
    expected_return_worst = Column(Float)
    actual_return = Column(Float, nullable=True)
    review_notes = Column(Text, nullable=True)


class ReviewLog(Base):
    """每日审查日志"""
    __tablename__ = "review_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_instance_id = Column(Integer, ForeignKey("strategy_instances.id"), nullable=True)
    review_date = Column(Date, default=date.today)
    result = Column(String(20), default="pass")  # pass / yellow / red / breaker
    violations = Column(JSON)  # [{"rule":"仓位超限","detail":"..."}]
    created_at = Column(DateTime, default=datetime.now)


class UserPreference(Base):
    """用户偏好 — 分析维度权重、默认风险等级"""
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dimension_weights = Column(JSON, default=lambda: {
        "technical": 0.30,
        "fundamental": 0.25,
        "capital_flow": 0.20,
        "sentiment": 0.15,
        "macro": 0.10,
    })
    default_risk_level = Column(Integer, default=3)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
```

- [ ] **Step 2: 更新现有模型，添加 strategy_instance_id 外键**

在 `TradeLog` 类中新增字段：
```python
strategy_instance_id = Column(Integer, ForeignKey("strategy_instances.id"), nullable=True)
```

在 `TradingSignal` 类中新增字段：
```python
strategy_instance_id = Column(Integer, ForeignKey("strategy_instances.id"), nullable=True)
```

在 `AIStrategy` 类中新增字段：
```python
strategy_instance_id = Column(Integer, ForeignKey("strategy_instances.id"), nullable=True)
```

在 `RiskAlert` 类中新增字段：
```python
review_type = Column(String(20), default="risk")  # risk / daily_review
```

- [ ] **Step 3: 初始化新表**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai
PYTHONPATH=backend python -c "
from app.database import engine
from app.models import Base
Base.metadata.create_all(bind=engine)
print('Tables created successfully')
"
```

Expected: 无报错，新表创建成功。

- [ ] **Step 4: 插入默认用户偏好**

```bash
PYTHONPATH=backend python -c "
from app.database import SessionLocal
from app.models import UserPreference
db = SessionLocal()
if db.query(UserPreference).count() == 0:
    db.add(UserPreference(id=1))
    db.commit()
    print('Default preferences created')
else:
    print('Preferences already exist')
db.close()
"
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/config.py
git commit -m "feat: add StrategyInstance/ReviewLog/UserPreference models and model matrix config"
```

---

### Task 1.3: 新建 ollama_pool.py 模型连接池

**Files:**
- Create: `backend/app/ai/ollama_pool.py`

- [ ] **Step 1: 编写连接池**

```python
"""Ollama 模型连接池 — 管理 7 个本地模型的排队、keep_alive、切换"""
import asyncio
import time
import httpx
from app.config import OLLAMA_MODELS, OLLAMA_KEEP_ALIVE

OLLAMA_BASE = "http://localhost:11434"


class OllamaPool:
    """管理多个 Ollama 模型的并发请求"""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        self._last_used = {}        # model_name → timestamp
        self._active_model = None   # 当前加载的大模型名称
        self._lock = asyncio.Lock()

    async def generate(self, model_key: str, prompt: str, **kwargs) -> dict:
        """调用指定角色模型生成文本。

        Args:
            model_key: OLLAMA_MODELS 中的 key (hunter/judge/accountant/...)
            prompt: 提示词
            **kwargs: 透传给 Ollama API (temperature, num_predict 等)
        """
        model_name = OLLAMA_MODELS.get(model_key)
        if not model_name:
            raise ValueError(f"Unknown model key: {model_key}")

        async with self._lock:
            self._last_used[model_key] = time.time()

        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "keep_alive": f"{OLLAMA_KEEP_ALIVE}s",
            **kwargs,
        }

        resp = await self._client.post(
            f"{OLLAMA_BASE}/api/generate", json=payload
        )
        resp.raise_for_status()
        return resp.json()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """使用 bge-m3 生成文本嵌入向量"""
        model_name = OLLAMA_MODELS["archivist"]
        embeddings = []
        for text in texts:
            resp = await self._client.post(
                f"{OLLAMA_BASE}/api/embeddings",
                json={"model": model_name, "prompt": text},
            )
            resp.raise_for_status()
            embeddings.append(resp.json()["embedding"])
        return embeddings

    async def unload_model(self, model_key: str):
        """手动卸载指定模型"""
        model_name = OLLAMA_MODELS.get(model_key)
        if model_name:
            await self._client.post(
                f"{OLLAMA_BASE}/api/generate",
                json={"model": model_name, "keep_alive": 0},
            )

    async def close(self):
        await self._client.aclose()


# 全局单例
pool = OllamaPool()
```

- [ ] **Step 2: 验证连接池可用**

```bash
ollama serve &
sleep 2
PYTHONPATH=backend python -c "
import asyncio
from app.ai.ollama_pool import pool

async def test():
    result = await pool.generate('gatekeeper', 'Say hello in Chinese', options={'num_predict': 20})
    print('Response:', result.get('response', '')[:100])
    await pool.close()

asyncio.run(test())
"
```

Expected: 打印中文问候语。

- [ ] **Step 3: Commit**

```bash
git add backend/app/ai/ollama_pool.py
git commit -m "feat: add OllamaPool for 7-model connection management"
```

---

### Task 1.4: 拉取 bge-m3 并清理废弃文件

**Files:**
- 拉取: `bge-m3` (Ollama model)
- Delete: `backend/app/ai/prompts.py`
- Delete: `backend/app/data_sources/sina_client.py`
- Delete: `backend/app/data_sources/proxy_bypass.py`
- Delete: `backend/app/services/sync.py`
- Delete: `backend/app/trading_engine/scheduler.py`
- Delete: `backend/app/trading_engine/trend_tracker.py`
- Delete: `frontend/src/store/holdings.js`

- [ ] **Step 1: 拉取 bge-m3 模型**

```bash
ollama pull bge-m3
```

Expected: 下载完成，`ollama list` 中出现 `bge-m3:latest`。

- [ ] **Step 2: 删除废弃文件**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai
rm backend/app/ai/prompts.py
rm backend/app/data_sources/sina_client.py
rm backend/app/data_sources/proxy_bypass.py
rm backend/app/services/sync.py
rm backend/app/trading_engine/scheduler.py
rm backend/app/trading_engine/trend_tracker.py
rm frontend/src/store/holdings.js
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove deprecated files, add bge-m3 model"
```

---

### Task 1.5: Phase 1 集成验证

- [ ] **Step 1: 验证后端可启动**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai
source backend/.venv/bin/activate
PYTHONPATH=backend python -m app.main &
sleep 3
curl http://localhost:8000/api/health
kill %1
```

Expected: `{"status":"ok"}`

- [ ] **Step 2: Commit**

```bash
git commit --allow-empty -m "chore: Phase 1 foundation complete"
```

---

## Phase 2: Core Engine — 分析研判 + 策略工坊 (P1)

### Task 2.1: 新建 lifecycle.py 策略生命周期协调器

**Files:**
- Create: `backend/app/engine/__init__.py`
- Create: `backend/app/engine/lifecycle.py`

- [ ] **Step 1: 创建 engine 包**

```bash
mkdir -p backend/app/engine
```

- [ ] **Step 2: 写入 __init__.py**

```python
"""旺财V4 策略生命周期引擎"""
```

- [ ] **Step 3: 写入 lifecycle.py 协调器**

```python
"""策略生命周期协调器 — 串联 6 个阶段"""
from datetime import datetime
from app.database import SessionLocal
from app.models import StrategyInstance
from app.ai.ollama_pool import pool


class StrategyLifecycle:
    """管理策略从创建到回顾的完整生命周期"""

    def __init__(self):
        self.db = SessionLocal()

    def create_instance(self) -> StrategyInstance:
        """创建新的策略实例"""
        instance = StrategyInstance(
            created_at=datetime.now(),
            status="draft",
            risk_level=3,
            position_limit_pct=30.0,
            single_stock_limit_pct=15.0,
            stop_loss_pct=-5.0,
            holding_period_days=5,
        )
        self.db.add(instance)
        self.db.commit()
        self.db.refresh(instance)
        return instance

    def get_active_instance(self) -> StrategyInstance | None:
        """获取当前活跃的策略实例"""
        return (
            self.db.query(StrategyInstance)
            .filter(StrategyInstance.status.in_([
                "draft", "confirmed", "planned", "executing"
            ]))
            .order_by(StrategyInstance.created_at.desc())
            .first()
        )

    def update_status(self, instance: StrategyInstance, status: str):
        """更新策略实例状态"""
        instance.status = status
        instance.updated_at = datetime.now()
        self.db.commit()

    def close(self):
        self.db.close()
        pool.close()
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/engine/
git commit -m "feat: add StrategyLifecycle coordinator skeleton"
```

---

### Task 2.2: 新建 analysis.py 分析研判引擎

**Files:**
- Create: `backend/app/engine/analysis.py`

- [ ] **Step 1: 编写分析研判引擎**

```python
"""① 分析研判引擎 — 多维度并行分析，产出投资倾向性报告"""
import asyncio
import json
from app.ai.ollama_pool import pool


async def run_analysis(market_data: dict) -> dict:
    """并行执行四维度分析，汇总为投资倾向性报告。

    Args:
        market_data: 包含 indices/sectors/holdings/kline/news 的字典

    Returns:
        结构化分析报告 dict
    """
    # 并行启动 4 个维度分析（本地+云端混跑，互不阻塞）
    technical_task = _analyze_technical(market_data)
    fundamental_task = _analyze_fundamental(market_data)
    capital_task = _analyze_capital_flow(market_data)
    sentiment_task = _analyze_sentiment(market_data)

    technical, fundamental, capital, sentiment = await asyncio.gather(
        technical_task, fundamental_task, capital_task, sentiment_task
    )

    # 综合研判在所有维度完成后执行
    synthesis = await _synthesize(technical, fundamental, capital, sentiment)

    return {
        "technical_score": technical.get("score", 50),
        "fundamental_score": fundamental.get("score", 50),
        "capital_score": capital.get("score", 50),
        "sentiment_score": sentiment.get("score", 50),
        "overall_bias": synthesis.get("overall_bias", "neutral"),
        "plans": synthesis.get("plans", []),  # 保守/中性/激进 3套方案
        "data_sources": _collect_sources(technical, fundamental, capital, sentiment),
        "generated_at": str(__import__("datetime").datetime.now()),
    }


async def _analyze_technical(data: dict) -> dict:
    """技术面分析 → qwen3.6:35b-mlx (本地)"""
    prompt = _build_technical_prompt(data)
    result = await pool.generate("hunter", prompt)
    return _parse_json_response(result)


async def _analyze_fundamental(data: dict) -> dict:
    """基本面/估值分析 → DeepSeek-v4-pro (云端)"""
    # Phase 4 实现云端调用，Phase 2 使用本地 fallback
    prompt = _build_fundamental_prompt(data)
    result = await pool.generate("accountant", prompt)
    return _parse_json_response(result)


async def _analyze_capital_flow(data: dict) -> dict:
    """资金面分析 → qwen3.5:9b (本地)"""
    prompt = _build_capital_prompt(data)
    result = await pool.generate("accountant", prompt)
    return _parse_json_response(result)


async def _analyze_sentiment(data: dict) -> dict:
    """情绪面/题材分析 → DeepSeek-v4-flash (云端)"""
    # Phase 4 实现云端调用，Phase 2 使用本地 fallback
    prompt = _build_sentiment_prompt(data)
    result = await pool.generate("gatekeeper", prompt)
    return _parse_json_response(result)


async def _synthesize(technical, fundamental, capital, sentiment) -> dict:
    """综合研判 → qwen3.6:27b-mlx (本地)"""
    prompt = f"""你是一位资深投资顾问。请综合以下四个维度的分析结果，生成投资倾向性报告。

技术面分析: {json.dumps(technical, ensure_ascii=False)}
基本面分析: {json.dumps(fundamental, ensure_ascii=False)}
资金面分析: {json.dumps(capital, ensure_ascii=False)}
情绪面分析: {json.dumps(sentiment, ensure_ascii=False)}

请输出 JSON 格式:
{{
    "overall_bias": "bullish|neutral|bearish",
    "confidence": 0-100,
    "plans": [
        {{
            "type": "conservative|neutral|aggressive",
            "label": "方案名称",
            "description": "方案描述",
            "risk_level": 1-5,
            "stock_pool": [{{"code":"000001","name":"股票名","weight":0.3,"reason":"理由"}}],
            "expected_return": {{"best": "+X%","neutral":"+Y%","worst":"-Z%"}},
            "holding_period": "X-Y天"
        }}
    ],
    "key_risks": ["风险1", "风险2"],
    "market_context": "一句话市场概况"
}}

只输出 JSON，不要其他内容。"""
    result = await pool.generate("judge", prompt)
    return _parse_json_response(result)


def _parse_json_response(result: dict) -> dict:
    """从 Ollama 响应中提取 JSON"""
    text = result.get("response", "{}")
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "error": "JSON parse failed"}


def _collect_sources(*analyses) -> list:
    """收集数据来源标注"""
    return [
        "腾讯行情 (K线/实时报价)",
        "东方财富 (板块/资金流向)",
        "财联社 (新闻/题材)",
        "Ollama AI (qwen3.6:35b-mlx + qwen3.6:27b-mlx + qwen3.5:9b)",
    ]


def _build_technical_prompt(data: dict) -> str:
    holdings = json.dumps(data.get("holdings", []), ensure_ascii=False)
    indices = json.dumps(data.get("indices", {}), ensure_ascii=False)
    return f"""你是一位技术分析师。请基于以下数据做短线(1-5天)技术面分析。

大盘指数: {indices}
持仓数据: {holdings}

请输出 JSON:
{{
    "score": 0-100,
    "trend": "up|down|sideways",
    "key_levels": {{"support": ["位1"], "resistance": ["位1"]}},
    "signals": [{{"stock_code":"000001","stock_name":"股票","signal":"buy|sell|hold","reason":"理由","confidence":0-100}}],
    "sector_rotation": "板块轮动方向判断",
    "summary": "一句话技术面总结"
}}

只输出 JSON。"""


def _build_fundamental_prompt(data: dict) -> str:
    holdings = json.dumps(data.get("holdings", []), ensure_ascii=False)
    return f"""你是一位基本面分析师。请基于以下数据做中低频(1-4周)基本面分析。

持仓数据: {holdings}

请输出 JSON:
{{
    "score": 0-100,
    "valuation": "undervalued|fair|overvalued",
    "signals": [{{"stock_code":"000001","stock_name":"股票","signal":"buy|sell|hold","reason":"理由","confidence":0-100}}],
    "macro_outlook": "宏观环境一句话判断",
    "summary": "一句话基本面总结"
}}

只输出 JSON。"""


def _build_capital_prompt(data: dict) -> str:
    sectors = json.dumps(data.get("sectors", []), ensure_ascii=False)
    return f"""你是一位资金面分析师。请基于以下数据分析资金流向。

板块资金: {sectors}

请输出 JSON:
{{
    "score": 0-100,
    "main_force_direction": "inflow|outflow|balanced",
    "hot_sectors": ["板块1", "板块2"],
    "signals": [{{"sector":"板块名","signal":"inflow|outflow","intensity":"high|medium|low"}}],
    "summary": "一句话资金面总结"
}}

只输出 JSON。"""


def _build_sentiment_prompt(data: dict) -> str:
    news = json.dumps(data.get("news", []), ensure_ascii=False)
    return f"""你是一位市场情绪分析师。请基于以下新闻数据分析市场情绪。

近期新闻: {news}

请输出 JSON:
{{
    "score": 0-100,
    "sentiment": "positive|neutral|negative",
    "hot_topics": ["题材1", "题材2"],
    "signals": [{{"topic":"题材名","sentiment":"positive|negative","impact":"high|medium|low"}}],
    "summary": "一句话情绪面总结"
}}

只输出 JSON。"""
```

- [ ] **Step 2: 验证分析引擎可独立运行**

```bash
PYTHONPATH=backend python -c "
import asyncio
from app.engine.analysis import run_analysis

async def test():
    mock_data = {
        'indices': {'shanghai': 3350.0, 'shenzhen': 10800.0},
        'sectors': [],
        'holdings': [],
        'news': [],
    }
    report = await run_analysis(mock_data)
    print('Scores:', report.get('technical_score'), report.get('fundamental_score'))
    print('Plans:', len(report.get('plans', [])))

asyncio.run(test())
"
```

Expected: 打印各维度评分和 3 套方案（耗时约 2-3 分钟）。

- [ ] **Step 3: Commit**

```bash
git add backend/app/engine/analysis.py
git commit -m "feat: add analysis engine with 4-dimension parallel analysis"
```

---

### Task 2.3: 新建 workshop.py 策略工坊引擎

**Files:**
- Create: `backend/app/engine/workshop.py`

- [ ] **Step 1: 编写策略工坊引擎**

```python
"""② 策略工坊引擎 — AI 辩论 + 风险定级 + 策略决策卡"""
import json
from app.ai.ollama_pool import pool

# 风险等级定义
RISK_LEVELS = {
    1: {"label": "R1 保守", "position_limit": 10, "stop_loss": -2, "stock_types": "ETF/债基"},
    2: {"label": "R2 稳健", "position_limit": 20, "stop_loss": -3, "stock_types": "蓝筹低波动"},
    3: {"label": "R3 适中", "position_limit": 30, "stop_loss": -5, "stock_types": "加入成长股"},
    4: {"label": "R4 积极", "position_limit": 50, "stop_loss": -8, "stock_types": "允许小盘"},
    5: {"label": "R5 激进", "position_limit": 70, "stop_loss": -12, "stock_types": "允许题材博弈"},
}


async def run_debate(analysis_report: dict) -> dict:
    """执行 4 角色辩论，产出策略决策卡。

    Args:
        analysis_report: 阶段①的分析研判报告

    Returns:
        辩论摘要 + 决策卡参数
    """
    report_json = json.dumps(analysis_report, ensure_ascii=False)

    # 顺序调用 4 个角色
    hunter_view = await _hunter_debate(report_json)
    accountant_view = await _accountant_debate(report_json)
    guardian_view = await _guardian_debate(report_json)
    judge_decision = await _judge_synthesize(
        report_json, hunter_view, accountant_view, guardian_view
    )

    return {
        "roles": {
            "hunter": hunter_view,
            "accountant": accountant_view,
            "guardian": guardian_view,
        },
        "decision": judge_decision,
        "recommended_risk_level": judge_decision.get("recommended_risk_level", 3),
        "debate_timestamp": str(__import__("datetime").datetime.now()),
    }


async def _hunter_debate(report_json: str) -> dict:
    """猎手 — 进攻观点 → qwen3.6:35b-mlx"""
    prompt = f"""你是「猎手」——专注于短线技术分析的角色。你的风格偏向进攻，关注技术信号和短期机会。

分析报告: {report_json}

请从进攻角度出发，输出你的观点。JSON 格式:
{{
    "view": "bullish|cautiously_bullish|neutral|bearish",
    "recommended_stocks": [{{"code":"000001","name":"股票","reason":"理由","entry_zone":"价格区间","stop_loss":"止损价","target":"目标价"}}],
    "key_arguments": ["论点1", "论点2", "论点3"],
    "risk_awareness": "你也需要注意的风险",
    "reasoning_chain": "你的完整推理链"
}}

只输出 JSON。"""
    result = await pool.generate("hunter", prompt)
    return _parse_json(result)


async def _accountant_debate(report_json: str) -> dict:
    """账房 — 稳健观点 → qwen3.5:9b"""
    prompt = f"""你是「账房」——专注于估值和趋势分析的角色。你的风格偏向稳健，关注基本面和中期趋势。

分析报告: {report_json}

请从稳健角度出发，输出你的观点。JSON 格式:
{{
    "view": "bullish|cautiously_bullish|neutral|bearish",
    "recommended_stocks": [{{"code":"000001","name":"股票","reason":"理由","entry_zone":"价格区间","stop_loss":"止损价","target":"目标价"}}],
    "key_arguments": ["论点1", "论点2", "论点3"],
    "risk_awareness": "你也需要注意的风险",
    "reasoning_chain": "你的完整推理链"
}}

只输出 JSON。"""
    result = await pool.generate("accountant", prompt)
    return _parse_json(result)


async def _guardian_debate(report_json: str) -> dict:
    """守夜人 — 保守观点 → qwen3.5:4b"""
    prompt = f"""你是「守夜人」——专注于风险控制的角色。你的风格极度保守，关注一切可能的风险因素。

分析报告: {report_json}

请从保守角度出发，输出你的观点。JSON 格式:
{{
    "view": "cautious|defensive|bearish",
    "risk_factors": ["风险1", "风险2", "风险3"],
    "veto_stocks": [{{"code":"000001","name":"股票","reason":"一票否决理由"}}],
    "safety_first_suggestions": ["建议1", "建议2"],
    "reasoning_chain": "你的完整推理链"
}}

只输出 JSON。"""
    result = await pool.generate("guardian", prompt)
    return _parse_json(result)


async def _judge_synthesize(
    report_json: str, hunter: dict, accountant: dict, guardian: dict
) -> dict:
    """裁判 — 综合决策 + 风险等级推荐 → qwen3.6:27b-mlx"""
    prompt = f"""你是「裁判」——负责综合三方观点，做出最终决策。你需要:

1. 综合三方观点，给出最终判断
2. 推荐风险等级 (R1-R5)
3. 生成策略决策卡参数

分析报告: {report_json}

猎手(进攻): {json.dumps(hunter, ensure_ascii=False)}
账房(稳健): {json.dumps(accountant, ensure_ascii=False)}
守夜人(保守): {json.dumps(guardian, ensure_ascii=False)}

风险等级参考:
R1(保守): 仓位≤10%, 止损-2%, ETF/债基
R2(稳健): 仓位≤20%, 止损-3%, 蓝筹低波动
R3(适中): 仓位≤30%, 止损-5%, 加入成长股
R4(积极): 仓位≤50%, 止损-8%, 允许小盘
R5(激进): 仓位≤70%, 止损-12%, 允许题材

请输出 JSON:
{{
    "recommended_risk_level": 1-5,
    "risk_level_reason": "推荐该等级的理由",
    "position_limit_pct": 数字,
    "single_stock_limit_pct": 数字,
    "stop_loss_pct": 负数,
    "holding_period_days": 数字,
    "stock_pool": [{{"code":"000001","name":"股票","weight":0.3,"reason":"理由"}}],
    "final_view": "bullish|cautiously_bullish|neutral|cautious|bearish",
    "debate_summary": "三方观点的一句话总结",
    "key_consensus": "三方达成共识的点",
    "key_divergence": "三方存在分歧的点",
    "reasoning_chain": "你的完整决策推理链"
}}

只输出 JSON。"""
    result = await pool.generate("judge", prompt)
    return _parse_json(result)


async def ask_role(role: str, question: str, context: str) -> dict:
    """追问特定角色 — 用户点击角色卡片后的深聊"""
    role_personas = {
        "hunter": ("猎手", "短线技术分析师，风格偏向进攻"),
        "accountant": ("账房", "估值和趋势分析师，风格稳健"),
        "guardian": ("守夜人", "风险控制专家，风格保守"),
        "judge": ("裁判", "综合决策者，负责最终判断"),
    }
    name, persona = role_personas.get(role, (role, "AI 助手"))

    prompt = f"""你是「{name}」——{persona}。

上下文: {context}

用户追问: {question}

请直接回答用户的问题，给出具体、有依据的回复。可以引用之前分析中的具体数据和逻辑。
不要输出 JSON——直接输出自然语言回答。"""

    result = await pool.generate(role, prompt)
    return {"role": name, "question": question, "answer": result.get("response", "")}


def _parse_json(result: dict) -> dict:
    text = result.get("response", "{}").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "error": "JSON parse failed"}
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/engine/workshop.py
git commit -m "feat: add workshop engine with 4-role debate and risk level system"
```

---

### Task 2.4: 添加策略生命周期 API 端点

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: 在 main.py 中添加新端点**

```python
from app.engine.lifecycle import StrategyLifecycle
from app.engine.analysis import run_analysis
from app.engine.workshop import run_debate, ask_role, RISK_LEVELS

# === 策略生命周期 API ===

@app.get("/api/strategy/active")
async def get_active_strategy():
    """获取当前活跃的策略实例"""
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.get_active_instance()
        if instance:
            return {
                "id": instance.id,
                "status": instance.status,
                "risk_level": instance.risk_level,
                "position_limit_pct": instance.position_limit_pct,
                "stop_loss_pct": instance.stop_loss_pct,
                "holding_period_days": instance.holding_period_days,
                "stock_pool": instance.stock_pool,
                "created_at": str(instance.created_at),
            }
        return {"status": "no_active_strategy"}
    finally:
        lifecycle.close()


@app.post("/api/strategy/analysis")
async def trigger_analysis():
    """触发①分析研判"""
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.create_instance()
        # 采集市场数据
        market_data = await _gather_market_context(lifecycle.db)
        # 执行分析
        report = await run_analysis(market_data)
        # 保存
        instance.analysis_report = report
        lifecycle.db.commit()
        return {"strategy_id": instance.id, "report": report}
    finally:
        lifecycle.close()


@app.post("/api/strategy/{strategy_id}/debate")
async def trigger_debate(strategy_id: int):
    """触发②策略工坊辩论"""
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.db.query(StrategyInstance).get(strategy_id)
        if not instance or not instance.analysis_report:
            raise HTTPException(404, "策略实例不存在或无分析报告")
        debate_result = await run_debate(instance.analysis_report)
        instance.debate_summary = debate_result
        lifecycle.db.commit()
        return {"strategy_id": strategy_id, "debate": debate_result}
    finally:
        lifecycle.close()


@app.post("/api/strategy/{strategy_id}/confirm")
async def confirm_strategy(strategy_id: int, decision: dict):
    """确认策略决策卡"""
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.db.query(StrategyInstance).get(strategy_id)
        if not instance:
            raise HTTPException(404, "策略实例不存在")
        instance.risk_level = decision.get("risk_level", instance.risk_level)
        instance.position_limit_pct = decision.get("position_limit_pct", instance.position_limit_pct)
        instance.stop_loss_pct = decision.get("stop_loss_pct", instance.stop_loss_pct)
        instance.holding_period_days = decision.get("holding_period_days", instance.holding_period_days)
        instance.stock_pool = decision.get("stock_pool", instance.stock_pool)
        instance.status = "confirmed"
        lifecycle.db.commit()
        return {"strategy_id": strategy_id, "status": "confirmed"}
    finally:
        lifecycle.close()


@app.post("/api/strategy/debate/ask")
async def ask_role_question(request: dict):
    """追问特定角色"""
    result = await ask_role(
        role=request["role"],
        question=request["question"],
        context=request.get("context", ""),
    )
    return result


@app.get("/api/strategy/risk-levels")
async def get_risk_levels():
    """获取风险等级定义"""
    return RISK_LEVELS
```

- [ ] **Step 2: 清理旧定时任务，替换为新任务**

在 `main.py` 中删除旧的 9 个定时任务注册代码，替换为：

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

# 仅交易日执行
@scheduler.scheduled_job("cron", day_of_week="mon-fri", hour=9, minute=0)
async def premarket_job():
    """盘前启动: ①分析研判 → ②策略工坊"""
    # ... 由 lifecycle engine 驱动


@scheduler.scheduled_job("cron", day_of_week="mon-fri", hour=11, minute=30)
async def midday_job():
    """午间简报: 盘中快照 + 绩效概览"""
    # Phase 3 实现


@scheduler.scheduled_job("cron", day_of_week="mon-fri", hour=14, minute=0)
async def afternoon_job():
    """下午简报: 盘中快照 + 执行规划更新"""
    # Phase 3 实现


@scheduler.scheduled_job("cron", day_of_week="mon-fri", hour=15, minute=0)
async def closing_job():
    """收盘处理: ⑤绩效回顾(完整) + ⑥每日审查"""
    # Phase 3 实现
```

- [ ] **Step 3: 验证 API 端点**

```bash
cd /Users/zhuchenyuan/工作流/cong-xi-fa-cai
source backend/.venv/bin/activate
PYTHONPATH=backend python -m app.main &
sleep 3

# 测试活跃策略查询
curl -s http://localhost:8000/api/strategy/active | python -m json.tool

# 测试风险等级查询
curl -s http://localhost:8000/api/strategy/risk-levels | python -m json.tool

kill %1
```

Expected: 两个端点正常返回 JSON。

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: add strategy lifecycle API endpoints, simplify scheduler"
```

---

## Phase 3: Execution & Review + 前端重设计 (P2)

### Task 3.1: 新建 planning.py 执行规划引擎

**Files:**
- Create: `backend/app/engine/planning.py`

- [ ] **Step 1: 编写执行规划引擎**

```python
"""③ 执行规划引擎 — 仓位扫描 + 资金分配 + 操作指令生成"""
from app.database import SessionLocal
from app.models import TradeLog


def generate_execution_plan(strategy_instance, holdings: list, available_cash: float) -> dict:
    """根据策略决策卡和实际仓位，生成操作计划书。

    Args:
        strategy_instance: StrategyInstance (已确认)
        holdings: [{"code":"000001","name":"平安银行","position":1000,"cost":12.5},...]
        available_cash: 可用资金

    Returns:
        操作计划书 dict
    """
    stock_pool = strategy_instance.stock_pool or []
    position_limit = strategy_instance.position_limit_pct / 100
    single_limit = strategy_instance.single_stock_limit_pct / 100
    total_assets = available_cash + sum(
        h.get("position", 0) * h.get("current_price", h.get("cost", 0))
        for h in holdings
    )

    buy_list = []
    sell_list = []
    hold_list = []

    # 当前持仓的代码集合
    holding_codes = {h["code"] for h in holdings}

    # 标的池中未持有的 → 买入
    for stock in stock_pool:
        code = stock["code"]
        if code not in holding_codes:
            weight = stock.get("weight", 1.0 / len(stock_pool))
            allocated = available_cash * weight
            buy_list.append({
                "code": code,
                "name": stock["name"],
                "allocated_amount": round(allocated, 2),
                "reason": stock.get("reason", ""),
            })

    # 已持有但不在标的池 → 卖出
    for holding in holdings:
        code = holding["code"]
        if code not in {s["code"] for s in stock_pool}:
            sell_list.append({
                "code": code,
                "name": holding["name"],
                "position": holding["position"],
                "reason": "不在当前策略标的池",
            })
        else:
            hold_list.append(holding)

    # 风控硬约束检查
    total_allocated = sum(b["allocated_amount"] for b in buy_list)
    checks = {
        "total_position_ok": (total_allocated / total_assets) <= position_limit,
        "single_position_ok": all(
            b["allocated_amount"] / total_assets <= single_limit
            for b in buy_list
        ),
    }

    return {
        "buy_list": buy_list,
        "sell_list": sell_list,
        "hold_list": hold_list,
        "total_assets": round(total_assets, 2),
        "available_cash": round(available_cash, 2),
        "total_allocated": round(total_allocated, 2),
        "expected_return_best": _estimate_return(strategy_instance, "best"),
        "expected_return_neutral": _estimate_return(strategy_instance, "neutral"),
        "expected_return_worst": _estimate_return(strategy_instance, "worst"),
        "risk_checks": checks,
    }


def _estimate_return(strategy_instance, scenario: str) -> str:
    """估算收益区间（简化版，Phase 3 使用历史波动率改进）"""
    base = {
        "best": "+12%",
        "neutral": "+5%",
        "worst": "-3%",
    }
    return base.get(scenario, "N/A")
```

- [ ] **Step 2: 添加执行规划 API**

在 `main.py` 中添加：
```python
@app.post("/api/strategy/{strategy_id}/plan")
async def generate_plan(strategy_id: int):
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.db.query(StrategyInstance).get(strategy_id)
        if not instance or instance.status != "confirmed":
            raise HTTPException(400, "策略未确认")
        holdings = _get_holdings_from_logs(lifecycle.db)
        available_cash = 100000  # TODO: 从账户模块获取
        plan = generate_execution_plan(instance, holdings, available_cash)
        instance.execution_plan = plan
        instance.status = "planned"
        lifecycle.db.commit()
        return {"strategy_id": strategy_id, "plan": plan}
    finally:
        lifecycle.close()
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/engine/planning.py backend/app/main.py
git commit -m "feat: add execution planning engine with position scanning"
```

---

### Task 3.2: 新建 review.py 每日审查引擎

**Files:**
- Create: `backend/app/engine/review.py`

- [ ] **Step 1: 编写每日审查引擎**

```python
"""⑥ 每日审查引擎 — 纯规则检查，预警和修正"""
from datetime import date, datetime
from app.database import SessionLocal
from app.models import StrategyInstance, ReviewLog, TradeLog
from sqlalchemy import func


def run_daily_review(strategy_instance_id: int = None) -> dict:
    """执行每日审查，检查操作是否违反策略约束。

    Returns:
        {"result": "pass|yellow|red|breaker", "violations": [...], "review_log_id": int}
    """
    db = SessionLocal()
    try:
        # 获取当前策略
        if strategy_instance_id:
            strategy = db.query(StrategyInstance).get(strategy_instance_id)
        else:
            strategy = (
                db.query(StrategyInstance)
                .filter(StrategyInstance.status.in_(["confirmed", "planned", "executing"]))
                .order_by(StrategyInstance.created_at.desc())
                .first()
            )

        if not strategy:
            return {"result": "pass", "violations": [], "message": "无活跃策略"}

        violations = []

        # 检查 1: 仓位是否超限
        today_trades = (
            db.query(TradeLog)
            .filter(
                TradeLog.strategy_instance_id == strategy.id,
                func.date(TradeLog.created_at) == date.today(),
            )
            .all()
        )
        if today_trades:
            # 简化: 检查交易频率
            trade_count = len(today_trades)
            if trade_count > 10:
                violations.append({
                    "rule": "操作频率过高",
                    "detail": f"今日已执行 {trade_count} 笔操作，超过阈值 10 笔",
                    "severity": "yellow",
                })
            elif trade_count > 20:
                violations.append({
                    "rule": "操作频率严重过高",
                    "detail": f"今日已执行 {trade_count} 笔操作，可能情绪化交易",
                    "severity": "red",
                })

        # 检查 2: 是否有止损违规
        # Phase 3 简化版，Phase 4 完善

        # 检查 3: 是否在标的池外交易
        pool_codes = {s["code"] for s in (strategy.stock_pool or [])}
        for trade in today_trades:
            if trade.stock_code not in pool_codes:
                violations.append({
                    "rule": "标的池外交易",
                    "detail": f"交易 {trade.stock_code} {trade.stock_name} 不在当前策略标的池",
                    "severity": "yellow",
                })

        # 判定结果
        has_red = any(v["severity"] == "red" for v in violations)
        has_yellow = any(v["severity"] == "yellow" for v in violations)

        if has_red:
            result = "red"
        elif has_yellow:
            result = "yellow"
        else:
            result = "pass"

        # 写入审查日志
        review_log = ReviewLog(
            strategy_instance_id=strategy.id,
            review_date=date.today(),
            result=result,
            violations=violations,
        )
        db.add(review_log)
        db.commit()
        db.refresh(review_log)

        return {
            "result": result,
            "violations": violations,
            "review_log_id": review_log.id,
        }
    finally:
        db.close()
```

- [ ] **Step 2: 添加每日审查 API**

在 `main.py` 中添加：
```python
@app.post("/api/strategy/review")
async def trigger_daily_review():
    """触发⑥每日审查"""
    result = run_daily_review()
    return result


@app.get("/api/strategy/reviews")
async def get_review_logs(days: int = 7):
    """获取最近 N 天的审查日志"""
    db = SessionLocal()
    try:
        logs = (
            db.query(ReviewLog)
            .filter(ReviewLog.review_date >= date.today() - __import__("datetime").timedelta(days=days))
            .order_by(ReviewLog.created_at.desc())
            .limit(30)
            .all()
        )
        return [
            {
                "id": log.id,
                "date": str(log.review_date),
                "result": log.result,
                "violations": log.violations,
            }
            for log in logs
        ]
    finally:
        db.close()
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/engine/review.py backend/app/main.py
git commit -m "feat: add daily review engine with rule-based violation checking"
```

---

### Task 3.3: 前端页面重设计 — 全局样式 + 导航

**Files:**
- Modify: `frontend/src/styles/index.css`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: 重写全局样式**

更新 `index.css` 的 CSS 变量：

```css
:root {
  --bg-primary: #0f1117;
  --bg-card: #1a1d2e;
  --bg-card-hover: #222640;
  --text-primary: #e4e6ed;
  --text-secondary: #8b8fa8;
  --text-muted: #5a5e76;
  --accent: #f0a050;
  --accent-dim: rgba(240, 160, 80, 0.15);
  --accent-secondary: #6366f1;
  --up-color: #ef4444;
  --down-color: #22c55e;
  --warning: #eab308;
  --danger: #ef4444;
  --success: #22c55e;
  --radius: 8px;
  --shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
  --font-mono: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.6;
}

.card {
  background: var(--bg-card);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 20px;
}

.card-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.card-title { font-size: 16px; font-weight: 600; color: var(--text-primary); }

.data-value { font-family: var(--font-mono); font-size: 18px; font-weight: 600; }
.data-label { font-size: 12px; color: var(--text-muted); }

.up { color: var(--up-color); }
.down { color: var(--down-color); }

.status-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;
}
.status-dot.green { background: var(--success); box-shadow: 0 0 6px var(--success); }
.status-dot.yellow { background: var(--warning); box-shadow: 0 0 6px var(--warning); }
.status-dot.red { background: var(--danger); box-shadow: 0 0 6px var(--danger); }

.progress-bar {
  height: 4px; background: rgba(255, 255, 255, 0.08); border-radius: 2px; overflow: hidden;
}
.progress-fill {
  height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-secondary));
  border-radius: 2px; transition: width 0.5s ease;
}

.risk-slider { /* Phase 3 策略工坊使用 */ }
.timeline { /* Phase 3 绩效回顾使用 */ }
```

- [ ] **Step 2: 重写 App.vue 导航栏**

```vue
<template>
  <div id="app">
    <nav class="navbar">
      <div class="nav-brand">
        <span class="nav-logo">🐕</span>
        <span class="nav-title">旺财V4</span>
      </div>
      <div class="nav-links">
        <router-link to="/" class="nav-link">策略看板</router-link>
        <router-link to="/workshop" class="nav-link">策略工坊</router-link>
        <router-link to="/holdings" class="nav-link">持仓总览</router-link>
        <router-link to="/review" class="nav-link">绩效回顾</router-link>
        <router-link to="/settings" class="nav-link">设置</router-link>
      </div>
    </nav>
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.navbar {
  height: 56px; background: var(--bg-card);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 24px; border-bottom: 1px solid rgba(255,255,255,0.04);
  position: sticky; top: 0; z-index: 100;
}
.nav-brand { display: flex; align-items: center; gap: 10px; }
.nav-logo { font-size: 24px; }
.nav-title { font-size: 18px; font-weight: 700; color: var(--accent); }
.nav-links { display: flex; gap: 4px; }
.nav-link {
  color: var(--text-secondary); text-decoration: none;
  padding: 8px 16px; border-radius: 6px; font-size: 14px;
  transition: all 0.2s;
}
.nav-link:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }
.nav-link.router-link-active { color: var(--accent); background: var(--accent-dim); }
.main-content { max-width: 1400px; margin: 0 auto; padding: 24px; }
</style>
```

- [ ] **Step 3: 更新路由**

在 `frontend/src/router/index.js` 中：
```javascript
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/workshop', name: 'Workshop', component: () => import('../views/Workshop.vue') },
  { path: '/holdings', name: 'Holdings', component: () => import('../views/Holdings.vue') },
  { path: '/review', name: 'Review', component: () => import('../views/Review.vue') },
  { path: '/settings', name: 'Settings', component: () => import('../views/Settings.vue') },
]

export default createRouter({ history: createWebHistory(), routes })
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/styles/index.css frontend/src/App.vue frontend/src/router/index.js
git commit -m "feat: redesign global styles, navigation, and routing for WangcaiV4"
```

---

### Task 3.4: 策略看板 Dashboard.vue 重设计

**Files:**
- Modify: `frontend/src/views/Dashboard.vue`
- Create: `frontend/src/components/StrategyStatus.vue`
- Create: `frontend/src/components/LifecyclePipeline.vue`

- [ ] **Step 1: 编写 StrategyStatus 组件**

```vue
<!-- frontend/src/components/StrategyStatus.vue -->
<template>
  <div class="card strategy-status">
    <div class="card-header">
      <span class="card-title">策略状态</span>
      <span class="status-badge" :class="statusClass">{{ statusLabel }}</span>
    </div>
    <div class="status-grid">
      <div class="status-item">
        <span class="data-label">风险等级</span>
        <span class="risk-badge" :class="`risk-${riskLevel}`">R{{ riskLevel }}</span>
      </div>
      <div class="status-item">
        <span class="data-label">仓位上限</span>
        <span class="data-value">{{ positionLimit }}%</span>
      </div>
      <div class="status-item">
        <span class="data-label">止损线</span>
        <span class="data-value down">{{ stopLoss }}%</span>
      </div>
      <div class="status-item">
        <span class="data-label">审查状态</span>
        <span class="review-status">
          <span class="status-dot" :class="reviewColor"></span>
          {{ reviewLabel }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  riskLevel: { type: Number, default: 3 },
  positionLimit: { type: Number, default: 30 },
  stopLoss: { type: Number, default: -5 },
  status: { type: String, default: 'draft' },
  reviewResult: { type: String, default: 'pass' },
})

const statusLabels = { draft: '草稿', confirmed: '已确认', planned: '已规划', executing: '执行中', completed: '已完成', reviewed: '已回顾' }
const statusLabel = computed(() => statusLabels[props.status] || props.status)
const statusClass = computed(() => `status-${props.status}`)
const reviewColor = computed(() => props.reviewResult === 'pass' ? 'green' : props.reviewResult === 'yellow' ? 'yellow' : 'red')
const reviewLabel = computed(() => props.reviewResult === 'pass' ? '全部通过' : props.reviewResult === 'yellow' ? '有预警' : '有告警')
</script>

<style scoped>
.strategy-status { margin-bottom: 16px; }
.status-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; }
.status-item { display: flex; flex-direction: column; gap: 4px; }
.risk-badge {
  display: inline-block; padding: 2px 10px; border-radius: 4px;
  font-weight: 600; font-size: 14px;
}
.risk-1 { background: var(--success); color: #fff; }
.risk-2 { background: #4ade80; color: #fff; }
.risk-3 { background: var(--accent); color: #fff; }
.risk-4 { background: #f97316; color: #fff; }
.risk-5 { background: var(--danger); color: #fff; }
.status-badge {
  padding: 2px 8px; border-radius: 4px; font-size: 12px;
  background: var(--accent-dim); color: var(--accent);
}
.review-status { display: flex; align-items: center; }
</style>
```

- [ ] **Step 2: 编写 LifecyclePipeline 组件**

```vue
<!-- frontend/src/components/LifecyclePipeline.vue -->
<template>
  <div class="card pipeline">
    <div class="card-header">
      <span class="card-title">策略生命周期</span>
    </div>
    <div class="pipeline-steps">
      <div
        v-for="(step, index) in steps"
        :key="step.key"
        class="pipeline-step"
        :class="{ active: currentStep === step.key, done: completedSteps.includes(step.key) }"
      >
        <div class="step-number">{{ index + 1 }}</div>
        <div class="step-label">{{ step.label }}</div>
        <div class="step-arrow" v-if="index < steps.length - 1">→</div>
      </div>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  currentStep: { type: String, default: 'analysis' },
  completedSteps: { type: Array, default: () => [] },
})

const steps = [
  { key: 'analysis', label: '分析研判' },
  { key: 'workshop', label: '策略工坊' },
  { key: 'planning', label: '执行规划' },
  { key: 'executing', label: '持仓执行' },
  { key: 'retro', label: '绩效回顾' },
  { key: 'review', label: '每日审查' },
]

import { computed } from 'vue'
const progressPct = computed(() => {
  const idx = steps.findIndex(s => s.key === props.currentStep)
  return idx >= 0 ? Math.round((idx / (steps.length - 1)) * 100) : 0
})
</script>

<style scoped>
.pipeline { margin-bottom: 16px; }
.pipeline-steps { display: flex; align-items: center; justify-content: space-between; padding: 12px 0; }
.pipeline-step { display: flex; align-items: center; gap: 8px; opacity: 0.35; transition: opacity 0.3s; }
.pipeline-step.active { opacity: 1; }
.pipeline-step.done { opacity: 0.7; }
.step-number {
  width: 28px; height: 28px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 600; background: rgba(255,255,255,0.08);
}
.pipeline-step.active .step-number { background: var(--accent); }
.pipeline-step.done .step-number { background: var(--success); }
.step-label { font-size: 12px; white-space: nowrap; }
.step-arrow { color: var(--text-muted); margin: 0 4px; }
</style>
```

- [ ] **Step 3: 重写 Dashboard.vue**

```vue
<template>
  <div class="dashboard">
    <div class="top-row">
      <StrategyStatus
        :risk-level="strategy?.risk_level || 3"
        :position-limit="strategy?.position_limit_pct || 30"
        :stop-loss="strategy?.stop_loss_pct || -5"
        :status="strategy?.status || 'draft'"
        :review-result="reviewResult"
      />
    </div>
    <LifecyclePipeline
      :current-step="strategy?.status || 'analysis'"
      :completed-steps="completedSteps"
    />
    <div class="mid-row">
      <div class="card plan-card">
        <div class="card-header">
          <span class="card-title">操作计划书</span>
          <span class="card-badge">{{ planCount }} 条待执行</span>
        </div>
        <div v-if="planItems.length === 0" class="empty-state">暂无操作计划</div>
        <div v-for="item in planItems" :key="item.code" class="plan-item">
          <span class="stock-code">{{ item.code }}</span>
          <span class="stock-name">{{ item.name }}</span>
          <span :class="item.action === 'buy' ? 'up' : 'down'">{{ item.action === 'buy' ? '买入' : '卖出' }}</span>
          <span class="data-value">{{ item.amount }}</span>
        </div>
      </div>
      <div class="card perf-card">
        <div class="card-header"><span class="card-title">绩效概览</span></div>
        <div class="perf-grid">
          <div class="perf-item"><span class="data-label">累计收益</span><span class="data-value up">+12.3%</span></div>
          <div class="perf-item"><span class="data-label">今日收益</span><span class="data-value up">+1.2%</span></div>
          <div class="perf-item"><span class="data-label">本月收益</span><span class="data-value up">+5.8%</span></div>
          <div class="perf-item"><span class="data-label">胜率</span><span class="data-value">62%</span></div>
        </div>
      </div>
    </div>
    <PositionTracker :compact="true" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import StrategyStatus from '../components/StrategyStatus.vue'
import LifecyclePipeline from '../components/LifecyclePipeline.vue'
import PositionTracker from '../components/PositionTracker.vue'
import { getActiveStrategy } from '../api/strategy'

const strategy = ref(null)
const reviewResult = ref('pass')

const completedSteps = ref([])
const planItems = ref([])
const planCount = ref(0)

onMounted(async () => {
  try {
    const res = await getActiveStrategy()
    strategy.value = res.data
  } catch (e) {
    console.log('No active strategy')
  }
})
</script>

<style scoped>
.dashboard { display: flex; flex-direction: column; gap: 16px; }
.top-row { display: flex; gap: 16px; }
.mid-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.empty-state { color: var(--text-muted); text-align: center; padding: 24px; }
.plan-item { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
.stock-code { font-family: var(--font-mono); font-weight: 600; min-width: 60px; }
.stock-name { flex: 1; }
.perf-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.perf-item { display: flex; flex-direction: column; gap: 4px; }
.card-badge {
  padding: 2px 8px; border-radius: 4px; font-size: 12px;
  background: var(--accent-dim); color: var(--accent);
}
</style>
```

- [ ] **Step 4: 新建前端 API client**

创建 `frontend/src/api/strategy.js`：
```javascript
import api from './client'

export const getActiveStrategy = () => api.get('/api/strategy/active')
export const triggerAnalysis = () => api.post('/api/strategy/analysis')
export const triggerDebate = (id) => api.post(`/api/strategy/${id}/debate`)
export const confirmStrategy = (id, decision) => api.post(`/api/strategy/${id}/confirm`, decision)
export const getRiskLevels = () => api.get('/api/strategy/risk-levels')
export const askRole = (data) => api.post('/api/strategy/debate/ask', data)
export const generatePlan = (id) => api.post(`/api/strategy/${id}/plan`)
export const triggerReview = () => api.post('/api/strategy/review')
export const getReviewLogs = (days = 7) => api.get('/api/strategy/reviews', { params: { days } })
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Dashboard.vue frontend/src/components/StrategyStatus.vue frontend/src/components/LifecyclePipeline.vue frontend/src/api/strategy.js
git commit -m "feat: redesign Dashboard with StrategyStatus, LifecyclePipeline, and new API client"
```

---

### Task 3.5: 策略工坊 Workshop.vue

**Files:**
- Create: `frontend/src/views/Workshop.vue`
- Create: `frontend/src/components/DebateCards.vue`
- Create: `frontend/src/components/RiskSlider.vue`

- [ ] **Step 1: 编写 DebateCards 组件**

```vue
<!-- frontend/src/components/DebateCards.vue -->
<template>
  <div class="debate-cards">
    <div v-for="role in roles" :key="role.key" class="debate-card" :class="role.key" @click="$emit('ask', role.key)">
      <div class="role-header">
        <span class="role-icon">{{ role.icon }}</span>
        <span class="role-name">{{ role.name }}</span>
        <span class="role-view" :class="role.viewClass">{{ role.viewLabel }}</span>
      </div>
      <div class="role-arguments">
        <div v-for="(arg, i) in role.arguments" :key="i" class="role-arg">{{ arg }}</div>
      </div>
      <details v-if="role.reasoning">
        <summary>查看推理链</summary>
        <pre class="reasoning-text">{{ role.reasoning }}</pre>
      </details>
      <div class="ask-hint">点击追问 →</div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  roles: { type: Array, default: () => [] },
})
defineEmits(['ask'])
</script>

<style scoped>
.debate-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.debate-card {
  background: var(--bg-card); border-radius: var(--radius); padding: 16px;
  border: 1px solid transparent; cursor: pointer; transition: all 0.2s;
}
.debate-card:hover { border-color: var(--accent); }
.debate-card.hunter { border-left: 3px solid #ef4444; }
.debate-card.accountant { border-left: 3px solid #6366f1; }
.debate-card.guardian { border-left: 3px solid #22c55e; }
.role-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.role-icon { font-size: 20px; }
.role-name { font-weight: 600; font-size: 15px; }
.role-view { font-size: 12px; padding: 2px 6px; border-radius: 3px; }
.role-view.bullish { background: rgba(239,68,68,0.15); color: #ef4444; }
.role-view.neutral { background: rgba(240,160,80,0.15); color: #f0a050; }
.role-view.bearish { background: rgba(34,197,94,0.15); color: #22c55e; }
.role-arg { font-size: 13px; color: var(--text-secondary); padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
.role-arg:last-child { border-bottom: none; }
.reasoning-text {
  background: #0d1117; color: var(--text-secondary); padding: 12px;
  border-radius: 4px; font-size: 12px; max-height: 200px; overflow-y: auto;
  margin-top: 8px; white-space: pre-wrap;
}
.ask-hint { font-size: 11px; color: var(--text-muted); text-align: right; margin-top: 8px; }
</style>
```

- [ ] **Step 2: 编写 RiskSlider 组件**

```vue
<!-- frontend/src/components/RiskSlider.vue -->
<template>
  <div class="risk-slider">
    <div class="slider-header">
      <span class="card-title">风险等级</span>
      <span class="ai-recommend">AI 推荐: R{{ recommended }}</span>
    </div>
    <div class="slider-track">
      <button
        v-for="level in levels"
        :key="level.value"
        class="slider-btn"
        :class="{
          active: modelValue === level.value,
          recommended: level.value === recommended,
        }"
        @click="$emit('update:modelValue', level.value)"
      >
        <div class="level-value">R{{ level.value }}</div>
        <div class="level-label">{{ level.label }}</div>
      </button>
    </div>
    <div class="slider-info">
      <div class="info-item">
        <span class="data-label">仓位上限</span>
        <span class="data-value">{{ currentLevel.position_limit }}%</span>
      </div>
      <div class="info-item">
        <span class="data-label">止损线</span>
        <span class="data-value down">{{ currentLevel.stop_loss }}%</span>
      </div>
      <div class="info-item">
        <span class="data-label">标的类型</span>
        <span>{{ currentLevel.stock_types }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Number, default: 3 },
  recommended: { type: Number, default: 3 },
})
defineEmits(['update:modelValue'])

const levels = [
  { value: 1, label: '保守', position_limit: 10, stop_loss: -2, stock_types: 'ETF/债基' },
  { value: 2, label: '稳健', position_limit: 20, stop_loss: -3, stock_types: '蓝筹低波动' },
  { value: 3, label: '适中', position_limit: 30, stop_loss: -5, stock_types: '加入成长股' },
  { value: 4, label: '积极', position_limit: 50, stop_loss: -8, stock_types: '允许小盘' },
  { value: 5, label: '激进', position_limit: 70, stop_loss: -12, stock_types: '允许题材' },
]

const currentLevel = computed(() => levels.find(l => l.value === props.modelValue) || levels[2])
</script>

<style scoped>
.risk-slider { padding: 8px 0; }
.slider-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.ai-recommend { font-size: 12px; color: var(--accent); }
.slider-track { display: flex; gap: 4px; margin-bottom: 16px; }
.slider-btn {
  flex: 1; padding: 10px 4px; border: 2px solid transparent;
  background: rgba(255,255,255,0.04); border-radius: 6px;
  cursor: pointer; text-align: center; transition: all 0.2s; color: var(--text-secondary);
}
.slider-btn:hover { background: rgba(255,255,255,0.08); }
.slider-btn.active {
  border-color: var(--accent); background: var(--accent-dim); color: var(--text-primary);
}
.slider-btn.recommended { border-style: dashed; border-color: rgba(240,160,80,0.3); }
.level-value { font-weight: 700; font-size: 16px; }
.level-label { font-size: 11px; margin-top: 2px; }
.slider-info { display: flex; gap: 32px; }
.info-item { display: flex; flex-direction: column; gap: 2px; }
</style>
```

- [ ] **Step 3: 编写 Workshop.vue 页面**

```vue
<template>
  <div class="workshop">
    <div class="workshop-header">
      <h2>策略工坊</h2>
      <div class="workshop-actions">
        <button class="btn-primary" @click="startAnalysis" :disabled="loading">
          {{ loading ? '分析中...' : '开始分析研判' }}
        </button>
      </div>
    </div>

    <!-- ① 分析研判报告 -->
    <div v-if="report" class="card analysis-section">
      <div class="card-header"><span class="card-title">分析研判报告</span></div>
      <div class="score-grid">
        <div v-for="dim in dimensions" :key="dim.key" class="score-item">
          <div class="score-label">{{ dim.label }}</div>
          <div class="score-bar">
            <div class="score-fill" :style="{ width: dim.score + '%', background: dim.color }"></div>
          </div>
          <div class="score-value">{{ dim.score }}</div>
        </div>
      </div>
      <div class="plans-section">
        <div v-for="(plan, i) in (report.plans || [])" :key="i" class="plan-card">
          <div class="plan-header">
            <span class="plan-type">{{ plan.type === 'conservative' ? '保守' : plan.type === 'neutral' ? '中性' : '激进' }}</span>
            <span class="plan-return">{{ plan.expected_return?.neutral || 'N/A' }}</span>
          </div>
          <div class="plan-desc">{{ plan.description }}</div>
        </div>
      </div>
    </div>

    <!-- ② AI 辩论 -->
    <div v-if="debate" class="card debate-section">
      <div class="card-header"><span class="card-title">AI 多方辩论</span></div>
      <DebateCards :roles="debateRoles" @ask="handleAsk" />
      <div v-if="askAnswer" class="ask-response card">
        <div class="ask-header">
          <span class="ask-role">{{ askAnswer.role }}</span>
          <span class="ask-question">{{ askAnswer.question }}</span>
        </div>
        <div class="ask-answer">{{ askAnswer.answer }}</div>
      </div>
    </div>

    <!-- ③ 策略决策卡 -->
    <div v-if="debate" class="card decision-section">
      <div class="card-header"><span class="card-title">策略决策卡</span></div>
      <RiskSlider
        v-model="riskLevel"
        :recommended="debate?.decision?.recommended_risk_level || 3"
      />
      <div class="decision-actions">
        <button class="btn-primary" @click="confirmStrategy">确认生效</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import DebateCards from '../components/DebateCards.vue'
import RiskSlider from '../components/RiskSlider.vue'
import { triggerAnalysis, triggerDebate, confirmStrategy as confirmApi, askRole } from '../api/strategy'

const loading = ref(false)
const strategyId = ref(null)
const report = ref(null)
const debate = ref(null)
const riskLevel = ref(3)
const askAnswer = ref(null)

const dimensions = computed(() => {
  if (!report.value) return []
  return [
    { key: 'technical', label: '技术面', score: report.value.technical_score || 50, color: '#ef4444' },
    { key: 'fundamental', label: '基本面', score: report.value.fundamental_score || 50, color: '#6366f1' },
    { key: 'capital', label: '资金面', score: report.value.capital_score || 50, color: '#f0a050' },
    { key: 'sentiment', label: '情绪面', score: report.value.sentiment_score || 50, color: '#22c55e' },
  ]
})

const debateRoles = computed(() => {
  if (!debate.value?.roles) return []
  const views = { bullish: '看多', cautiously_bullish: '谨慎看多', neutral: '中性', cautious: '谨慎', bearish: '看空', defensive: '防守' }
  const viewClass = { bullish: 'bullish', cautiously_bullish: 'bullish', neutral: 'neutral', cautious: 'bearish', bearish: 'bearish', defensive: 'bearish' }
  return [
    { key: 'hunter', icon: '🔴', name: '猎手(进攻)', viewLabel: views[debate.value.roles.hunter?.view] || '', viewClass: viewClass[debate.value.roles.hunter?.view] || '', arguments: debate.value.roles.hunter?.key_arguments || [], reasoning: debate.value.roles.hunter?.reasoning_chain },
    { key: 'accountant', icon: '🔵', name: '账房(稳健)', viewLabel: views[debate.value.roles.accountant?.view] || '', viewClass: viewClass[debate.value.roles.accountant?.view] || '', arguments: debate.value.roles.accountant?.key_arguments || [], reasoning: debate.value.roles.accountant?.reasoning_chain },
    { key: 'guardian', icon: '🟢', name: '守夜人(保守)', viewLabel: views[debate.value.roles.guardian?.view] || '', viewClass: viewClass[debate.value.roles.guardian?.view] || '', arguments: debate.value.roles.guardian?.risk_factors || [], reasoning: debate.value.roles.guardian?.reasoning_chain },
  ]
})

async function startAnalysis() {
  loading.value = true
  try {
    const res = await triggerAnalysis()
    strategyId.value = res.data.strategy_id
    report.value = res.data.report
    // 自动进入辩论
    const debateRes = await triggerDebate(strategyId.value)
    debate.value = debateRes.data.debate
    riskLevel.value = debateRes.data.debate.decision?.recommended_risk_level || 3
  } catch (e) { console.error(e) }
  finally { loading.value = false }
}

async function handleAsk(role) {
  const question = prompt(`向${role}提问:`)
  if (!question) return
  const res = await askRole({ role, question, context: JSON.stringify(debate.value) })
  askAnswer.value = res.data
}

async function confirmStrategy() {
  await confirmApi(strategyId.value, { risk_level: riskLevel.value })
  alert('策略已确认生效')
}
</script>

<style scoped>
.workshop { display: flex; flex-direction: column; gap: 16px; }
.workshop-header { display: flex; align-items: center; justify-content: space-between; }
.workshop-header h2 { font-size: 20px; }
.btn-primary {
  padding: 8px 20px; background: var(--accent); color: #fff;
  border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600;
}
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.score-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; padding: 8px 0; }
.score-item { display: flex; flex-direction: column; gap: 6px; }
.score-label { font-size: 13px; color: var(--text-secondary); }
.score-bar { height: 6px; background: rgba(255,255,255,0.06); border-radius: 3px; overflow: hidden; }
.score-fill { height: 100%; border-radius: 3px; transition: width 0.5s ease; }
.score-value { font-family: var(--font-mono); font-size: 18px; font-weight: 600; }
.plans-section { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }
.plan-card { background: rgba(255,255,255,0.03); border-radius: 6px; padding: 14px; }
.plan-header { display: flex; justify-content: space-between; margin-bottom: 8px; }
.plan-type { font-weight: 600; font-size: 14px; }
.plan-return { font-family: var(--font-mono); color: var(--accent); }
.plan-desc { font-size: 13px; color: var(--text-secondary); }
.ask-response { margin-top: 12px; padding: 16px; background: rgba(99,102,241,0.06); }
.ask-header { display: flex; gap: 12px; margin-bottom: 8px; }
.ask-role { font-weight: 600; color: var(--accent-secondary); }
.ask-question { color: var(--text-secondary); }
.ask-answer { font-size: 14px; line-height: 1.7; white-space: pre-wrap; }
.decision-actions { margin-top: 16px; text-align: right; }
</style>
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/Workshop.vue frontend/src/components/DebateCards.vue frontend/src/components/RiskSlider.vue
git commit -m "feat: add Workshop page with Analysis, Debate, and Decision card"
```

---

## Phase 4: Polish — 绩效回顾 + 移动端 + 云端集成 (P3-P4)

### Task 4.1: 绩效回顾 Review.vue

**Files:**
- Create: `frontend/src/views/Review.vue`
- Create: `frontend/src/components/ReviewTimeline.vue`

- [ ] **Step 1: 编写 ReviewTimeline 组件**

```vue
<!-- frontend/src/components/ReviewTimeline.vue -->
<template>
  <div class="timeline">
    <div v-for="log in logs" :key="log.id" class="timeline-item">
      <div class="timeline-dot" :class="log.result"></div>
      <div class="timeline-content">
        <div class="timeline-date">{{ log.date }}</div>
        <div class="timeline-result" :class="log.result">
          {{ log.result === 'pass' ? '✅ 全部通过' : log.result === 'yellow' ? '⚠️ 有预警' : '🟠 有告警' }}
        </div>
        <div v-for="(v, i) in (log.violations || [])" :key="i" class="timeline-violation">
          <span class="violation-rule">{{ v.rule }}</span>: {{ v.detail }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ logs: { type: Array, default: () => [] } })
</script>

<style scoped>
.timeline { padding: 8px 0; }
.timeline-item { display: flex; gap: 16px; padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
.timeline-dot {
  width: 12px; height: 12px; border-radius: 50%; margin-top: 4px; flex-shrink: 0;
}
.timeline-dot.pass { background: var(--success); }
.timeline-dot.yellow { background: var(--warning); }
.timeline-dot.red { background: var(--danger); }
.timeline-date { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }
.timeline-result { font-weight: 600; margin: 4px 0; }
.timeline-result.pass { color: var(--success); }
.timeline-result.yellow { color: var(--warning); }
.timeline-result.red { color: var(--danger); }
.timeline-violation { font-size: 13px; color: var(--text-secondary); padding: 2px 0; }
.violation-rule { font-weight: 600; color: var(--accent); }
</style>
```

- [ ] **Step 2: 编写 Review.vue 页面**

```vue
<template>
  <div class="review-page">
    <h2>绩效回顾</h2>
    <div class="review-grid">
      <div class="card">
        <div class="card-header"><span class="card-title">收益曲线</span></div>
        <div class="perf-summary">
          <div class="perf-row"><span>累计收益</span><span class="up">+12.3%</span></div>
          <div class="perf-row"><span>本月收益</span><span class="up">+5.8%</span></div>
          <div class="perf-row"><span>最大回撤</span><span class="down">-8.2%</span></div>
          <div class="perf-row"><span>夏普比率</span><span>1.42</span></div>
          <div class="perf-row"><span>胜率</span><span>62%</span></div>
          <div class="perf-row"><span>盈亏比</span><span>2.1:1</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-title">策略匹配度</span></div>
        <div class="match-content">
          <div class="match-item">
            <span>策略一致性</span>
            <div class="progress-bar"><div class="progress-fill" style="width:85%"></div></div>
            <span>85%</span>
          </div>
          <div class="match-item">
            <span>风险控制</span>
            <div class="progress-bar"><div class="progress-fill" style="width:92%"></div></div>
            <span>92%</span>
          </div>
          <div class="match-item">
            <span>执行纪律</span>
            <div class="progress-bar"><div class="progress-fill" style="width:78%"></div></div>
            <span>78%</span>
          </div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><span class="card-title">每日审查日志</span></div>
      <ReviewTimeline :logs="reviewLogs" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import ReviewTimeline from '../components/ReviewTimeline.vue'
import { getReviewLogs } from '../api/strategy'

const reviewLogs = ref([])

onMounted(async () => {
  try {
    const res = await getReviewLogs(7)
    reviewLogs.value = res.data
  } catch (e) { console.error(e) }
})
</script>

<style scoped>
.review-page { display: flex; flex-direction: column; gap: 16px; }
.review-page h2 { font-size: 20px; }
.review-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.perf-summary { display: flex; flex-direction: column; gap: 12px; }
.perf-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
.match-content { display: flex; flex-direction: column; gap: 16px; }
.match-item { display: flex; align-items: center; gap: 12px; }
.match-item span:first-child { min-width: 80px; font-size: 13px; color: var(--text-secondary); }
.match-item .progress-bar { flex: 1; }
.match-item span:last-child { font-family: var(--font-mono); font-size: 13px; }
</style>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Review.vue frontend/src/components/ReviewTimeline.vue
git commit -m "feat: add Review page with performance overview and daily review timeline"
```

---

### Task 4.2: 前端验证 + 删除旧文件

- [ ] **Step 1: 删除废弃前端文件**

```bash
rm frontend/src/views/Analysis.vue
rm frontend/src/views/Trading.vue
rm frontend/src/views/CodeView.vue
```

- [ ] **Step 2: 验证前端构建**

```bash
cd frontend && npm run build
```

Expected: 构建成功，无报错。

- [ ] **Step 3: 删除废弃后端文件中剩余的引用**

确保以下文件不再被 import：
- `backend/app/ai/prompts.py`
- `backend/app/data_sources/sina_client.py`
- `backend/app/services/sync.py`
- `backend/app/trading_engine/scheduler.py`
- `backend/app/trading_engine/trend_tracker.py`

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove deprecated frontend files, verify build"
```

---

## 完成检查清单

- [ ] Phase 1: 数据模型创建，OllamaPool 可用，bge-m3 拉取完成，废弃文件删除
- [ ] Phase 2: 分析研判引擎能并行生成 4 维度 + 综合报告；策略工坊能执行 4 角色辩论 + 风险等级推荐
- [ ] Phase 3: 执行规划引擎能根据仓位生成操作计划；每日审查引擎能检测违规；前端 4+1 页面可访问
- [ ] Phase 4: 绩效回顾页面展示审查日志时间线
- [ ] 整体验证: `curl http://localhost:8000/api/health` 返回 ok，前端 `npm run build` 成功
