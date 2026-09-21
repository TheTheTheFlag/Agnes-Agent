"""回归测试：图片工作台作品库删除路由。

背景（bug）：前端 `image-studio.js` 用 `apiPost("/api/image/delete", {ids})` 删除作品，
而后端曾把删除端点注册成 `@router.delete("")`（实为 DELETE /api/image），
路径与方法都不匹配 → 点「删除」报 404。本测试锁定前端约定的 POST /api/image/delete。
"""
import asyncio
import types
import unittest
from unittest import mock

from app.server.api import image as img


def _req(username="tester"):
    return types.SimpleNamespace(state=types.SimpleNamespace(username=username))


class ImageDeleteRouteTest(unittest.TestCase):
    def _route_methods(self):
        out = {}
        for r in img.router.routes:
            out.setdefault(getattr(r, "path", ""), set()).update(getattr(r, "methods", set()) or set())
        return out

    def test_delete_route_matches_frontend_post(self):
        routes = self._route_methods()
        self.assertIn("/api/image/delete", routes)
        self.assertIn("POST", routes["/api/image/delete"])

    def test_other_routes_intact(self):
        paths = set(self._route_methods())
        self.assertIn("/api/image/history", paths)
        self.assertIn("/api/image/file/{item_id}", paths)
        self.assertIn("/api/image/generate", paths)

    def test_delete_removes_items(self):
        user = "tester"
        with mock.patch.object(img, "_save_user_manifest"):
            with img._user_items_lock(user):
                img._user_items[user] = [{"id": "a"}, {"id": "b"}]
            try:
                resp = asyncio.run(img.delete({"ids": ["a"]}, _req(user)))
                self.assertEqual(resp, {"ok": True, "deleted": 1})
                self.assertEqual([it["id"] for it in img._user_items[user]], ["b"])
            finally:
                with img._user_items_lock(user):
                    img._user_items.pop(user, None)
                    img._user_locks.pop(user, None)

    def test_delete_requires_ids(self):
        resp = asyncio.run(img.delete({}, _req()))
        self.assertEqual(getattr(resp, "status_code", None), 400)


if __name__ == "__main__":
    unittest.main()
