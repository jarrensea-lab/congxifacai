"""V7 飞书推送 — OpenAPI 优先，Webhook/lark-cli 兜底。"""
import subprocess
import json
import time
import httpx
from app.utils.logger import logger

LARK_CLI = "/Users/zhuchenyuan/.npm-global/bin/lark-cli"
CONGXI_CHAT_ID = "oc_c51ef6103f2e0b5b9ed9c40ab86b3e45"
_TOKEN_CACHE: dict[str, object] = {}


def _placeholder(value: str | None) -> bool:
    return not value or "YOUR_" in value


def _interactive_card(title: str, content: str, color: str = "blue") -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [{"tag": "markdown", "content": content[:3000]}],
    }


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
    if _placeholder(webhook_url):
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            payload = {
                "msg_type": "interactive",
                "card": _interactive_card(title, content, color),
            }
            resp = await client.post(webhook_url, json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Webhook push failed: {e}")
        return False


async def send_api_card(
    *,
    app_id: str,
    app_secret: str,
    chat_id: str,
    title: str,
    content: str,
    color: str = "blue",
    api_base: str = "https://open.feishu.cn",
) -> bool:
    """通过飞书开放平台 API 发送群聊交互卡片。"""
    if _placeholder(app_id) or _placeholder(app_secret) or _placeholder(chat_id):
        return False
    base = api_base.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token = await get_tenant_access_token(client, base, app_id, app_secret)
            if not token:
                return False
            msg_resp = await client.post(
                f"{base}/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "interactive",
                    "content": json.dumps(_interactive_card(title, content, color), ensure_ascii=False),
                },
            )
            if msg_resp.status_code == 200:
                payload = msg_resp.json()
                return payload.get("code") in (0, None)
            logger.warning(f"Feishu API message failed: {msg_resp.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Feishu API push failed: {e}")
        return False


async def get_tenant_access_token(client: httpx.AsyncClient, api_base: str, app_id: str, app_secret: str) -> str:
    """Return cached tenant token; refresh before expiry to save API quota."""
    cache_key = f"{api_base}:{app_id}"
    now = time.time()
    cached = _TOKEN_CACHE.get(cache_key)
    if isinstance(cached, dict) and cached.get("token") and float(cached.get("expires_at", 0)) > now + 300:
        return str(cached["token"])

    token_resp = await client.post(
        f"{api_base}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    token_payload = token_resp.json()
    token = token_payload.get("tenant_access_token")
    if token_resp.status_code != 200 or not token:
        logger.warning("Feishu API token request failed")
        return ""
    expire = float(token_payload.get("expire") or 7200)
    _TOKEN_CACHE[cache_key] = {"token": token, "expires_at": now + expire}
    return str(token)


async def send_feishu_card(
    *,
    title: str,
    content: str,
    webhook_url: str = "",
    color: str = "blue",
    app_id: str = "",
    app_secret: str = "",
    chat_id: str = "",
    api_base: str = "https://open.feishu.cn",
) -> dict:
    """API 优先、Webhook 兜底发送飞书卡片。"""
    api_ok = await send_api_card(
        app_id=app_id,
        app_secret=app_secret,
        chat_id=chat_id,
        title=title,
        content=content,
        color=color,
        api_base=api_base,
    )
    if api_ok:
        return {"feishu_api": True, "feishu_webhook": False, "channel": "api", "error": ""}
    webhook_ok = await send_webhook_card(webhook_url, title, content, color)
    return {
        "feishu_api": False,
        "feishu_webhook": webhook_ok,
        "channel": "webhook" if webhook_ok else "",
        "error": "" if webhook_ok else "Feishu API 未配置/失败，Webhook 也未成功",
    }


def send_feishu_card_sync(
    *,
    title: str,
    content: str,
    webhook_url: str = "",
    color: str = "blue",
    app_id: str = "",
    app_secret: str = "",
    chat_id: str = "",
    api_base: str = "https://open.feishu.cn",
) -> dict:
    """同步场景使用的飞书卡片推送。"""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return {"feishu_api": False, "feishu_webhook": False, "channel": "", "error": "event_loop_running"}
        return loop.run_until_complete(
            send_feishu_card(
                title=title,
                content=content,
                webhook_url=webhook_url,
                color=color,
                app_id=app_id,
                app_secret=app_secret,
                chat_id=chat_id,
                api_base=api_base,
            )
        )
    except RuntimeError:
        return asyncio.run(
            send_feishu_card(
                title=title,
                content=content,
                webhook_url=webhook_url,
                color=color,
                app_id=app_id,
                app_secret=app_secret,
                chat_id=chat_id,
                api_base=api_base,
            )
        )


def push_report_to_feishu(webhook_url: str, title: str, report_md: str, chat_id: str = None) -> dict:
    """双通道推送策略报告: Webhook卡片(摘要) + lark-cli IM(全文)

    Returns:
        {"webhook": bool, "lark_im": bool}
    """
    import asyncio
    cid = chat_id or CONGXI_CHAT_ID
    results = {"webhook": False, "feishu_api": False, "lark_im": False}

    # 1. Webhook 卡片推送 (摘要)
    summary = report_md[:2500] + ("\n\n ... *(完整报告已推送至群聊)*" if len(report_md) > 2500 else "")
    try:
        from app.config import settings

        card_result = asyncio.get_event_loop().run_until_complete(
            send_feishu_card(
                title=title,
                content=summary,
                webhook_url=webhook_url,
                app_id=getattr(settings, "FEISHU_APP_ID", ""),
                app_secret=getattr(settings, "FEISHU_APP_SECRET", ""),
                chat_id=getattr(settings, "FEISHU_CHAT_ID", ""),
                api_base=getattr(settings, "FEISHU_API_BASE", "https://open.feishu.cn"),
            )
        )
        results["feishu_api"] = card_result.get("feishu_api", False)
        results["webhook"] = card_result.get("feishu_webhook", False)
    except Exception as e:
        logger.warning(f"Feishu card push failed: {e}")

    # 2. lark-cli IM 文本推送 (完整)
    try:
        from app.config import settings

        if not getattr(settings, "FEISHU_WEBHOOK_ONLY", False):
            full_text = f"**{title}**\n\n{report_md[:7500]}"
            results["lark_im"] = send_lark_text(full_text, cid)
    except Exception as e:
        logger.warning(f"lark IM failed: {e}")

    return results
