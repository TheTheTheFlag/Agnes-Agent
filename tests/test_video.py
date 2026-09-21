"""回归测试：视频创作工作台后端（app.server.api.video）。

覆盖：
  - 路由注册（前端约定的 /api/video/* 路径与方法）
  - 请求体构造：flash text/keyframe/reference 与 v2.0 ti2vid/img2vid/keyframes
  - 分辨率档换算 _dimensions
  - 视频接口「按 key 限流（429）」的创建轮换逻辑
  - generate 参数校验 + task 不存在时的 404
  - delete 移除条目
"""
import asyncio
import json
import types
import unittest
from unittest import mock

from app.server.api import video as vid


class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if not isinstance(body, str) else body

    def json(self):
        return self._body


def _req(base="http://god.makeup:8081/", username="tester"):
    return types.SimpleNamespace(base_url=base, state=types.SimpleNamespace(username=username))


class VideoRoutesTest(unittest.TestCase):
    def _route_methods(self):
        out = {}
        for r in vid.router.routes:
            out.setdefault(getattr(r, "path", ""), set()).update(getattr(r, "methods", set()) or set())
        return out

    def test_routes_registered(self):
        routes = self._route_methods()
        for path in ("/api/video/status", "/api/video/generate", "/api/video/task/{item_id}",
                     "/api/video/history", "/api/video/file/{item_id}", "/api/video/delete"):
            self.assertIn(path, routes, path)
        self.assertIn("POST", routes["/api/video/generate"])
        self.assertIn("POST", routes["/api/video/delete"])

    def test_status_shape(self):
        with mock.patch.object(vid, "_agnes_keys", return_value=["sk-test1234567890"]):
            resp = vid.status(_req())
        self.assertTrue(resp["configured"])
        self.assertEqual(resp["key_count"], 1)
        ids = [m["id"] for m in resp["models"]]
        self.assertIn("agnes-video-2.5-flash", ids)
        self.assertIn("agnes-video-v2.0", ids)
        self.assertTrue(resp["public_ok"])


class VideoDimensionsTest(unittest.TestCase):
    def test_landscape_and_portrait(self):
        self.assertEqual(vid._dimensions("16:9", "720p"), (1280, 720))
        self.assertEqual(vid._dimensions("9:16", "720p"), (720, 1280))
        self.assertEqual(vid._dimensions("1:1", "1080p"), (1080, 1080))
        self.assertEqual(vid._dimensions("16:9", "480p"), (853, 480))

    def test_unknown_falls_back(self):
        w, h = vid._dimensions("bogus", "bogus")
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)


class VideoPayloadTest(unittest.TestCase):
    def test_flash_text(self):
        out = vid._build_payload("agnes-video-2.5-flash", "text",
                                 {"prompt": "hi", "seconds": "4", "aspect_ratio": "16:9"}, _req())
        self.assertEqual(out["model"], "agnes-video-2.5-flash")
        self.assertEqual(out["mode"], "text")
        self.assertEqual(out["size"], "720P")
        self.assertEqual(out["seconds"], "4")
        self.assertNotIn("images", out)
        self.assertNotIn("first_frame", out)

    def test_flash_refs_inline_data_uri(self):
        with mock.patch.object(vid, "_to_data_uri", side_effect=lambda r: "data:image/png;base64," + r):
            kf = vid._build_payload("agnes-video-2.5-flash", "keyframe",
                                    {"prompt": "x", "refs": ["uploads/a.png", "uploads/b.png"]}, _req())
            rf = vid._build_payload("agnes-video-2.5-flash", "reference",
                                    {"prompt": "x", "refs": ["uploads/a.png"]}, _req("http://localhost:8081/"))
        self.assertEqual(kf["first_frame"], "data:image/png;base64,uploads/a.png")
        self.assertEqual(kf["last_frame"], "data:image/png;base64,uploads/b.png")
        self.assertEqual(len(rf["images"]), 1)
        self.assertNotIn("first_frame", rf)

    def test_flash_refs_public_url_fallback(self):
        with mock.patch.object(vid, "_to_public_url", side_effect=lambda r, b: b + "/pub/video-ref/" + r.split("/")[-1]):
            kf = vid._build_payload("agnes-video-2.5-flash", "keyframe",
                                    {"prompt": "x", "refs": ["uploads/a.png"]}, _req(), use_public=True)
        self.assertTrue(kf["first_frame"].endswith("/pub/video-ref/a.png"))

    def test_v20_img2vid_uses_data_uri(self):
        with mock.patch.object(vid, "_to_data_uri", return_value="data:image/png;base64,AAA"):
            out = vid._build_payload("agnes-video-v2.0", "img2vid",
                                     {"prompt": "x", "refs": ["uploads/a.png"], "num_frames": 81, "frame_rate": 24}, _req())
        self.assertEqual(out["image"], "data:image/png;base64,AAA")
        self.assertEqual(out["mode"], "ti2vid")
        self.assertEqual(out["num_frames"], 81)

    def test_v20_keyframes_extra_body(self):
        with mock.patch.object(vid, "_to_data_uri", return_value="data:image/png;base64,AAA"):
            out = vid._build_payload("agnes-video-v2.0", "keyframes",
                                     {"prompt": "x", "refs": ["uploads/a.png", "uploads/b.png"]}, _req())
        self.assertEqual(out["extra_body"]["mode"], "keyframes")
        self.assertEqual(len(out["extra_body"]["image"]), 2)

    def test_v20_ti2vid_gets_dimensions(self):
        out = vid._build_payload("agnes-video-v2.0", "ti2vid",
                                 {"prompt": "x", "aspect_ratio": "16:9", "resolution": "720p"}, _req())
        self.assertEqual((out["width"], out["height"]), (1280, 720))
        self.assertEqual(out["mode"], "ti2vid")


class VideoKeyRotationTest(unittest.TestCase):
    def test_rotation_on_429(self):
        calls = []

        def fake_post(key, payload):
            calls.append(key)
            if key == "bad":
                return _FakeResp(429, {"error": "rate_limit_exceeded"})
            return _FakeResp(200, {"video_id": "vid_1", "status": "queued", "task_id": "t1"})

        with mock.patch.object(vid, "_post_create", side_effect=fake_post):
            idx, data = vid._create_task(["bad", "good"], {"prompt": "x"})
        self.assertEqual(calls, ["bad", "good"])
        self.assertEqual(idx, 1)
        self.assertEqual(data["video_id"], "vid_1")

    def test_4xx_raises_without_rotation(self):
        calls = []

        def fake_post(key, payload):
            calls.append(key)
            return _FakeResp(400, {"detail": "size must be 720P"})

        with mock.patch.object(vid, "_post_create", side_effect=fake_post):
            with self.assertRaises(RuntimeError):
                vid._create_task(["k1", "k2"], {"prompt": "x"})
        self.assertEqual(calls, ["k1"])

    def test_all_429_raises(self):
        with mock.patch.object(vid, "_post_create", return_value=_FakeResp(429, {"error": "x"})):
            with self.assertRaises(RuntimeError):
                vid._create_task(["a", "b"], {"prompt": "x"})


class VideoGenerateValidationTest(unittest.TestCase):
    def test_bad_model(self):
        resp = vid.generate({"model": "nope", "prompt": "x"}, _req())
        self.assertEqual(resp.status_code, 400)

    def test_empty_prompt(self):
        resp = vid.generate({"model": "agnes-video-2.5-flash", "mode": "text", "prompt": "  "}, _req())
        self.assertEqual(resp.status_code, 400)

    def test_missing_refs(self):
        with mock.patch.object(vid, "_agnes_keys", return_value=["sk-x"]):
            resp = vid.generate({"model": "agnes-video-v2.0", "mode": "img2vid", "prompt": "x"}, _req())
        self.assertEqual(resp.status_code, 400)

    def test_no_key(self):
        with mock.patch.object(vid, "_agnes_keys", return_value=[]):
            resp = vid.generate({"model": "agnes-video-2.5-flash", "mode": "text", "prompt": "x"}, _req())
        self.assertEqual(resp.status_code, 503)

    def test_success_persists_item(self):
        created = {"video_id": "vid_9", "task_id": "task_9", "status": "queued", "progress": 0,
                   "seconds": "4", "size": "720P"}
        user = "tester"
        with mock.patch.object(vid, "_agnes_keys", return_value=["sk-good"]), \
             mock.patch.object(vid, "_create_task", return_value=(0, created)), \
             mock.patch.object(vid, "_save_user_manifest"), \
             mock.patch.object(vid, "_prune_refs"):
            with vid._user_items_lock(user):
                vid._user_items[user] = []
            try:
                resp = vid.generate({"model": "agnes-video-2.5-flash", "mode": "text",
                                     "prompt": "hello", "seconds": "4"}, _req())
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["items"][0]["video_id"], "vid_9")
                self.assertEqual(resp["items"][0]["status"], "queued")
                self.assertEqual(vid._user_items[user][0]["video_id"], "vid_9")
            finally:
                with vid._user_items_lock(user):
                    vid._user_items.pop(user, None)
                    vid._user_locks.pop(user, None)

    def test_generate_falls_back_to_public_url(self):
        created = {"video_id": "vid_9", "task_id": "task_9", "status": "queued", "seconds": "4", "size": "720P"}
        err = RuntimeError('上游 HTTP 400: {"code":"invalid_request","message":"载体必须是公开 http(s) URL 或合法 Base64"}')
        calls = []

        def fake_create(keys, payload):
            calls.append(dict(payload))
            if len(calls) == 1:
                raise err
            return 0, created

        user = "tester"
        with mock.patch.object(vid, "_agnes_keys", return_value=["sk-good"]), \
             mock.patch.object(vid, "_create_task", side_effect=fake_create), \
             mock.patch.object(vid, "_to_data_uri", return_value="data:image/png;base64,AAAA"), \
             mock.patch.object(vid, "_to_public_url", return_value="http://god.makeup:8081/pub/video-ref/x.png"), \
             mock.patch.object(vid, "_save_user_manifest"), mock.patch.object(vid, "_prune_refs"):
            with vid._user_items_lock(user):
                vid._user_items[user] = []
            try:
                resp = vid.generate({"model": "agnes-video-2.5-flash", "mode": "reference",
                                     "prompt": "x", "refs": ["uploads/a.png"]}, _req())
                self.assertTrue(resp["ok"])
                self.assertEqual(len(calls), 2)
                self.assertTrue(calls[1]["images"][0].startswith("http://"))
            finally:
                with vid._user_items_lock(user):
                    vid._user_items.pop(user, None)
                    vid._user_locks.pop(user, None)

    def test_generate_carrier_error_local_base_hint(self):
        err = RuntimeError('上游 HTTP 400: {"message":"载体必须是公开 http(s) URL 或合法 Base64"}')
        with mock.patch.object(vid, "_agnes_keys", return_value=["sk-good"]), \
             mock.patch.object(vid, "_create_task", side_effect=err), \
             mock.patch.object(vid, "_to_data_uri", return_value="data:image/png;base64,AAAA"), \
             mock.patch.object(vid, "_prune_refs"):
            resp = vid.generate({"model": "agnes-video-2.5-flash", "mode": "reference",
                                 "prompt": "x", "refs": ["uploads/a.png"]}, _req("http://localhost:8081/"))
        self.assertEqual(resp.status_code, 502)

    def test_task_missing(self):
        resp = vid.task("doesnotexist01")
        self.assertEqual(resp.status_code, 404)

    def test_task_bad_id(self):
        resp = vid.task("bad/id!")
        self.assertEqual(resp.status_code, 400)


class VideoDeleteTest(unittest.TestCase):
    def test_delete_removes_items(self):
        user = "tester"
        with mock.patch.object(vid, "_save_user_manifest"):
            with vid._user_items_lock(user):
                vid._user_items[user] = [{"id": "a"}, {"id": "b"}]
            try:
                resp = vid.delete({"ids": ["a"]}, _req())
                self.assertEqual(resp, {"ok": True, "deleted": 1})
                self.assertEqual([it["id"] for it in vid._user_items[user]], ["b"])
            finally:
                with vid._user_items_lock(user):
                    vid._user_items.pop(user, None)
                    vid._user_locks.pop(user, None)

    def test_delete_requires_ids(self):
        resp = vid.delete({}, _req())
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
