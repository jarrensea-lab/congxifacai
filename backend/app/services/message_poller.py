"""消息轮询 — 检查飞书桥接新消息并分发处理"""
import json
import subprocess
from typing import Optional, Dict, Any
from app.config import get_settings
from app.services.bot_commands import process_message
from app.services.reply_formatter import format_reply


def check_and_process_new_messages() -> Optional[Dict]:
    """检查飞书桥接是否有新消息，有则处理（带用户鉴权）"""
    s = get_settings()
    import os as _os
    bridge = _os.path.join(s.FEISHU_BRIDGE_PATH, "check_inbox.py")

    if not _os.path.exists(bridge):
        return None

    # 加载授权用户白名单
    allowed_users = json.loads(s.FEISHU_ALLOWED_USERS) if s.FEISHU_ALLOWED_USERS else []
    if not allowed_users:
        return None

    try:
        result = subprocess.run(
            ["python3", bridge, "list"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout) if result.stdout.strip() else {}
        messages = data.get("messages", [])
    except Exception:
        return None

    for msg in messages:
        text = msg.get("text", "")
        msg_id = msg.get("id", "")
        chat_id = msg.get("chat_id", "")
        sender = msg.get("sender", "")

        if not text or not msg_id:
            continue

        # 鉴权检查
        if sender not in allowed_users:
            try:
                subprocess.run(
                    ["python3", bridge, "reply", chat_id or "default",
                     "❌ 未授权的用户，无法执行交易指令"],
                    capture_output=True, timeout=10
                )
                subprocess.run(
                    ["python3", bridge, "process", msg_id],
                    capture_output=True, timeout=5
                )
            except Exception:
                pass
            continue

        result = process_message(text)

        # 回复确认
        if result.get("ok"):
            reply = format_reply(result)
        else:
            reply = f"❌ 指令处理失败: {result.get('error', '未知错误')}"
            if result.get("action") == "parse_error":
                reply += "\n支持: 买入/卖出/清仓/持仓更新"

        try:
            subprocess.run(
                ["python3", bridge, "reply", chat_id or "default", reply],
                capture_output=True, timeout=10
            )
            subprocess.run(
                ["python3", bridge, "process", msg_id],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

    return None
