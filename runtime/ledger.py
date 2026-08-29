"""The trade ledger — persistent, shared memory of every cycle (README §4 LOG, §8).

An append-only JSONL file (one cycle per line): cheap to append, easy to read, and safe
for two processes (the cron agent and the UI) to both write to. Each record captures what
the agent decided and traded that cycle, so trades survive restarts, the UI can show the
cron's real activity, and performance can be graded later.

    from runtime.ledger import TradeLedger
    led = TradeLedger("state/ledger.jsonl")
    led.record_cycle(state, mode="LIVE")     # after run_cycle(...)
    led.entries(limit=25)                     # newest-first, for the UI
    led.summary()                             # aggregates for a headline strip
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class TradeLedger:
    def __init__(self, path: str | Path = "state/ledger.jsonl"):
        self.path = Path(path)

    def _count(self) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def append(self, entry: dict) -> dict:
        """Append one record (stamped with a sequential id) and return it."""
        stamped = {"id": self._count() + 1, **entry}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(stamped) + "\n")
        return stamped

    def record_cycle(self, state: dict, *, mode: str = "MOCK", ts: str | None = None) -> dict:
        """Build + append a record from one `run_cycle` result state."""
        ctx = state.get("context") or {}
        plan = ctx.get("plan") or {}
        inc = plan.get("income") or {}
        snap = ctx.get("snapshot") or {}
        val = ctx.get("validation") or {}
        ex = state.get("execution") or {}
        dec = state.get("decision") or {}
        hedge = plan.get("hedge") or {}
        return self.append({
            "ts": ts or datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "posture": dec.get("posture") or plan.get("posture", ""),
            "approved": dec.get("approved"),
            "risk_score": snap.get("risk_score"),
            "equity": (ctx.get("portfolio") or {}).get("equity"),
            "index_symbol": ctx.get("index_symbol", "SPY"),
            "index_price": (ctx.get("market") or {}).get("index_price"),
            "credit": inc.get("total_credit", 0.0),
            "hedge_cost": hedge.get("total_cost", 0.0),
            "net_theta_per_day": inc.get("net_theta_per_day", 0.0),
            "hedge_contracts": hedge.get("contracts_target", 0),
            "orders": ex.get("orders", []),
            "dry_run": ex.get("dry_run", True),
            "validation_ok": val.get("ok", True),
            "violations": val.get("violations", []),
            "log": state.get("log", ""),
        })

    def grade(self, cycle_id: int, grade: dict) -> bool:
        """Attach a self-grade to an existing cycle (rewrites the small ledger file)."""
        rows = self.entries(newest_first=False)
        hit = False
        for r in rows:
            if r.get("id") == cycle_id:
                r["grade"] = grade
                hit = True
        if hit:
            with self.path.open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
        return hit

    def entries(self, limit: int | None = None, newest_first: bool = True) -> list[dict]:
        """Read records (newest-first by default; `limit` caps the count)."""
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        if newest_first:
            rows.reverse()
        return rows[:limit] if limit else rows

    def summary(self) -> dict:
        """Aggregate stats across all recorded cycles (for a headline strip)."""
        rows = self.entries(newest_first=False)
        postures: dict[str, int] = {}
        for r in rows:
            key = (r.get("posture") or "").split(" ")[0] or "?"
            postures[key] = postures.get(key, 0) + 1
        graded = [r["grade"] for r in rows if r.get("grade")]
        realized = sum(g.get("realized_pnl", 0.0) for g in graded)
        wins = sum(1 for g in graded if g.get("realized_pnl", 0.0) > 0)
        return {
            "cycles": len(rows),
            "total_credit": round(sum(r.get("credit", 0.0) for r in rows), 2),
            "orders_placed": sum(len(r.get("orders", [])) for r in rows),
            "hedged_cycles": sum(1 for r in rows if (r.get("hedge_contracts") or 0) > 0),
            "live_cycles": sum(1 for r in rows if not r.get("dry_run", True)),
            "graded_cycles": len(graded),
            "realized_pnl": round(realized, 2),
            "win_rate": round(wins / len(graded), 3) if graded else None,
            "postures": postures,
        }
