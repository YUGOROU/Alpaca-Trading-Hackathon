"""Live app UI backend — a tiny stdlib web server (README §4 UI). Zero extra deps.

Serves the dashboard page and the live engine JSON, so the page shows the agent's
CURRENT risk-validated decision as trade-memo cards (like the Alpaca screenshot).

    GET  /             -> the dashboard page
    GET  /api/context  -> current snapshot + risk-validated plan (auto-polled)
    POST /api/cycle    -> run one full decide+execute (dry-run) cycle, return result

Uses live Alpaca data when ALPACA_API_KEY/ALPACA_SECRET_KEY are set, else MockDataSource.
Run via `python scripts/run_webui.py`.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HERE = Path(__file__).parent


def _make_source():
    if os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"):
        from feed import AlpacaDataSource

        return AlpacaDataSource(), "LIVE"
    from feed import MockDataSource

    return MockDataSource(), "MOCK"


class _State:
    source = None
    mode = "MOCK"
    store = None
    ledger = None  # shared, persistent trade ledger (also written by the cron agent)


def _init() -> None:
    from feed import StateStore
    from runtime.ledger import TradeLedger

    _State.source, _State.mode = _make_source()
    _State.store = StateStore(os.getenv("AGENT_STATE_PATH", "state/state.json"))
    _State.ledger = TradeLedger(os.getenv("AGENT_LEDGER_PATH", "state/ledger.jsonl"))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, (_HERE / "index.html").read_text(encoding="utf-8"),
                       "text/html; charset=utf-8")
            return
        if self.path.startswith("/api/context"):
            from runtime.strategy_api import get_strategy_context

            try:
                ctx = get_strategy_context(_State.source, _State.store)
                ctx["mode"] = _State.mode
                self._send(200, json.dumps(ctx))
            except Exception as e:  # surface engine errors to the page
                self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
            return
        if self.path.startswith("/api/positions"):
            try:
                rows = _State.source.positions()
                out = [{"symbol": s, "shares": sh, "price": px,
                        "market_value": round(sh * px, 2)} for (s, sh, px) in rows]
                self._send(200, json.dumps({"positions": out}))
            except Exception as e:
                self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
            return
        if self.path.startswith("/api/history"):
            self._send(200, json.dumps({"history": _State.ledger.entries(limit=25)}))
            return
        if self.path.startswith("/api/summary"):
            self._send(200, json.dumps(_State.ledger.summary()))
            return
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self) -> None:
        if self.path.startswith("/api/cycle"):
            from harness import run_cycle

            try:
                state = run_cycle(_State.source, _State.store)
                _State.ledger.record_cycle(state, mode=_State.mode)
                try:  # self-grade any now-expired past cycles
                    from runtime.grade import grade_ledger

                    grade_ledger(_State.ledger, price_lookup=_State.source.latest_price)
                except Exception:
                    pass
                self._send(200, json.dumps({
                    "decision": state.get("decision"),
                    "execution": state.get("execution"),
                    "log": state.get("log"),
                }))
            except Exception as e:
                self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
            return
        self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *args) -> None:  # keep the console quiet
        pass


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    _init()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"[{_State.mode}] live UI on http://{host}:{port}  (Ctrl+C to stop)")
    httpd.serve_forever()
