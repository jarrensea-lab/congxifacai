"""V7 飞书双通道推送 — Webhook卡片 + lark-cli IM文本"""
import subprocess
import json
import httpx
from app.utils.logger import logger

LARK_CLI = "/Users/zhuchenyuan/.npm-global/bin/lark-cli"
CONGXI_CHAT_ID = "oc_c51ef6103f2e0b5b9ed9c40ab86b3e45"


def send_lark_text(text: str, chat_id: str = None) -> bool:
    """通过 lark-cli IM 发送文本消息到群聊"""
    cid = chat_id or CONGXI_CHAT_ID
    try:
        result = subprocess.run(
            [LARK_CLI, "im", "+messages-send", "--chat-id", cid, "--text", text[:8000], "--as", "bot"],
            capture_output=True, text=True, timeout=15
        )
        # lark-cli sends [WARN] to stderr but JSON to stdout
        for line in result.stdout.strip().split("\n"):
            if line.strip().startswith("{") and '"ok"' in line:
                data = json.loads(line)
                if data.get("ok"):
                    return True
        return False
    except Exception as e:
        logger.warning(f"lark IM text push failed: {e}")
        return False


async def send_webhook_card(webhook_url: str, title: str, content: str, color: str = "blue") -> bool:
    """通过 Feishu Webhook 发送富文本卡片"""
    if not webhook_url or "YOUR_WEBHOOK" in webhook_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            payload = {
                "msg_type": "interactive",
                "card": {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {"tag": "plain_text", "content": title},
                        "template": "blue",
                    },
                    "elements": [{"tag": "markdown", "content": content[:3000]}],
                },
            }
            resp = await client.post(webhook_url, json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Webhook push failed: {e}")
        return False


def push_report_to_feishu(webhook_url: str, title: str, report_md: str, chat_id: str = None) -> dict:
    """双通道推送策略报告: Webhook卡片(摘要) + lark-cli IM(全文)

    Returns:
        {"webhook": bool, "lark_im": bool}
    """
    import asyncio
    cid = chat_id or CONGXI_CHAT_ID
    results = {"webhook": False, "lark_im": False}

    # 1. Webhook 卡片推送 (摘要)
    summary = report_md[:2500] + ("\n\n ... *(完整报告已推送至群聊)*" if len(report_md) > 2500 else "")
    try:
        results["webhook"] = asyncio.get_event_loop().run_until_complete(
            send_webhook_card(webhook_url, title, summary)
        )
    except Exception as e:
        logger.warning(f"Webhook failed: {e}")

    # 2. lark-cli IM 文本推送 (完整)
    try:
        full_text = f"**{title}**\n\n{report_md[:7500]}"
        results["lark_im"] = send_lark_text(full_text, cid)
    except Exception as e:
        logger.warning(f"lark IM failed: {e}")

    return results
