"""Single-user loopback API. Uploaded bytes are not written to disk."""

import asyncio
import hmac
import re
import secrets
import threading
import time
from email import policy
from email.parser import BytesParser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import __version__
from .analysis import MAX_COLUMNS, MAX_FILE_BYTES, MAX_ROWS, TableError, analyze, example_csv

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = MAX_FILE_BYTES + 65536
HOST = re.compile(r"(127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?\Z", re.I)
TOKEN = secrets.token_urlsafe(32)
ANALYSIS_SLOTS = threading.BoundedSemaphore(2)
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class LocalBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        raw = scope.get("headers", [])
        values = {}
        for key, value in raw:
            values.setdefault(key.lower(), []).append(value.decode("latin-1"))

        async def reject(status, message):
            await JSONResponse({"detail": message}, status_code=status, headers=SECURITY_HEADERS)(
                scope, receive, send
            )

        if any(
            len(values.get(key, [])) > 1
            for key in (
                b"host",
                b"origin",
                b"content-length",
                b"transfer-encoding",
                b"x-table-notes-csrf",
            )
        ):
            return await reject(400, "Duplicate request headers are not supported.")
        host = values.get(b"host", [""])[0]
        match = HOST.fullmatch(host)
        if not match or match[2] and not 1 <= int(match[2]) <= 65535:
            return await reject(403, "Open the local Table Notes address directly.")
        origin = values.get(b"origin", [None])[0]
        expected = scope.get("scheme", "http") + "://" + host
        if origin is not None and origin.lower() != expected.lower():
            return await reject(403, "Cross-origin requests are not accepted.")
        if values.get(b"sec-fetch-site", [""])[0] == "cross-site":
            return await reject(403, "Cross-site requests are not accepted.")
        if scope["method"] not in ("GET", "HEAD", "POST"):
            return await reject(405, "This request method is not supported.")
        if scope["method"] == "POST":
            token = values.get(b"x-table-notes-csrf", [""])[0]
            if not hmac.compare_digest(token.encode("latin-1"), TOKEN.encode("ascii")):
                return await reject(403, "Refresh this page before analyzing a file.")
            length = values.get(b"content-length", [""])[0]
            if (
                b"transfer-encoding" in values
                or len(length) > 8
                or not length.isascii()
                or not length.isdigit()
                or values.get(b"content-encoding")
            ):
                return await reject(400, "Use a normal file upload with a known length.")
            if int(length) > MAX_BODY:
                return await reject(413, "The upload exceeds the 5 MiB file limit.")
            deadline = time.monotonic() + 30
            body = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return await reject(408, "The upload took too long. Try a smaller file.")
                try:
                    event = await asyncio.wait_for(receive(), timeout=remaining)
                except asyncio.TimeoutError:
                    return await reject(408, "The upload took too long. Try a smaller file.")
                if event["type"] == "http.disconnect":
                    return
                body.extend(event.get("body", b""))
                if len(body) > MAX_BODY or len(body) > int(length):
                    return await reject(413, "The upload is larger than declared.")
                if not event.get("more_body", False):
                    break
            if len(body) != int(length):
                return await reject(400, "The upload ended before its declared length.")
            consumed = False
            upstream_receive = receive

            async def bounded_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await upstream_receive()

            receive = bounded_receive

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in headers}
                headers.extend(
                    (key.lower().encode(), value.encode())
                    for key, value in SECURITY_HEADERS.items()
                    if key.lower().encode() not in existing
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, guarded_send)


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(LocalBoundary)


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/assets/{name}")
def asset(name: str):
    if name not in {"app.js", "styles.css", "mark.svg"} or not (STATIC / name).is_file():
        raise HTTPException(404, "Asset not found.")
    return FileResponse(STATIC / name)


@app.get("/api/config")
def config():
    return {
        "csrf_token": TOKEN,
        "max_file_bytes": MAX_FILE_BYTES,
        "max_rows": MAX_ROWS,
        "max_columns": MAX_COLUMNS,
        "version": __version__,
    }


@app.get("/api/example")
def example():
    return analyze(example_csv(), "weekly-orders.csv", is_example=True)


@app.get("/examples/weekly-orders.csv")
def sample():
    return Response(
        example_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="weekly-orders.csv"'},
    )


def unpack_file(body, content_type):
    if "\r" in content_type or "\n" in content_type or len(content_type) > 200:
        raise TableError("The upload content type is invalid.")
    message = BytesParser(policy=policy.default).parsebytes(
        ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode("latin-1") + body
    )
    if (
        message.get_content_type() != "multipart/form-data"
        or not message.is_multipart()
        or message.defects
    ):
        raise TableError("Choose a file using the upload control.")
    parts = list(message.iter_parts())
    if len(parts) != 1:
        raise TableError("Upload one file at a time.")
    part = parts[0]
    if (
        part.is_multipart()
        or part.defects
        or part.get_content_disposition() != "form-data"
        or part.get_param("name", header="content-disposition") != "file"
    ):
        raise TableError("The upload must contain one file field.")
    if part.get("Content-Transfer-Encoding", "binary").lower() not in ("binary", "8bit"):
        raise TableError("Encoded multipart files are not supported.")
    filename = part.get_filename()
    data = part.get_payload(decode=True)
    if not isinstance(filename, str) or not isinstance(data, bytes) or len(filename) > 240:
        raise TableError("Choose a CSV or XLSX file with a short filename.")
    return data, re.split(r"[/\\]", filename)[-1]


@app.post("/api/analyze")
async def analyze_file(request: Request):
    if not ANALYSIS_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Two tables are already being analyzed. Try again shortly.")
    try:
        data, filename = unpack_file(await request.body(), request.headers.get("content-type", ""))
        worker = asyncio.get_running_loop().run_in_executor(None, analyze, data, filename)
    except TableError as error:
        ANALYSIS_SLOTS.release()
        raise HTTPException(422, str(error)) from None
    except BaseException:
        ANALYSIS_SLOTS.release()
        raise
    worker.add_done_callback(_finish_analysis)
    try:
        # A canceled request must not free a slot while its parser is still running.
        return await asyncio.shield(worker)
    except TableError as error:
        raise HTTPException(422, str(error)) from None


def _finish_analysis(worker):
    ANALYSIS_SLOTS.release()
    if not worker.cancelled():
        # Retrieve errors even if the client canceled its waiting request.
        worker.exception()
