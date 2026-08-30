"""Authenticated Human Approval API helpers for the Modal heartbeat."""

from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import sys
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlparse

LEDGER = Path("/data/state/decisions.jsonl")
MAX_BODY_BYTES = 16_384
DECISION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    try:
        return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def proposals() -> list[dict]:
    """Compact, non-secret projection of the append-only canonical ledger."""
    out: dict[str, dict] = {}
    for row in rows():
        decision_id = row.get("decision_id")
        if not isinstance(decision_id, str):
            continue
        if row.get("event", "proposal") == "proposal":
            gate = row.get("gate", {})
            out[decision_id] = {
                "decision_id": decision_id, "created_at": row.get("timestamp"),
                "candidate_id": row.get("decision", {}).get("candidate_id"),
                "reason": row.get("decision", {}).get("reason"),
                "context_id": row.get("decision", {}).get("context_id"),
                "gate_status": gate.get("status"), "gate_reasons": gate.get("reasons", []),
                "orders": gate.get("orders", []), "state": row.get("execution", {}).get("state"),
                "approved_by": None,
            }
        elif decision_id in out and isinstance(row.get("execution"), dict):
            out[decision_id]["state"] = row["execution"].get("state")
            out[decision_id]["approved_by"] = row["execution"].get("approved_by", out[decision_id]["approved_by"])
            out[decision_id]["updated_at"] = row.get("timestamp")
    return sorted(out.values(), key=lambda item: item.get("created_at") or "", reverse=True)


def core_book() -> dict:
    from risk_engine.book import DEFAULT_BOOK, target_cash_weight
    result = {
        "execution": "human-approval-required",
        "entries": [{"symbol": item.symbol, "target_weight": item.target_weight, "beta": item.beta} for item in DEFAULT_BOOK],
        "cash_weight": target_cash_weight(),
        "note": "Selected portfolio target for review. Live sizing is read-only; no core-book order can bypass Human Approval.",
    }
    if not (os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY")):
        result["live_plan"] = {"available": False, "reason": "paper credentials unavailable"}
        return result
    try:
        from feed import AlpacaDataSource
        from scripts.seed_book import _prices, build_plan
        source = AlpacaDataSource()
        equity, cash = source.account()
        held = {symbol: shares for symbol, shares, _price in source.positions()}
        book_rows, _book = build_plan(equity, _prices(source, [item.symbol for item in DEFAULT_BOOK]), held)
        result["live_plan"] = {
            "available": True, "equity": equity, "cash": cash,
            "orders": [
                {"symbol": symbol, "target_shares": target, "buy_shares": buy, "price": price, "notional": dollars}
                for symbol, target, buy, price, dollars in book_rows
            ],
        }
    except Exception as error:
        result["live_plan"] = {"available": False, "reason": f"read failed: {type(error).__name__}"}
    return result


def run_cli(arguments: list[str]) -> dict:
    result = subprocess.run(["python3", "-m", "agent.cli", *arguments], cwd="/app", env=os.environ.copy(), capture_output=True, text=True, timeout=90, check=False)
    if result.returncode:
        raise ValueError((result.stderr.strip() or result.stdout.strip() or "operation failed")[:500])
    return json.loads(result.stdout)


def run_executor(decision_id: str) -> dict:
    result = subprocess.run(["node", "/app/agent/dsh/human-executor.js", "--ledger", str(LEDGER), "--decision-id", decision_id], cwd="/app", env=os.environ.copy(), capture_output=True, text=True, timeout=180, check=False)
    if result.returncode:
        raise ValueError((result.stderr.strip() or "paper submission failed")[:500])
    return json.loads(result.stdout)


PAGE = """<!doctype html><meta charset=utf-8><title>Paper Human Approval</title>
<style>body{font:15px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem;background:#101417;color:#e9eef2}input,button{padding:.55rem;margin:.25rem}pre{white-space:pre-wrap;background:#192127;padding:1rem;border-radius:6px}.proposal{border:1px solid #40515d;margin:1rem 0;padding:1rem;border-radius:8px}</style>
<h1>Paper Human Approval</h1><p>Every action is paper-only, authenticated, revalidated immediately, and written to the ledger.</p>
<label>Approval token <input id=token type=password></label><label>Operator <input id=operator placeholder="name or handle"></label><button onclick=load()>Load</button><pre id=out>Authenticate, then load proposals.</pre>
<script>
const out=document.querySelector('#out');const auth=()=>({'Authorization':'Bearer '+token.value});const safe=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));const safeId=id=>/^[A-Za-z0-9._:-]{1,128}$/.test(id);
async function api(p,o={}){let r=await fetch(p,{...o,headers:{...auth(),'Content-Type':'application/json',...(o.headers||{})}}),j=await r.json();if(!r.ok)throw Error(j.error||r.status);return j}
async function load(){try{let [p,b]=await Promise.all([api('/api/proposals'),api('/api/core-book')]);out.innerHTML='<h2>Selected core book</h2><pre>'+safe(JSON.stringify(b,null,2))+'</pre><h2>Proposals</h2>'+p.proposals.map(x=>{let buttons=safeId(x.decision_id)?((x.state==='proposed'&&x.gate_status==='approved_for_dry_run')?'<button onclick="approve(\\''+x.decision_id+'\\')">Approve and submit one paper order</button> <button onclick="reject(\\''+x.decision_id+'\\')">Reject</button>':'')+' <button onclick="reconcile(\\''+x.decision_id+'\\')">Reconcile</button>':'<em>invalid decision id</em>';return '<div class=proposal><pre>'+safe(JSON.stringify(x,null,2))+'</pre>'+buttons+'</div>'}).join('')}catch(e){out.textContent='Error: '+e.message}}
async function approve(id){try{let j=await api('/api/proposals/'+encodeURIComponent(id)+'/approve',{method:'POST',body:JSON.stringify({operator:operator.value})});alert(JSON.stringify(j,null,2));load()}catch(e){alert(e.message)}}
async function reject(id){try{let j=await api('/api/proposals/'+encodeURIComponent(id)+'/reject',{method:'POST',body:JSON.stringify({operator:operator.value})});alert(JSON.stringify(j,null,2));load()}catch(e){alert(e.message)}}
async function reconcile(id){try{let j=await api('/api/proposals/'+encodeURIComponent(id)+'/reconcile',{method:'POST',body:'{}'});alert(JSON.stringify(j,null,2));load()}catch(e){alert(e.message)}}
</script>"""


class ApprovalApiMixin:
    def _json(self, status: HTTPStatus | int, body: dict | list) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)

    def _authorized(self) -> bool:
        token = os.getenv("HUMAN_APPROVAL_TOKEN")
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        return bool(token and supplied) and hmac.compare_digest(supplied, token)

    def handle_approval_get(self) -> bool:
        path = urlparse(self.path).path
        if path == "/":
            payload = PAGE.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return True
        if path not in {"/api/proposals", "/api/core-book"}:
            return False
        if not self._authorized(): self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"}); return True
        self._json(HTTPStatus.OK, {"proposals": proposals()} if path.endswith("proposals") else core_book()); return True

    def handle_approval_post(self) -> bool:
        if not self._authorized(): self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"}); return True
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY_BYTES: raise ValueError("invalid request body")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict): raise ValueError("request body must be an object")
            if path.startswith("/api/proposals/") and path.endswith("/approve"):
                decision_id = path.removeprefix("/api/proposals/").removesuffix("/approve").strip("/")
                if not DECISION_ID.fullmatch(decision_id): raise ValueError("invalid decision_id")
                operator = body.get("operator")
                if not isinstance(operator, str) or not operator.strip(): raise ValueError("operator is required")
                approval = run_cli(["approve", "--ledger", str(LEDGER), "--decision-id", decision_id, "--approved-by", operator.strip()])
                self._json(HTTPStatus.OK, {"approval": approval, "submission": run_executor(decision_id)}); return True
            if path.startswith("/api/proposals/") and path.endswith("/reject"):
                decision_id = path.removeprefix("/api/proposals/").removesuffix("/reject").strip("/")
                if not DECISION_ID.fullmatch(decision_id): raise ValueError("invalid decision_id")
                operator = body.get("operator")
                if not isinstance(operator, str) or not operator.strip(): raise ValueError("operator is required")
                self._json(HTTPStatus.OK, {"rejection": run_cli(["reject", "--ledger", str(LEDGER), "--decision-id", decision_id, "--rejected-by", operator.strip()])}); return True
            if path.startswith("/api/proposals/") and path.endswith("/reconcile"):
                decision_id = path.removeprefix("/api/proposals/").removesuffix("/reconcile").strip("/")
                if not DECISION_ID.fullmatch(decision_id): raise ValueError("invalid decision_id")
                self._json(HTTPStatus.OK, {"reconciliation": run_cli(["reconcile", "--ledger", str(LEDGER), "--decision-id", decision_id])}); return True
            return False
        except (ValueError, json.JSONDecodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)}); return True
        except subprocess.TimeoutExpired:
            self._json(HTTPStatus.GATEWAY_TIMEOUT, {"error": "operation timed out; inspect the ledger before retrying"}); return True
        except Exception:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal operation failure; inspect the ledger before retrying"}); return True
