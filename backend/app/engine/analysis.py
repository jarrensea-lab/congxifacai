"""① 分析研判引擎 — 多维度并行分析，产出投资倾向性报告"""
import asyncio
import json
from datetime import datetime
from app.ai.cloud_client import cloud


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
        "plans": synthesis.get("plans", []),
        "key_risks": synthesis.get("key_risks", []),
        "market_context": synthesis.get("market_context", ""),
        "data_sources": [
            "Tushare + 腾讯 (行情/K线)",
            "a-stock-data (板块/资金/研报/公告)",
            "a-stock-data 财联社+东财 (新闻)",
            "DeepSeek v4-pro + v4-flash (云端)",
        ],
        "generated_at": str(datetime.now()),
        # 透传原始市场数据供下游辩论使用
        "holdings_str": market_data.get("holdings_str", "无持仓"),
        "available_cash": market_data.get("available_cash", 0),
        "news": market_data.get("news", []),
    }


async def _analyze_technical(data: dict) -> dict:
    """技术面分析 → qwen3.6:35b-mlx (本地)"""
    holdings_str = data.get("holdings_str", "无持仓")
    indices = json.dumps(data.get("indices", {}), ensure_ascii=False)
    prompt = f"""你是一位技术分析师。请基于以下数据做短线(1-5天)技术面分析，并针对当前持仓给出具体的买卖持有建议。

大盘指数: {indices}

当前持仓:
{holdings_str}

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
    return await _call_model("hunter", prompt)


async def _analyze_fundamental(data: dict) -> dict:
    """基本面/估值分析 → qwen3.5:9b (本地 fallback, 未来切云端)"""
    holdings_str = data.get("holdings_str", "无持仓")
    prompt = f"""你是一位基本面分析师。请基于以下数据做中低频(1-4周)基本面分析，并针对当前持仓给出估值判断和操作建议。

当前持仓:
{holdings_str}

请输出 JSON:
{{
    "score": 0-100,
    "valuation": "undervalued|fair|overvalued",
    "signals": [{{"stock_code":"000001","stock_name":"股票","signal":"buy|sell|hold","reason":"理由","confidence":0-100}}],
    "macro_outlook": "宏观环境一句话判断",
    "summary": "一句话基本面总结"
}}

只输出 JSON。"""
    return await _call_model("accountant", prompt)


async def _analyze_capital_flow(data: dict) -> dict:
    """资金面分析 → qwen3.5:9b (本地)"""
    sectors = json.dumps(data.get("sectors", []), ensure_ascii=False)
    prompt = f"""你是一位资金面分析师。请基于以下数据分析资金流向。

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
    return await _call_model("accountant", prompt)


async def _analyze_sentiment(data: dict) -> dict:
    """情绪面/题材分析 → qwen3.5:2b (本地 fallback, 未来切云端)"""
    news = json.dumps(data.get("news", []), ensure_ascii=False)
    prompt = f"""你是一位市场情绪分析师。请基于以下新闻数据分析市场情绪。

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
    return await _call_model("gatekeeper", prompt)


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
    return await _call_model("judge", prompt)


# v6: cloud role mapping
_CLOUD_ROLES = {
    "hunter": "analyst",
    "accountant": "analyst",
    "judge": "analyst",
    "gatekeeper": "reporter",
}

async def _call_model(model_key: str, prompt: str) -> dict:
    """统一模型调用 — DeepSeek 云端 + JSON 解析"""
    cloud_role = _CLOUD_ROLES.get(model_key, "reporter")
    try:
        result = await cloud.chat(cloud_role, [{"role": "user", "content": prompt}], max_tokens=4096)
        text = result.get("content", "{}").strip()
    except Exception as e:
        return {"raw": "", "error": f"AI 调用失败: {e}"}

    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "error": "JSON parse failed"}
