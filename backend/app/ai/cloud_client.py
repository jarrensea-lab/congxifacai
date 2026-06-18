"""云端模型客户端 — DeepSeek (默认) + Qwen (可选)"""
import httpx
import json
from app.config import CLOUD_MODELS, settings


class CloudClient:
    """多模型 API 客户端，支持 DeepSeek v4 / Qwen-Plus 等"""

    def __init__(self):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            headers={"Content-Type": "application/json"},
        )

    async def chat(self, model_key: str, messages: list[dict], **kwargs) -> dict:
        """调用云端模型进行对话。

        Args:
            model_key: CLOUD_MODELS key (analyst/reporter/qwen_judge 等)
            messages: [{"role":"user","content":"..."}, ...]
            **kwargs: temperature, max_tokens 等
        """
        # Qwen 回退：未配置 key 时静默切回 DeepSeek
        if model_key.startswith("qwen_") and not settings.QWEN_API_KEY:
            fallback_key = model_key.replace("qwen_", "")
            model_name = CLOUD_MODELS.get(fallback_key)
            if not model_name:
                raise ValueError(f"No fallback for {model_key}")
            return await self._call_deepseek(model_name, messages, fallback_key, **kwargs)

        model_name = CLOUD_MODELS.get(model_key)
        if not model_name:
            raise ValueError(f"Unknown cloud model: {model_key}")

        if model_key.startswith("qwen_"):
            return await self._call_qwen(model_name, messages, **kwargs)
        else:
            return await self._call_deepseek(model_name, messages, model_key, **kwargs)

    async def _call_deepseek(self, model_name: str, messages: list[dict],
                              model_key: str = "", **kwargs) -> dict:
        """调用 DeepSeek API"""
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }

        resp = await self._client.post(
            f"{settings.DEEPSEEK_API_BASE}/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
        )
        if resp.status_code != 200:
            app_logger = __import__('logging').getLogger('cong-xi-fa-cai')
            app_logger.warning(f"DeepSeek API {resp.status_code}: {resp.text[:200]}")
            return {"content": json.dumps({"error": f"DeepSeek API {resp.status_code}"}, ensure_ascii=False), "model": model_name, "usage": {}}
        data = resp.json()
        return {
            "content": data["choices"][0]["message"]["content"],
            "model": data.get("model", model_name),
            "usage": data.get("usage", {}),
        }

    async def _call_qwen(self, model_name: str, messages: list[dict], **kwargs) -> dict:
        """调用阿里云 Qwen 百炼 API (OpenAI 兼容格式)"""
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }

        resp = await self._client.post(
            f"{settings.QWEN_API_BASE}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.QWEN_API_KEY}"},
        )
        if resp.status_code != 200:
            app_logger = __import__('logging').getLogger('cong-xi-fa-cai')
            app_logger.warning(f"Qwen API {resp.status_code}: {resp.text[:200]}")
            return {"content": json.dumps({"error": f"Qwen API {resp.status_code}"}, ensure_ascii=False), "model": model_name, "usage": {}}
        data = resp.json()
        return {
            "content": data["choices"][0]["message"]["content"],
            "model": data.get("model", model_name),
            "usage": data.get("usage", {}),
        }

    async def analyze_research_report(self, report_text: str) -> dict:
        """深度研报解读 DeepSeek"""
        messages = [
            {"role": "system", "content": "你是一位资深证券分析师，擅长解读研究报告、行业分析和产业链调研。"},
            {"role": "user", "content": f"请深度解读以下研报，提取关键信息：\n\n{report_text[:8000]}\n\n输出JSON格式：{{\"key_points\":[],\"valuation_analysis\":\"\",\"industry_chain\":\"\",\"risk_factors\":[],\"recommendation\":\"\"}}"},
        ]
        result = await self.chat("analyst", messages, max_tokens=4096)
        return _parse_json(result.get("content", "{}"))

    async def analyze_sentiment(self, news_texts: list[str]) -> dict:
        """新闻情绪分析 DeepSeek"""
        text = "\n---\n".join(news_texts[:20])
        messages = [
            {"role": "system", "content": "你是一位市场情绪分析师，擅长从新闻中提取市场情绪和题材热点。"},
            {"role": "user", "content": f"分析以下新闻的情绪倾向和热点题材：\n\n{text[:6000]}\n\n输出JSON：{{\"overall_sentiment\":\"positive|neutral|negative\",\"hot_topics\":[],\"sentiment_score\":0-100,\"key_events\":[],\"market_impact\":\"\"}}"},
        ]
        result = await self.chat("reporter", messages, max_tokens=2048)
        return _parse_json(result.get("content", "{}"))

    async def is_available(self) -> bool:
        """检查云端 API 是否可用"""
        if not settings.DEEPSEEK_API_KEY:
            return False
        try:
            await self.chat("reporter", [{"role": "user", "content": "ping"}], max_tokens=10)
            return True
        except Exception:
            return False

    async def close(self):
        await self._client.aclose()


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


# 全局单例
cloud = CloudClient()
