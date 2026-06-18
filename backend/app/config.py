"""应用配置"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# v6: 云端模型 (全功能通过 DeepSeek, 可扩展 Qwen)
CLOUD_MODELS = {
    "judge": "deepseek-chat",       # DeepSeek — 裁判（默认）
    "analyst": "deepseek-chat",     # 猎手/账房/守夜人 — 主力分析
    "reporter": "deepseek-chat",    # 盘中快速/校验/情绪
    "qwen_judge": "qwen-plus",       # Qwen-Plus — 可选第二模型，辩论裁判
}


class Settings(BaseSettings):
    """应用配置类"""
    # DeepSeek API 配置
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_BASE: str = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")

    # Qwen (通义千问) API 配置 — 用于多模型多样性
    QWEN_API_KEY: str = ""
    QWEN_API_BASE: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-plus"

    # 飞书机器人
    FEISHU_WEBHOOK_URL: str = ""
    FEISHU_BRIDGE_PATH: str = os.path.expanduser("~/.codex/feishu-bridge")
    LARK_CLI_PATH: str = "/Users/zhuchenyuan/.npm-global/bin/lark-cli"

    # 飞书 Bot 授权 — 仅允许列表中的 open_id 执行交易指令
    FEISHU_ALLOWED_USERS: str = "[]"  # JSON 字符串，如 ["ou_xxx", "ou_yyy"]

    # 飞书多维表格配置
    FEISHU_BITABLE_APP_TOKEN: str = ""
    FEISHU_TABLE_STRATEGY: str = ""
    FEISHU_TABLE_STOCK_POOL: str = ""
    FEISHU_TABLE_POSITIONS: str = ""
    FEISHU_TABLE_INDICES: str = ""
    FEISHU_TABLE_RISK: str = ""
    FEISHU_TABLE_PERFORMANCE: str = ""

    # 服务配置
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000

    # 数据库
    DATABASE_PATH: str = os.path.join(PROJECT_ROOT, "data", "stock_data.db")

    # 缓存配置
    CACHE_MAX_SIZE: int = 1000
    CACHE_TTL: int = 300  # 5 分钟

    model_config = SettingsConfigDict(
        env_file=os.path.join(PROJECT_ROOT, "..", ".env.local"),
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    s = Settings()
    # 安全检查: 密钥未配置或使用示例值时警告
    if not s.DEEPSEEK_API_KEY:
        import logging
        logging.getLogger("cong-xi-fa-cai").warning(
            "DEEPSEEK_API_KEY 未配置，云端 AI 分析将不可用。"
            " 请在 .env.local 中设置 DEEPSEEK_API_KEY"
        )
    if not s.QWEN_API_KEY:
        import logging
        logging.getLogger("cong-xi-fa-cai").warning(
            "QWEN_API_KEY 未配置，Qwen-Plus 裁判将不可用，会自动回退到 DeepSeek。"
            " 如需多模型多样性请在 .env.local 中设置 QWEN_API_KEY"
        )
    if not s.FEISHU_WEBHOOK_URL or "YOUR_WEBHOOK_ID" in s.FEISHU_WEBHOOK_URL:
        import logging
        logging.getLogger("cong-xi-fa-cai").warning(
            "FEISHU_WEBHOOK_URL 未配置或使用示例值，飞书推送将不可用。"
            " 请在 .env.local 中设置正确的 Webhook URL"
        )
    return s


settings = get_settings()
