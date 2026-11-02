import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from table_notes.app import ANALYSIS_SLOTS, TOKEN, LocalBoundary, app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://127.0.0.1:8127")
        self.token = self.client.get("/api/config").json()["csrf_token"]

    def upload(self, content=b"label,amount\nSample A,2\n", **kwargs):
        headers = {"X-Table-Notes-CSRF": self.token}
        headers.update(kwargs.pop("headers", {}))
        return self.client.post(
            "/api/analyze", files={"file": ("sample.csv", content)}, headers=headers, **kwargs
        )

    def test_upload_sample_and_security_headers(self):
        response = self.upload(headers={"origin": "http://127.0.0.1:8127"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["shape"]["rows"], 1)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertTrue(self.client.get("/api/example").json()["is_example"])
        self.assertIn("Sample A", self.client.get("/examples/weekly-orders.csv").text)

    def test_host_origin_token_and_methods(self):
        for headers in (
            {"host": "attacker.example"},
            {"origin": "https://attacker.example"},
            {"origin": "null"},
            {"sec-fetch-site": "cross-site"},
            {"X-Table-Notes-CSRF": "wrong"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.upload(headers=headers).status_code, 403)
        self.assertEqual(
            self.client.post("/api/analyze", files={"file": ("x.csv", b"a\n1")}).status_code, 403
        )
        self.assertEqual(self.client.put("/api/analyze").status_code, 405)

    def test_duplicate_headers_and_oversized_framing(self):
        response = self.client.get(
            "/api/config", headers=[("host", "127.0.0.1:8127"), ("host", "attacker.example")]
        )
        self.assertEqual(response.status_code, 400)
        response = self.upload(headers={"Content-Length": "9999999"})
        self.assertEqual(response.status_code, 413)
        response = self.upload(headers={"Content-Length": "9" * 100})
        self.assertEqual(response.status_code, 400)

    def test_safe_errors_multiple_parts_and_no_arbitrary_files(self):
        secret_marker = b"PRIVATE_TEST_MARKER"
        response = self.upload(b"bad,bad\n" + secret_marker)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret_marker.decode(), response.text)
        response = self.client.post(
            "/api/analyze",
            files=[("file", ("a.csv", b"a\n1")), ("file", ("b.csv", b"b\n2"))],
            headers={"X-Table-Notes-CSRF": self.token},
        )
        self.assertEqual(response.status_code, 422)
        for path in (
            "/README.md",
            "/.git/config",
            "/assets/analysis.py",
            "/assets/..%2Fapp.py",
            "/docs",
        ):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_processing_admission_rejects_without_leaking_slots(self):
        ANALYSIS_SLOTS.acquire()
        ANALYSIS_SLOTS.acquire()
        try:
            self.assertEqual(self.upload().status_code, 429)
        finally:
            ANALYSIS_SLOTS.release()
            ANALYSIS_SLOTS.release()
        self.assertEqual(self.upload().status_code, 200)

    def test_absolute_upload_deadline_rejects_available_late_chunk(self):
        messages, invoked = [], []
        events = iter(
            [
                {"type": "http.request", "body": b"a", "more_body": True},
                {"type": "http.request", "body": b"b", "more_body": False},
            ]
        )
        clock = iter([0, 0, 31])

        async def receive():
            return next(events)

        async def send(message):
            messages.append(message)

        async def inner(scope, receive, send):
            invoked.append(True)

        scope = {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "headers": [
                (b"host", b"127.0.0.1"),
                (b"content-length", b"2"),
                (b"x-table-notes-csrf", TOKEN.encode()),
            ],
        }
        with patch("table_notes.app.time", SimpleNamespace(monotonic=lambda: next(clock))):
            asyncio.run(LocalBoundary(inner)(scope, receive, send))
        self.assertEqual(messages[0]["status"], 408)
        self.assertEqual(invoked, [])


if __name__ == "__main__":
    unittest.main()
