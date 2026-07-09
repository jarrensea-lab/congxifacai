import pytest

from app.services import feishu_pusher


@pytest.mark.asyncio
async def test_send_feishu_card_prefers_api(monkeypatch):
    calls = {"api": 0, "webhook": 0}

    async def fake_api(**kwargs):
        calls["api"] += 1
        return True

    async def fake_webhook(*args, **kwargs):
        calls["webhook"] += 1
        return True

    monkeypatch.setattr(feishu_pusher, "send_api_card", fake_api)
    monkeypatch.setattr(feishu_pusher, "send_webhook_card", fake_webhook)

    result = await feishu_pusher.send_feishu_card(
        title="测试",
        content="内容",
        webhook_url="https://example.test/webhook",
        app_id="cli_xxx",
        app_secret="secret",
        chat_id="oc_xxx",
    )

    assert result["feishu_api"] is True
    assert result["feishu_webhook"] is False
    assert result["channel"] == "api"
    assert calls == {"api": 1, "webhook": 0}


@pytest.mark.asyncio
async def test_send_feishu_card_falls_back_to_webhook(monkeypatch):
    async def fake_api(**kwargs):
        return False

    async def fake_webhook(*args, **kwargs):
        return True

    monkeypatch.setattr(feishu_pusher, "send_api_card", fake_api)
    monkeypatch.setattr(feishu_pusher, "send_webhook_card", fake_webhook)

    result = await feishu_pusher.send_feishu_card(
        title="测试",
        content="内容",
        webhook_url="https://example.test/webhook",
    )

    assert result["feishu_api"] is False
    assert result["feishu_webhook"] is True
    assert result["channel"] == "webhook"


@pytest.mark.asyncio
async def test_send_api_card_uses_feishu_message_api(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            requests.append({"url": url, **kwargs})
            if url.endswith("/tenant_access_token/internal"):
                return FakeResponse({"tenant_access_token": "tenant-token"})
            return FakeResponse({"code": 0})

    monkeypatch.setattr(feishu_pusher.httpx, "AsyncClient", FakeClient)

    ok = await feishu_pusher.send_api_card(
        app_id="cli_xxx",
        app_secret="secret",
        chat_id="oc_xxx",
        title="标题",
        content="正文",
    )

    assert ok is True
    assert requests[0]["json"] == {"app_id": "cli_xxx", "app_secret": "secret"}
    assert requests[1]["params"] == {"receive_id_type": "chat_id"}
    assert requests[1]["headers"] == {"Authorization": "Bearer tenant-token"}
    assert requests[1]["json"]["receive_id"] == "oc_xxx"
    assert requests[1]["json"]["msg_type"] == "interactive"


@pytest.mark.asyncio
async def test_send_api_card_reuses_cached_tenant_token(monkeypatch):
    feishu_pusher._TOKEN_CACHE.clear()
    requests = []

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            requests.append(url)
            if url.endswith("/tenant_access_token/internal"):
                return FakeResponse({"tenant_access_token": "tenant-token", "expire": 7200})
            return FakeResponse({"code": 0})

    monkeypatch.setattr(feishu_pusher.httpx, "AsyncClient", FakeClient)

    for _ in range(2):
        assert await feishu_pusher.send_api_card(
            app_id="cli_cache",
            app_secret="secret",
            chat_id="oc_xxx",
            title="标题",
            content="正文",
        ) is True

    token_requests = [url for url in requests if url.endswith("/tenant_access_token/internal")]
    message_requests = [url for url in requests if url.endswith("/im/v1/messages")]
    assert len(token_requests) == 1
    assert len(message_requests) == 2


@pytest.mark.asyncio
async def test_send_feishu_card_sync_inside_running_loop(monkeypatch):
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return {
            "feishu_api": False,
            "feishu_webhook": True,
            "channel": "webhook",
            "error": "",
        }

    monkeypatch.setattr(feishu_pusher, "send_feishu_card", fake_send)

    result = feishu_pusher.send_feishu_card_sync(
        title="盘中告警",
        content="风险触发",
        webhook_url="https://example.test/webhook",
    )

    assert result["feishu_webhook"] is True
    assert result["channel"] == "webhook"
    assert calls[0]["title"] == "盘中告警"
