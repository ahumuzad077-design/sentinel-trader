import base64
import importlib
import os
import sys
import types
import unittest


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


# Keep these route tests independent of broker SDK availability and never
# construct an actual trading client.
bot_stub = types.ModuleType("bot")
bot_stub.TraderEngine = DummyEngine
bot_stub.ChatEngine = DummyChat
sys.modules["bot"] = bot_stub
web = importlib.import_module("web")


class DashboardAuthTests(unittest.TestCase):
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

    def auth_headers(self, username="operator", password="long-test-password"):
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def test_health_is_minimal_and_available_without_credentials(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})

    def test_dashboard_security_headers_and_favicon(self):
        os.environ["DASHBOARD_USERNAME"] = "operator"
        os.environ["DASHBOARD_PASSWORD"] = "long-test-password"
        headers = self.auth_headers()
        response = self.client.get("/", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        icon = self.client.get("/favicon.ico", headers=headers)
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon.mimetype, "image/svg+xml")
        self.assertIn(b"<svg", icon.data)
        icon.close()

    def test_dashboard_and_state_fail_closed_without_config(self):
        for method, path in (("get", "/"), ("get", "/api/state"),
                             ("post", "/api/start"),
                             ("post", "/api/chat")):
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertIn("WWW-Authenticate", response.headers)
        self.assertIsNone(web._engine)

    def test_wrong_credentials_are_rejected(self):
        os.environ["DASHBOARD_USERNAME"] = "operator"
        os.environ["DASHBOARD_PASSWORD"] = "long-test-password"
        response = self.client.get("/api/state", headers=self.auth_headers(
            password="wrong-password"))
        self.assertEqual(response.status_code, 401)

    def test_correct_credentials_can_read_and_control(self):
        os.environ["DASHBOARD_USERNAME"] = "operator"
        os.environ["DASHBOARD_PASSWORD"] = "long-test-password"
        headers = self.auth_headers()
        self.assertEqual(self.client.get("/api/state", headers=headers).status_code,
                         200)
        headers["Origin"] = "https://localhost"
        response = self.client.post("/api/start", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(web._engine.started)

    def test_live_start_requires_exact_server_side_acknowledgement(self):
        os.environ["DASHBOARD_USERNAME"] = "operator"
        os.environ["DASHBOARD_PASSWORD"] = "long-test-password"
        old_engine_class = web.TraderEngine
        web.TraderEngine = DummyLiveEngine
        headers = self.auth_headers()
        headers["Origin"] = "https://localhost"
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

    def test_cross_origin_control_request_is_rejected(self):
        os.environ["DASHBOARD_USERNAME"] = "operator"
        os.environ["DASHBOARD_PASSWORD"] = "long-test-password"
        headers = self.auth_headers()
        headers["Origin"] = "https://attacker.example"
        response = self.client.post("/api/start", headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(web._engine)

    def test_plain_http_is_rejected_even_with_valid_credentials(self):
        os.environ["DASHBOARD_USERNAME"] = "operator"
        os.environ["DASHBOARD_PASSWORD"] = "long-test-password"
        response = self.client.get("/api/state", headers=self.auth_headers(),
                                   base_url="http://localhost",
                                   environ_overrides={"HTTP_X_FORWARDED_PROTO": "http"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "HTTPS is required.")


if __name__ == "__main__":
    unittest.main()
