"""
Label-aware model routing (plan s3 "Model router", s6.4, s6.10).

`AgentSpec.model_policy` is only an upper bound. On EVERY call the router checks the live context
labels: cloud is used only if the spec allows it AND the context is cloud-eligible (not secret, not
PII). A local failure falls through to cloud only when both hold — never silently otherwise. Cloud
calls reserve spend atomically before the call so concurrent runs cannot jointly overshoot the cap.

P1 additions: every cloud provider is a `CloudEntry` carrying its own DATA CEILING (a free tier whose
inputs may be used for training is PUBLIC-only), its own price (input/output per million tokens) and
its own rate limits (rpm/rpd/tpm/tpd). Spend is reserved at the worst case and settled with the
provider-reported usage when available. Free entries never touch the spend ledger.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple, Union

from eternal_core.llm.base import LLMError, LLMProvider

from .llm_errors import LLMBadOutput

from .contracts import Labels, ModelPolicy, Sensitivity


class ModelUnavailable(Exception):
    def __init__(self, reason: str, attempts: Optional[List[dict]] = None):
        super().__init__(reason)
        self.reason = reason
        self.attempts = attempts or []


class SpendCapExceeded(Exception):
    pass


@dataclass
class ModelReply:
    text: str
    provider: str
    model: str
    tier: str                       # "local" | "cloud"
    attempts: List[dict] = field(default_factory=list)
    input_tokens: Optional[int] = None      # provider-reported usage, None when not reported
    output_tokens: Optional[int] = None
    cost_usd: float = 0.0                   # settled cost of this call (0 for local and free entries)

    @property
    def external_request(self) -> bool:
        return self.tier == "cloud"


def cloud_eligibility(labels: Labels, policy: ModelPolicy,
                      ceiling: Optional[Sensitivity] = None) -> Tuple[bool, str]:
    """Global cloud rule, optionally tightened by a specific provider's data ceiling."""
    if policy != ModelPolicy.CLOUD_ALLOWED:
        return False, "agent policy is local_only"
    if labels.sensitivity >= Sensitivity.SECRET:
        return False, "context contains secret data"
    if labels.pii:
        return False, "context contains PII"
    if ceiling is not None and labels.sensitivity > ceiling:
        return False, (f"context is {labels.sensitivity.name.lower()} but provider ceiling is "
                       f"{ceiling.name.lower()}")
    return True, "eligible"


@dataclass(frozen=True)
class RateLimits:
    """Any field left None is unlimited. Defaults to no limits."""
    rpm: Optional[int] = None
    rpd: Optional[int] = None
    tpm: Optional[int] = None
    tpd: Optional[int] = None


class RateLimiter:
    """Sliding-window limiter (60 s and 24 h windows). Clock is injectable for tests.

    `acquire` checks AND records in one step (the request is counted even if it later fails, since the
    provider counts it too). Returns (ok, reason, wait_seconds)."""

    def __init__(self, limits: RateLimits, clock: Callable[[], float] = time.monotonic):
        self.limits, self.clock = limits, clock
        self._events: Deque[Tuple[float, int]] = deque()      # (time, tokens)
        self._lock = threading.Lock()

    def _window(self, now: float, span: float) -> Tuple[int, int, float]:
        n = tok = 0
        oldest = now
        for t, k in self._events:
            if now - t < span:
                n += 1
                tok += k
                oldest = min(oldest, t)
        return n, tok, oldest

    def acquire(self, tokens: int) -> Tuple[bool, str, float]:
        with self._lock:
            now = self.clock()
            while self._events and now - self._events[0][0] >= 86400:
                self._events.popleft()
            L = self.limits
            n_m, tok_m, old_m = self._window(now, 60.0)
            n_d, tok_d, old_d = self._window(now, 86400.0)
            checks = [("rpm", L.rpm, n_m + 1, old_m, 60.0), ("tpm", L.tpm, tok_m + tokens, old_m, 60.0),
                      ("rpd", L.rpd, n_d + 1, old_d, 86400.0), ("tpd", L.tpd, tok_d + tokens, old_d, 86400.0)]
            for name, lim, would, oldest, span in checks:
                if lim is not None and would > lim:
                    return False, f"{name} limit {lim} reached", max(0.0, span - (now - oldest))
            self._events.append((now, tokens))
            return True, "", 0.0

    def correct(self, estimated: int, actual: int) -> None:
        """Replace the estimate recorded for the latest event with the provider-reported figure."""
        with self._lock:
            if self._events:
                t, k = self._events[-1]
                if k == estimated:
                    self._events[-1] = (t, actual)


@dataclass
class CloudEntry:
    """A cloud provider plus the policy that governs it."""
    provider: LLMProvider
    max_sensitivity: Sensitivity = Sensitivity.INTERNAL
    input_price_per_mtok: float = 0.0
    output_price_per_mtok: float = 0.0
    max_output_tokens: int = 0              # worst-case output reserved against the spend cap
    limits: RateLimits = field(default_factory=RateLimits)
    free: bool = False
    clock: Callable[[], float] = time.monotonic
    limiter: Optional[RateLimiter] = None

    def __post_init__(self):
        if self.limiter is None:
            self.limiter = RateLimiter(self.limits, self.clock)
        if self.free and (self.input_price_per_mtok or self.output_price_per_mtok):
            raise ValueError("a free entry must have zero prices")

    @property
    def name(self) -> str:
        return self.provider.name

    def worst_case_cost(self, in_tokens: int) -> float:
        return (in_tokens * self.input_price_per_mtok + self.max_output_tokens * self.output_price_per_mtok) / 1e6

    def actual_cost(self, in_tokens: int, out_tokens: int) -> float:
        return (in_tokens * self.input_price_per_mtok + out_tokens * self.output_price_per_mtok) / 1e6


class SpendLedger:
    """Daily (UTC) cloud-spend accounting with atomic reserve-before-call (BEGIN IMMEDIATE).

    Two APIs share one daily total:
      * legacy `reserve(amount)` / `adjust(delta)`: anonymous, kept for callers that only need the cap;
      * reservations (`open` -> `mark_sent` -> `settle` | `release`): each has an id, a run and agent, a state, and
        survives a crash. A crash can leave a reservation open, and `reap` resolves it honestly: if the request was
        never sent it is released (provably nothing was spent); if it WAS sent and we never heard back it stays
        counted as `unknown` (we cannot prove it was free), and a late `settle` from an abandoned worker thread
        corrects it. `settle`/`release` are idempotent, so a thread abandoned by a timeout can finish safely.
    """

    OPEN_STATES = ("reserved", "sent")

    def __init__(self, db_path: str, daily_cap_usd: float, clock=None):
        self.db_path = db_path
        self.cap = daily_cap_usd
        self._clock = clock or time.time
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS spend (day TEXT PRIMARY KEY, total REAL NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS reservations ("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, run_id TEXT, agent TEXT, provider TEXT, "
                      "amount REAL NOT NULL, actual REAL, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)")

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        return _Closing(c)

    def _today(self) -> str:
        return self._day_of(self._clock())

    def _day_of(self, ts: float) -> str:
        return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d")

    def _check_and_add(self, c, day: str, amount: float) -> None:
        row = c.execute("SELECT total FROM spend WHERE day=?", (day,)).fetchone()
        total = row[0] if row else 0.0
        if total + amount > self.cap:
            raise SpendCapExceeded(
                f"daily cloud-spend cap {self.cap:.2f} USD would be exceeded "
                f"(spent/reserved {total:.4f} + {amount:.4f})")
        c.execute("INSERT INTO spend(day,total) VALUES(?,?) ON CONFLICT(day) DO UPDATE SET total=total+excluded.total",
                  (day, amount))

    # -- legacy API ------------------------------------------------------------------------------------
    def reserve(self, amount: float) -> float:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                self._check_and_add(c, self._today(), amount)
                c.execute("COMMIT")
            except SpendCapExceeded:
                c.execute("ROLLBACK")
                raise
            except Exception:
                c.execute("ROLLBACK")
                raise
        return amount

    def adjust(self, delta: float) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE spend SET total=MAX(0,total+?) WHERE day=?", (delta, self._today()))
            c.execute("COMMIT")

    # -- reservations ----------------------------------------------------------------------------------
    def open(self, amount: float, *, run_id=None, agent=None, provider=None) -> int:
        now = self._clock()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                day = self._day_of(now)
                self._check_and_add(c, day, amount)
                cur = c.execute("INSERT INTO reservations(day,run_id,agent,provider,amount,state,created,updated) "
                                "VALUES(?,?,?,?,?,'reserved',?,?)", (day, run_id, agent, provider, amount, now, now))
                rid = cur.lastrowid
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        return rid

    def mark_sent(self, rid: int) -> None:
        """Call immediately before the network request: from here on the money may be spent."""
        with self._conn() as c:
            c.execute("UPDATE reservations SET state='sent', updated=? WHERE id=? AND state='reserved'",
                      (self._clock(), rid))

    def _finish(self, rid: int, new_state: str, actual: float, allowed: tuple) -> bool:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute("SELECT day, amount, state FROM reservations WHERE id=?", (rid,)).fetchone()
                if row is None or row[2] not in allowed:
                    c.execute("ROLLBACK")
                    return False
                day, amount, _ = row
                c.execute("UPDATE spend SET total=MAX(0,total+?) WHERE day=?", (actual - amount, day))
                c.execute("UPDATE reservations SET state=?, actual=?, updated=? WHERE id=?",
                          (new_state, actual, self._clock(), rid))
                c.execute("COMMIT")
                return True
            except Exception:
                c.execute("ROLLBACK")
                raise

    def settle(self, rid: int, actual: float) -> bool:
        """Record the real cost. Also corrects a reservation the reaper marked `unknown`. False if already final."""
        return self._finish(rid, "settled", max(0.0, actual), self.OPEN_STATES + ("unknown",))

    def release(self, rid: int) -> bool:
        """Nothing was charged: refund in full. False if already final."""
        return self._finish(rid, "released", 0.0, self.OPEN_STATES)

    def reap(self, stale_after_s: float = 900.0) -> dict:
        """Resolve reservations a crashed or abandoned caller never finished."""
        cutoff = self._clock() - stale_after_s
        with self._conn() as c:
            rows = c.execute("SELECT id, state FROM reservations WHERE state IN ('reserved','sent') AND updated < ?",
                             (cutoff,)).fetchall()
        released = unknown = 0
        for rid, state in rows:
            if state == "reserved":
                released += int(self.release(rid))
            else:
                with self._conn() as c:
                    c.execute("UPDATE reservations SET state='unknown', updated=? WHERE id=? AND state='sent'",
                              (self._clock(), rid))
                unknown += 1
        return {"released": released, "unknown": unknown}

    def by_run(self, run_id: str) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT state, amount, actual FROM reservations WHERE run_id=?", (run_id,)).fetchall()
        spent = sum((a if a is not None else amt) for st, amt, a in rows if st != "released")
        return {"run_id": run_id, "calls": len(rows), "spent_usd": round(spent, 6),
                "open": sum(1 for st, _, _ in rows if st in self.OPEN_STATES),
                "unknown": sum(1 for st, _, _ in rows if st == "unknown")}

    def report_today(self) -> dict:
        day = self._day_of(self._clock())
        with self._conn() as c:
            rows = c.execute("SELECT agent, state, amount, actual FROM reservations WHERE day=?", (day,)).fetchall()
        per_agent: dict = {}
        for agent, st, amt, act in rows:
            if st == "released":
                continue
            per_agent[agent or "?"] = round(per_agent.get(agent or "?", 0.0) + (act if act is not None else amt), 6)
        return {"day": day, "total_usd": round(self.total_today(), 6), "cap_usd": self.cap, "per_agent_usd": per_agent,
                "unknown_calls": sum(1 for _, st, _, _ in rows if st == "unknown"),
                "open_calls": sum(1 for _, st, _, _ in rows if st in self.OPEN_STATES)}

    def total_today(self) -> float:
        with self._conn() as c:
            row = c.execute("SELECT total FROM spend WHERE day=?", (self._today(),)).fetchone()
            return row[0] if row else 0.0


class _Closing:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()
        return False


class PolicyRouter:
    """`cloud` items may be plain providers (legacy: INTERNAL ceiling, blended price, no limits) or CloudEntry."""

    def __init__(self, local: List[LLMProvider], cloud: List[Union[LLMProvider, CloudEntry]],
                 spend: Optional[SpendLedger] = None, price_per_1k_tokens_usd: float = 0.01):
        self.local = list(local)
        self.spend = spend
        self.price = price_per_1k_tokens_usd
        blended = price_per_1k_tokens_usd * 1000.0
        self.cloud: List[CloudEntry] = [
            c if isinstance(c, CloudEntry) else
            CloudEntry(c, Sensitivity.INTERNAL, blended, blended, max_output_tokens=0)
            for c in cloud]

    @staticmethod
    def _usage(r) -> Tuple[Optional[int], Optional[int]]:
        i, o = getattr(r, "input_tokens", None), getattr(r, "output_tokens", None)
        return (i if isinstance(i, int) else None, o if isinstance(o, int) else None)

    def _try_local(self, system_prompt, messages, attempts) -> Optional[ModelReply]:
        for p in self.local:
            if not p.is_configured():
                attempts.append({"provider": p.name, "tier": "local", "ok": False, "error": "not configured"})
                continue
            try:
                r = p.generate(system_prompt, messages)
                attempts.append({"provider": p.name, "tier": "local", "ok": True, "error": ""})
                i, o = self._usage(r)
                return ModelReply(r.text, r.provider, r.model, "local", attempts, i, o, 0.0)
            except LLMBadOutput as e:
                attempts.append({"provider": p.name, "tier": "local", "ok": True, "error": f"bad_output: {e}"[:200]})
                return ModelReply(e.salvage, p.name, getattr(p, "model", "?"), "local", attempts)
            except LLMError as e:
                attempts.append({"provider": p.name, "tier": "local", "ok": False, "error": str(e)})
        return None

    def generate(self, system_prompt: str, messages: List[Dict[str, str]], labels: Labels,
                 policy: ModelPolicy, est_tokens: int, *, run_id: Optional[str] = None,
                 agent: Optional[str] = None) -> ModelReply:
        attempts: List[dict] = []
        reply = self._try_local(system_prompt, messages, attempts)
        if reply:
            return reply
        ok, why = cloud_eligibility(labels, policy)
        if not ok:
            raise ModelUnavailable(f"local model unavailable and cloud is not permitted: {why}", attempts)
        if not any(e.provider.is_configured() for e in self.cloud):
            raise ModelUnavailable("local model unavailable and no cloud provider is configured", attempts)

        cap_blocked = False
        for entry in self.cloud:
            name = entry.name
            if not entry.provider.is_configured():
                attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": "not configured"})
                continue
            ok, why = cloud_eligibility(labels, policy, entry.max_sensitivity)
            if not ok:
                attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": f"skipped: {why}",
                                 "skipped": "ceiling"})
                continue
            allowed, lim_why, wait = entry.limiter.acquire(est_tokens + entry.max_output_tokens)
            if not allowed:
                attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": f"skipped: {lim_why}",
                                 "skipped": "rate_limit", "wait_s": round(wait, 1)})
                continue
            reserved, rid = 0.0, None
            if self.spend is not None and not entry.free:
                try:
                    reserved = entry.worst_case_cost(est_tokens)
                    rid = self.spend.open(reserved, run_id=run_id, agent=agent, provider=name)
                except SpendCapExceeded as e:
                    reserved = 0.0
                    cap_blocked = True
                    attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": f"skipped: {e}",
                                     "skipped": "spend_cap"})
                    continue
            try:
                if rid is not None:
                    self.spend.mark_sent(rid)
                r = entry.provider.generate(system_prompt, messages)
            except LLMBadOutput as e:
                # The call happened (and may be billed): keep the reservation, hand the salvage text back so the
                # runner counts an invalid turn and repairs, instead of treating a model fault as an outage.
                attempts.append({"provider": name, "tier": "cloud", "ok": True, "error": f"bad_output: {e}"[:200]})
                if rid is not None:
                    self.spend.settle(rid, reserved)
                return ModelReply(e.salvage, name, getattr(entry.provider, "model", "?"), "cloud", attempts,
                                  None, None, reserved)
            except LLMError as e:
                if rid is not None:
                    self.spend.release(rid)             # nothing was charged: refund the reservation
                attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": str(e)})
                continue
            i, o = self._usage(r)
            cost = 0.0
            if not entry.free:
                if i is not None and o is not None:
                    cost = entry.actual_cost(i, o)
                    if rid is not None:
                        self.spend.settle(rid, cost)            # settle to the real figure
                else:
                    cost = reserved                             # no usage reported: keep the worst case
                    if rid is not None:
                        self.spend.settle(rid, reserved)
            if i is not None and o is not None:
                entry.limiter.correct(est_tokens + entry.max_output_tokens, i + o)
            attempts.append({"provider": name, "tier": "cloud", "ok": True, "error": ""})
            return ModelReply(r.text, r.provider, r.model, "cloud", attempts, i, o, cost)

        if cap_blocked and not any(a["ok"] for a in attempts) and \
                all(a.get("skipped") in ("spend_cap", "ceiling", "rate_limit") or a["error"] == "not configured"
                    for a in attempts if a["tier"] == "cloud"):
            raise SpendCapExceeded("daily cloud-spend cap blocks every eligible cloud provider")
        cloud_attempts = [a for a in attempts if a["tier"] == "cloud"]
        if cloud_attempts and all(a.get("skipped") == "ceiling" or a["error"] == "not configured"
                                  for a in cloud_attempts) and any(a.get("skipped") == "ceiling" for a in cloud_attempts):
            raise ModelUnavailable("blocked_by_policy: no cloud provider is permitted for this data's sensitivity", attempts)
        raise ModelUnavailable("local and cloud providers all failed", attempts)
