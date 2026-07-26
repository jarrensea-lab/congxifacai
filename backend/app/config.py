"""应用配置"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 将 .env.local 加载到 os.environ，供 os.getenv() 使用（如 tushare_client）
_env_path = os.path.join(PROJECT_ROOT, "..", ".env.local")
if os.path.exists(_env_path):
    load_dotenv(_env_path)


@dataclass(frozen=True)
class RuntimeDatabasePaths:
    business: str
    scheduler: str


@dataclass(frozen=True)
class RuntimeYitaojinPaths:
    bridge: Path
    account_snapshot: Path
    account_fingerprint_salt: Path
    account_audit: Path
    watchlist_state: Path
    watchlist_audit: Path
    quote_snapshot: Path
    runtime_status: Path


def resolve_runtime_state_dir() -> Path:
    """Return the local mutable state root without creating it."""
    return Path(
        os.getenv("CONGXI_STATE_DIR")
        or "~/Library/Application Support/congxicai-v7"
    ).expanduser()


def resolve_runtime_database_paths() -> RuntimeDatabasePaths:
    """Resolve mutable SQLite files onto the local home disk by default."""
    state_dir = resolve_runtime_state_dir()
    business = Path(
        os.getenv("CONGXI_DATABASE_PATH") or state_dir / "stock_data.db"
    ).expanduser()
    scheduler = Path(
        os.getenv("CONGXI_SCHEDULER_DATABASE_PATH") or state_dir / "scheduler_jobs.db"
    ).expanduser()
    return RuntimeDatabasePaths(business=str(business), scheduler=str(scheduler))


def resolve_runtime_yitaojin_paths() -> RuntimeYitaojinPaths:
    """Resolve broker-integration artifacts onto the local state disk."""
    state_dir = resolve_runtime_state_dir()
    integration_dir = state_dir / "yitaojin"

    def resolve(name: str, default: Path) -> Path:
        return Path(os.getenv(name) or default).expanduser()

    return RuntimeYitaojinPaths(
        bridge=resolve(
            "CONGXI_YITAOJIN_BRIDGE_PATH",
            state_dir / "bin" / "yitaojin-bridge",
        ),
        account_snapshot=resolve(
            "CONGXI_YITAOJIN_ACCOUNT_SNAPSHOT_PATH",
            integration_dir / "account_snapshot.json",
        ),
        account_fingerprint_salt=resolve(
            "CONGXI_YITAOJIN_ACCOUNT_FINGERPRINT_SALT_PATH",
            integration_dir / "account_fingerprint_salt",
        ),
        account_audit=resolve(
            "CONGXI_YITAOJIN_ACCOUNT_AUDIT_PATH",
            integration_dir / "account_sync_audit.jsonl",
        ),
        watchlist_state=resolve(
            "CONGXI_YITAOJIN_WATCHLIST_STATE_PATH",
            integration_dir / "watchlist_state.json",
        ),
        watchlist_audit=resolve(
            "CONGXI_YITAOJIN_WATCHLIST_AUDIT_PATH",
            integration_dir / "watchlist_sync_audit.jsonl",
        ),
        quote_snapshot=resolve(
            "CONGXI_YITAOJIN_QUOTE_SNAPSHOT_PATH",
            integration_dir / "quote_snapshot.json",
        ),
        runtime_status=resolve(
            "CONGXI_YITAOJIN_RUNTIME_STATUS_PATH",
            integration_dir / "runtime_status.json",
        ),
    )


_runtime_database_paths = resolve_runtime_database_paths()

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

    # Tushare 数据源
    TUSHARE_TOKEN: str = ""

    # 飞书机器人
    FEISHU_WEBHOOK_URL: str = ""
    FEISHU_WEBHOOK_ONLY: bool = False
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_CHAT_ID: str = ""
    FEISHU_API_BASE: str = "https://open.feishu.cn"
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

    # 广发易淘金：默认完全关闭，UI 写入还需第二个开关
    CONGXI_YITAOJIN_ENABLED: bool = False
    CONGXI_YITAOJIN_WRITE_ENABLED: bool = False
    CONGXI_YITAOJIN_APP_PATH: str = "/Applications/GF-Trader.app"
    CONGXI_YITAOJIN_TASK_TIMEOUT_SECONDS: int = 120
    CONGXI_YITAOJIN_APP_START_TIMEOUT_SECONDS: int = 30

    # 数据库
    DATABASE_PATH: str = _runtime_database_paths.business
    SCHEDULER_DATABASE_PATH: str = _runtime_database_paths.scheduler

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
        logging.getLogger("恭喜发财").warning(
            "DEEPSEEK_API_KEY 未配置，云端 AI 分析将不可用。"
            " 请在 .env.local 中设置 DEEPSEEK_API_KEY"
        )
    if not s.QWEN_API_KEY:
        import logging
        logging.getLogger("恭喜发财").warning(
            "QWEN_API_KEY 未配置，Qwen-Plus 裁判将不可用，会自动回退到 DeepSeek。"
            " 如需多模型多样性请在 .env.local 中设置 QWEN_API_KEY"
        )
    has_feishu_api = bool(s.FEISHU_APP_ID and s.FEISHU_APP_SECRET and s.FEISHU_CHAT_ID)
    has_webhook = bool(s.FEISHU_WEBHOOK_URL and "YOUR_WEBHOOK_ID" not in s.FEISHU_WEBHOOK_URL)
    if not has_feishu_api and not has_webhook:
        import logging
        logging.getLogger("恭喜发财").warning(
            "飞书 OpenAPI 与 Webhook 都未配置，飞书推送将不可用。"
            " 请在 .env.local 中设置 FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_CHAT_ID 或 FEISHU_WEBHOOK_URL"
        )
    return s


settings = get_settings()
