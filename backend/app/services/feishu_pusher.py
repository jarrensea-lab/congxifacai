"""V7 飞书双通道推送 — Webhook卡片 + lark-cli IM文本"""
import subprocess
import json
import time
import httpx
import requests as sync_requests
from app.utils.logger import logger
from app.services.push_tracker import compute_retry_delay

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


def push_webhook_retry(title: str, content: str, webhook_url: str = None) -> bool:
    """同步飞书 webhook 推送 + 指数退避重试（供 APScheduler 线程使用）

    Args:
        title: 消息标题
        content: 消息内容
        webhook_url: 可选覆盖 webhook URL，默认使用 settings.FEISHU_WEBHOOK_URL

    Returns:
        是否推送成功
    """
    from app.config import settings
    url = webhook_url or settings.FEISHU_WEBHOOK_URL
    if not url or "YOUR_WEBHOOK" in url:
        logger.warning("飞书Webhook未配置，跳过推送")
        return False

    MAX_RETRIES = 3
    BASE_DELAY = 10

    for attempt in range(1 + MAX_RETRIES):
        try:
            template = "red"
            if "风险" not in title and "熔断" not in title and "告警" not in title:
                template = "green" if any(kw in title for kw in ("检查", "无忧", "空仓")) else "blue"
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": title},
                               "template": template},
                    "elements": [{"tag": "markdown", "content": content[:3000]}],
                },
            }
            resp = sync_requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info(f"Webhook OK (attempt {attempt+1}): {title}")
                return True
            logger.warning(f"Webhook FAIL (attempt {attempt+1}/{MAX_RETRIES+1}): {resp.status_code} - {title}")
            if 400 <= resp.status_code < 500:
                logger.warning(f"Webhook 4xx 不重试: {resp.status_code}")
                return False
        except sync_requests.exceptions.Timeout:
            logger.warning(f"Webhook 超时 (attempt {attempt+1}): {title}")
        except sync_requests.exceptions.ConnectionError as e:
            logger.warning(f"Webhook 连接失败 (attempt {attempt+1}): {e}")
        except Exception as e:
            logger.warning(f"Webhook 异常 (attempt {attempt+1}): {e}")

        if attempt == MAX_RETRIES:
            logger.error(f"Webhook 已达最大重试次数 ({MAX_RETRIES})，放弃: {title}")
            return False

        delay = compute_retry_delay(attempt + 1, BASE_DELAY, 120)
        logger.info(f"Webhook 将在 {delay:.0f}s 后重试...")
        time.sleep(delay)

    return False
