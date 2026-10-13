from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from celltrack.web.access import COOKIE_NAME, TOKEN_ENV, cookie_value
from celltrack.web.app import app


TOKEN = "s3cret-token"


class AccessTokenTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {TOKEN_ENV: TOKEN})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app, base_url="https://testserver")

    def test_requests_without_a_token_are_refused(self):
        for path in ("/", "/api/health", "/api/datasets", "/static/js/app.js"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403)

    def test_a_wrong_token_or_cookie_is_refused(self):
        self.assertEqual(self.client.get("/?token=wrong").status_code, 403)
        self.client.cookies.set(COOKIE_NAME, cookie_value("wrong"))
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_the_right_token_sets_a_cookie_and_drops_itself_from_the_url(self):
        response = self.client.get(
            "/analysis/tasks/abc?token=s3cret-token&tab=2", follow_redirects=False
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/analysis/tasks/abc?tab=2")
        cookie = response.headers["set-cookie"]
        self.assertIn(f"{COOKIE_NAME}={cookie_value(TOKEN)}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertNotIn(TOKEN, cookie)

        page = self.client.get("/analysis/tasks/abc?tab=2")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_redirect_cannot_point_at_another_host(self):
        response = self.client.get("//evil.example/?token=s3cret-token", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/"))
        self.assertFalse(response.headers["location"].startswith("//"))

    def test_the_gate_is_off_without_the_environment_variable(self):
        with mock.patch.dict(os.environ, {TOKEN_ENV: ""}):
            self.assertEqual(TestClient(app).get("/api/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
