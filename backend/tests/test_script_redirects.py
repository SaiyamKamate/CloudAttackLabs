"""The real scenario scripts never follow a redirect (plan section 13.1, rule 6).

A local "target" answers every request with 302 to a second server. The script
runs as a real subprocess; the second server must receive nothing.
"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.scenario_engine import runner

SCRIPTS = Path(__file__).resolve().parents[2] / "scenarios" / "scripts"


def serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def servers():
    hits = []

    class Elsewhere(BaseHTTPRequestHandler):
        def _hit(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        do_GET = do_POST = _hit

        def log_message(self, *args):
            pass

    elsewhere = serve(Elsewhere)
    location = f"http://127.0.0.1:{elsewhere.server_port}/stolen"

    class Redirector(BaseHTTPRequestHandler):
        def _redirect(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            # 302 is what urllib follows by default (a POST becomes a GET to Location).
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = _redirect

        def log_message(self, *args):
            pass

    target = serve(Redirector)
    yield f"http://127.0.0.1:{target.server_port}", hits
    target.shutdown()
    elsewhere.shutdown()


def test_authentication_abuse_does_not_follow_redirects(servers):
    base_url, hits = servers
    result = runner.execute_script(
        SCRIPTS / "authentication_abuse.py", base_url, {"username": "lab_user", "attempts": 3}, timeout_seconds=30
    )
    assert result.outcome == "completed", result.error
    assert result.summary["other"] == 3
    assert hits == []


def test_unauthorized_resource_access_does_not_follow_redirects(servers):
    base_url, hits = servers
    runner.execute_script(
        SCRIPTS / "unauthorized_resource_access.py", base_url, {"username": "lab_restricted", "attempts": 2},
        timeout_seconds=30,
    )
    assert hits == []
