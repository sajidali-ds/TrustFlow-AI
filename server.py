"""
Demo UI + JSON API using only the standard library.

    python3 server.py            # http://localhost:8000

GET  /                  the demo page (static/index.html)
GET  /api/scenarios     the built-in scenarios
POST /api/resolve       {customer_id, order_id, message, proposer?, guardrail?, chaos?[]} -> full result
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from main import run_case
from agent.graph import build_graph
from scenarios.cases import SCENARIOS

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = build_graph()
CLEAN = lambda s: {k: v for k, v in s.items() if not k.startswith("_")}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            with open(os.path.join(ROOT, "static", "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/api/scenarios":
            return self._send(200, SCENARIOS)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/resolve":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            case = dict(case_id=body.get("case_id", "WEB-1"), customer_id=body["customer_id"],
                        order_id=body["order_id"], customer_message=body["message"],
                        proposer=body.get("proposer", "auto"), guardrail=bool(body.get("guardrail", True)),
                        chaos=body.get("chaos", []), as_of=body.get("as_of", "2026-11-20"))
            self._send(200, CLEAN(run_case(APP, case)))
        except (KeyError, ValueError) as e:
            self._send(400, {"error": f"bad request: {e}"})

    def log_message(self, *a):
        pass


def serve(port: int = 8000):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Resolution agent demo on http://localhost:{port}  (Ctrl+C to stop)")
    srv.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
