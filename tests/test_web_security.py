import importlib
import os
import sys
import types
import unittest
from unittest.mock import patch


class DummyEngine:
    def __init__(self, **kwargs):
        self.started = False
        self.cfg = {"paper": True}

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def is_running(self):
        return self.started


class DummyLiveEngine(DummyEngine):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cfg["paper"] = False


class DummyChat:
    def __init__(self, engine):
        self.engine = engine

    def handle(self, message):
        return {"reply": "ok", "type": "text"}


# These route tests never construct a real trading client or call a broker.
bot_stub = types.ModuleType("bot")
bot_stub.TraderEngine = DummyEngine
bot_stub.ChatEngine = DummyChat
sys.modules["bot"] = bot_stub
web = importlib.import_module("web")


class PublicDashboardTests(unittest.TestCase):
    def setUp(self):
        self.old_user = os.environ.pop("DASHBOARD_USERNAME", None)
        self.old_password = os.environ.pop("DASHBOARD_PASSWORD", None)
        web._engine = None
        web._chat = None
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.client.environ_base["wsgi.url_scheme"] = "https"
        self.client.environ_base["HTTP_X_FORWARDED_PROTO"] = "https"

    def tearDown(self):
        os.environ.pop("DASHBOARD_USERNAME", None)
        os.environ.pop("DASHBOARD_PASSWORD", None)
        if self.old_user is not None:
            os.environ["DASHBOARD_USERNAME"] = self.old_user
        if self.old_password is not None:
            os.environ["DASHBOARD_PASSWORD"] = self.old_password

    def same_origin_headers(self):
        return {"Origin": "https://localhost"}

    def test_health_is_public_and_minimal(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})

    def test_dashboard_and_account_state_are_public_without_auth(self):
        os.environ["DASHBOARD_USERNAME"] = "legacy-user"
        os.environ["DASHBOARD_PASSWORD"] = "legacy-password"
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("WWW-Authenticate", page.headers)

        state = self.client.get("/api/state")
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.get_json()["mode"], "PAPER")
        self.assertNotIn("WWW-Authenticate", state.headers)
        self.assertIsNone(web._engine)

    def test_public_auto_start_requires_separate_opt_in(self):
        with patch.dict(os.environ, {
                "AUTO_START": "true", "ALLOW_PUBLIC_AUTO_START": "false"}):
            self.assertFalse(web._public_auto_start_enabled())
        with patch.dict(os.environ, {
                "AUTO_START": "true", "ALLOW_PUBLIC_AUTO_START": "true"}):
            self.assertTrue(web._public_auto_start_enabled())

    def test_security_headers_and_favicon_are_public(self):
        page = self.client.get("/")
        self.assertIn("default-src 'self'", page.headers["Content-Security-Policy"])
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")

        icon = self.client.get("/favicon.ico")
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon.mimetype, "image/svg+xml")
        self.assertIn(b"<svg", icon.data)
        icon.close()

    def test_public_chat_route_is_available_without_auth(self):
        response = self.client.post("/api/chat", json={"message": "account"},
                                   headers=self.same_origin_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reply"], "ok")

    def test_public_start_requires_same_origin_but_no_password(self):
        response = self.client.post("/api/start", headers=self.same_origin_headers())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(web._engine.started)

    def test_cross_origin_control_request_is_rejected_without_auth(self):
        headers = {"Origin": "https://attacker.example"}
        response = self.client.post("/api/start", headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(web._engine)

    def test_live_start_requires_exact_acknowledgement_without_auth(self):
        old_engine_class = web.TraderEngine
        web.TraderEngine = DummyLiveEngine
        headers = self.same_origin_headers()
        try:
            response = self.client.post("/api/start", headers=headers)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(web._engine.started)
            response = self.client.post("/api/start", headers=headers,
                                        json={"confirm_live": "START LIVE"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(web._engine.started)
        finally:
            web.TraderEngine = old_engine_class

    def test_plain_http_is_rejected(self):
        response = self.client.get(
            "/api/state", base_url="http://localhost",
            environ_overrides={"HTTP_X_FORWARDED_PROTO": "http"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "HTTPS is required.")
        self.assertNotIn("WWW-Authenticate", response.headers)


if __name__ == "__main__":
    unittest.main()
