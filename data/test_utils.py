from unittest.mock import patch, MagicMock
import requests

def call_api(url):
    """模拟一个调用外部 API 的函数"""
    resp = requests.get(url)
    return resp.json()

# mock: 模拟 requests.get 的返回值
def test_call_api_mock():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "ok", "count": 42}

    with patch("test_utils.requests.get", return_value=mock_resp):
        result = call_api("https://example.com/api")
        assert result["count"] == 42
        assert result["status"] == "ok"
