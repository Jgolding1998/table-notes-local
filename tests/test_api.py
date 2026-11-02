import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from table_notes.app import ANALYSIS_SLOTS, TOKEN, LocalBoundary, analyze_file, app


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
            with patch("table_notes.app.unpack_file", side_effect=AssertionError("Busy parser ran")):
                self.assertEqual(self.upload().status_code, 429)
        finally:
            ANALYSIS_SLOTS.release()
            ANALYSIS_SLOTS.release()
        self.assertEqual(self.upload().status_code, 200)

    def test_invalid_upload_releases_processing_slot(self):
        for _ in range(3):
            self.assertEqual(self.upload(b"duplicate,duplicate\n1,2\n").status_code, 422)
        self.assertEqual(self.upload().status_code, 200)

    def test_extreme_exponent_returns_safe_input_error(self):
        response = self.upload(b"amount\n1e-" + b"9" * 200 + b"\n")
        self.assertEqual(response.status_code, 422)
        self.assertIn("exponent", response.json()["detail"])
        self.assertNotIn("InvalidOperation", response.text)

    def test_canceled_request_keeps_slot_until_worker_finishes(self):
        started, finish = threading.Event(), threading.Event()

        def blocking_analysis(*args):
            started.set()
            if not finish.wait(5):
                raise AssertionError("Test did not release parser")
            return {"ok": True}

        async def scenario():
            async def body():
                return b"synthetic upload"

            request = SimpleNamespace(body=body, headers={})
            ANALYSIS_SLOTS.acquire()
            task = None
            try:
                with (
                    patch("table_notes.app.unpack_file", return_value=(b"a\n1", "sample.csv")),
                    patch("table_notes.app.analyze", blocking_analysis),
                ):
                    task = asyncio.create_task(analyze_file(request))
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    acquired = ANALYSIS_SLOTS.acquire(blocking=False)
                    if acquired:
                        ANALYSIS_SLOTS.release()
                    self.assertFalse(acquired, "Canceled request freed its running worker's slot")
                    finish.set()
                    self.assertTrue(await asyncio.to_thread(ANALYSIS_SLOTS.acquire, True, 5))
                    ANALYSIS_SLOTS.release()
            finally:
                finish.set()
                ANALYSIS_SLOTS.release()
                if task is not None and not task.done():
                    task.cancel()

        asyncio.run(scenario())

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
