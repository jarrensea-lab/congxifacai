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
