"""飞书桥接输出 — 基于 ~/.codex/feishu-bridge 的成熟实现

使用 check_inbox.py 的 inbox/outbox 机制:
- 推送消息: check_inbox.py reply <chat_id> <text>
- 接收消息: check_inbox.py list
- 标记已处理: check_inbox.py process <msg_id>

桥接守护进程 (launchd) 负责实际的 lark-cli 通信。
"""
import subprocess
import json
from typing import Optional, Dict, List
from app.utils.logger import logger
import os
from app.config import settings
from app.config import get_settings

BRIDGE_CHECK_FALLBACK = os.path.expanduser("~/.codex/feishu-bridge/check_inbox.py")

def _bridge_check():
    """Resolve bridge check_inbox.py path from settings or fallback."""
    s = get_settings()
    path = os.path.join(s.FEISHU_BRIDGE_PATH, "check_inbox.py")
    if os.path.exists(path):
        return path
    return BRIDGE_CHECK_FALLBACK

def _lark_cli():
    s = get_settings()
    return s.LARK_CLI_PATH

# 恭喜发财群聊 chat_id (需从 bridge 的 conversation 中获取)
DEFAULT_CHAT_ID = None  # 将在初始化时从状态文件自动获取


class FeishuBridge:
    """基于 feishu-bridge 的飞书消息通道"""

    def __init__(self):
        self._available = self._check_bridge()
        self.default_chat_id = self._load_default_chat()

    def _check_bridge(self) -> bool:
        try:
            result = subprocess.run(
                ["python3", _bridge_check(), "list"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _load_default_chat(self) -> Optional[str]:
        """从 bridge 状态文件中获取默认群聊 chat_id"""
        try:
            import os
            import json
            # 优先从 conversation.jsonl 中获取最近活跃的 chat_id
            conv_file = os.path.join(settings.FEISHU_BRIDGE_PATH, "conversation.jsonl")
            if os.path.exists(conv_file):
                with open(conv_file) as f:
                    last = None
                    for line in f:
                        last = line.strip()
                    if last:
                        entry = json.loads(last)
                        return entry.get("chat_id")
            # fallback: 从 sessions 获取
            sessions_file = os.path.join(settings.FEISHU_BRIDGE_PATH, "state", "sessions.json")
            if os.path.exists(sessions_file):
                with open(sessions_file) as f:
                    sessions = json.load(f)
                    if sessions:
                        return list(sessions.keys())[0]
        except Exception:
            pass
        return None

    @property
    def is_available(self) -> bool:
        return self._available and self.default_chat_id is not None

    async def send_card(self, title: str, content_md: str, color: str = "blue") -> bool:
        """通过 bridge outbox 发送富文本消息"""
        if not self.is_available:
            return False
        try:
            text = f"**{title}**\n{content_md[:4000]}"
            result = subprocess.run(
                ["python3", _bridge_check(), "reply", self.default_chat_id, text],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                logger.info(f"Bridge 消息已发送: {title}")
                return True
            logger.warning(f"Bridge 消息发送失败: {result.stderr}")
            return False
        except Exception as e:
            logger.error(f"Bridge 消息异常: {e}")
            return False

    async def send_text(self, text: str) -> bool:
        """发送纯文本消息"""
        return await self.send_card("", text)

    async def get_inbox(self) -> List[Dict]:
        """获取待处理消息列表"""
        if not self._available:
            return []
        try:
            result = subprocess.run(
                ["python3", _bridge_check(), "list"],
                capture_output=True, text=True, timeout=5
            )
            data = json.loads(result.stdout)
            return data.get("messages", [])
        except Exception:
            return []

    async def process_message(self, msg_id: str) -> bool:
        """标记消息为已处理"""
        try:
            result = subprocess.run(
                ["python3", _bridge_check(), "process", msg_id],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False



async def send_via_lark_cli(text: str, chat_id: str = None) -> bool:
    """通过 lark-cli 直接发送消息到飞书群聊"""
    import subprocess
    import json
    cid = chat_id or "oc_c51ef6103f2e0b5b9ed9c40ab86b3e45"
    try:
        r = subprocess.run(
            ["/Users/zhuchenyuan/.npm-global/bin/lark-cli", "im", "+messages-send",
             "--chat-id", cid, "--text", text[:8000], "--as", "bot"],
            capture_output=True, text=True, timeout=15
        )
        output = r.stdout.strip()
        if output:
            data = json.loads(output.split("\n")[-1])
            ok = data.get("ok", False)
            if ok: logger.info("lark IM 发送成功")
            return ok
        return False
    except Exception as e:
        logger.warning(f"lark IM 发送失败: {e}")
        return False


# 全局单例
feishu_channels = FeishuBridge()
