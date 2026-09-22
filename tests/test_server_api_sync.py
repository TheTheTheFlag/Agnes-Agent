"""server API 端点不得在事件循环里做阻塞网络调用（会把整个 uvicorn 冻死）。

历史事故：image.py 的 async def generate 内直接 requests.post(timeout=360)，
上游一旦缓慢，整个单进程事件循环卡死最长 360s，期间所有请求（含 /api/upload
上传参考图）全部超时失败。
"""
import asyncio
from unittest.mock import patch

from app.server.api.image import _call_generate, generate as image_generate, _UPSTREAM
from app.server.api.video import task as video_task
from app.server.config import fetch_models


def test_image_generate_is_sync():
    assert not asyncio.iscoroutinefunction(image_generate)


def test_video_task_is_sync():
    assert not asyncio.iscoroutinefunction(video_task)


def test_fetch_models_is_sync():
    assert not asyncio.iscoroutinefunction(fetch_models)


def test_call_generate_wraps_network_error_as_runtime():
    from requests.exceptions import ConnectionError
    with patch("requests.post", side_effect=ConnectionError("boom")):
        try:
            _call_generate("sk-test", {"model": "agnes-image-2.5-flash"})
            raise AssertionError("应抛出 RuntimeError")
        except RuntimeError as e:
            assert "上游连接失败" in str(e)


def test_call_generate_wraps_timeout_as_runtime():
    from requests.exceptions import ReadTimeout
    with patch("requests.post", side_effect=ReadTimeout("slow")):
        try:
            _call_generate("sk-test", {})
            raise AssertionError("应抛出 RuntimeError")
        except RuntimeError as e:
            assert "上游连接失败" in str(e)


def test_call_generate_surfaces_upstream_http_error():
    class FakeResp:
        status_code = 400
        text = "invalid ref image"

        def json(self):
            return {}

    with patch("requests.post", return_value=FakeResp()):
        try:
            _call_generate("sk-test", {})
            raise AssertionError("应抛出 RuntimeError")
        except RuntimeError as e:
            assert "上游 HTTP 400" in str(e)
            assert "invalid ref image" in str(e)


def test_call_generate_surfaces_bad_json():
    class FakeResp:
        status_code = 200
        text = "not-json"

        def json(self):
            raise ValueError("no json")

    with patch("requests.post", return_value=FakeResp()):
        try:
            _call_generate("sk-test", {})
            raise AssertionError("应抛出 RuntimeError")
        except RuntimeError as e:
            assert "上游响应解析失败" in str(e)


def test_call_generate_empty_data():
    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

    with patch("requests.post", return_value=FakeResp()):
        try:
            _call_generate("sk-test", {})
            raise AssertionError("应抛出 RuntimeError")
        except RuntimeError as e:
            assert "上游返回空结果" in str(e)