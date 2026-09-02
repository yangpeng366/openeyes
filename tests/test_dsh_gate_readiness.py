"""Tests for the dsh web gate readiness probe."""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "dsh-gate-readiness.py"


def _run_server(status_code: int) -> tuple[http.server.ThreadingHTTPServer, str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            self.send_response(status_code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    return server, url


def test_source_keeps_default_gate_and_safe_next_action():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'DEFAULT_GATE_URL = "http://127.0.0.1:3080/"' in text
    assert 'DEFAULT_NEXT_RECHECK = "2026-09-10T16:30:00+08:00"' in text
    assert "browser_click" in text
    assert '"go": true' not in text
    assert "subprocess" not in text


def test_ready_gate_reports_http_200_and_section_4_next_action():
    server, url = _run_server(200)
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--url", url, "--timeout", "2"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ready"] is True
    assert data["status_code"] == 200
    assert data["gate_url"] == url
    assert "section 4" in data["next_action"]


def test_non_200_gate_reports_not_ready_and_recheck_time():
    server, url = _run_server(503)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--url",
                url,
                "--timeout",
                "2",
                "--next-recheck",
                "2099-01-01T00:00:00+08:00",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert proc.returncode == 2, proc.stdout
    data = json.loads(proc.stdout)
    assert data["ready"] is False
    assert data["status_code"] == 503
    assert data["next_recheck"] == "2099-01-01T00:00:00+08:00"
    assert "HTTP 200" in data["next_action"]
