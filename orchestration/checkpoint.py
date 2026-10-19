"""Signed external checkpoints for the run ledger (plan section 6.9, invariant I13).

The run ledger is a hash chain, which stops casual edits but not an attacker who can write the database: change
entry k, recompute every hash after it, and `verify_chain` is happy again. A checkpoint closes that gap. Periodically
(and at the end of every run) the ledger's head hash and entry count are signed by a key the runtime cannot read, and
the signed record is copied somewhere the runtime cannot rewrite. To pass verification an attacker would now need the
signing key too, or to rewrite the off-box copies.

Pieces (all pure logic here; sockets, subprocesses and OS-user separation live in the composition root):
  * `Checkpoint`        a signed {seq, ts, head, count, prev} record; `prev` chains each checkpoint to the one before.
  * `SignerService`     what runs as a SEPARATE OS USER holding the private key. It does not sign arbitrary bytes: it
                        accepts only (head, count, prev), assigns seq and time itself, and refuses to sign a count that
                        goes backwards. So a compromised runtime cannot obtain a valid signature for a rolled-back
                        ledger, and cannot use the service as a general signing oracle.
  * `CheckpointStore`   append-only local log (JSON lines, fsync'd).
  * `CheckpointSink`    off-box copy: directory sink here; scp/git sinks in the composition root (they need a subprocess).
  * `CheckpointWriter`  what the runtime calls: read head -> ask the signer -> store -> publish to sinks.
  * `verify_checkpoints` what the Auditor calls: signatures, chain, monotonic counts, ledger agreement (this catches
                        the "recompute the whole chain" double tamper), staleness, and agreement with the off-box copy.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .capability import canonical, public_key_bytes
from .ledger import RunLedger

ZERO = "0" * 64


class CheckpointError(RuntimeError):
    pass


def key_id_of(pub: Ed25519PublicKey) -> str:
    return hashlib.sha256(public_key_bytes(pub)).hexdigest()[:16]


@dataclass(frozen=True)
class Checkpoint:
    seq: int
    ts: float
    head: str
    count: int
    prev: str               # digest() of the previous checkpoint, ZERO for the first
    key_id: str
    sig: str = ""           # base64 Ed25519 over body()

    def body(self) -> bytes:
        return canonical({"seq": self.seq, "ts": self.ts, "head": self.head, "count": self.count,
                          "prev": self.prev, "key_id": self.key_id})

    def digest(self) -> str:
        return hashlib.sha256(self.body() + self.sig.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "head": self.head, "count": self.count, "prev": self.prev,
                "key_id": self.key_id, "sig": self.sig}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Checkpoint":
        try:
            return Checkpoint(int(d["seq"]), float(d["ts"]), str(d["head"]), int(d["count"]), str(d["prev"]),
                              str(d["key_id"]), str(d.get("sig", "")))
        except (KeyError, TypeError, ValueError) as e:
            raise CheckpointError(f"malformed checkpoint: {e}") from e


class SignerPort(Protocol):
    def checkpoint(self, head: str, count: int, prev: str) -> Checkpoint: ...


# ------------------------------------------------------------------------------------- the signer
class SignerService:
    """Runs as the separate OS user. Persists its own state (last seq/count/head) so a restart cannot be used to
    reset the monotonic rule. The state file must be writable only by that user."""

    def __init__(self, private_key: Ed25519PrivateKey, state_path: str, *, clock: Callable[[], float] = time.time):
        self._key = private_key
        self.key_id = key_id_of(private_key.public_key())
        self._path = state_path
        self.clock = clock
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self._path, encoding="utf-8") as fh:
                st = json.load(fh)
            return {"seq": int(st["seq"]), "count": int(st["count"]), "head": str(st["head"]),
                    "digest": str(st.get("digest", ZERO))}
        except FileNotFoundError:
            return {"seq": 0, "count": 0, "head": ZERO, "digest": ZERO}

    def _save(self) -> None:
        tmp = f"{self._path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._key.public_key()

    def checkpoint(self, head: str, count: int, prev: str) -> Checkpoint:
        if not (isinstance(head, str) and len(head) == 64 and all(c in "0123456789abcdef" for c in head)):
            raise CheckpointError("head must be a 64-char hex digest")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise CheckpointError("count must be a non-negative integer")
        with self._lock:
            st = self._state
            if prev != st["digest"]:
                raise CheckpointError("prev does not match the last checkpoint this signer issued")
            if count < st["count"]:
                raise CheckpointError(f"refusing to sign: count went backwards ({count} < {st['count']}); "
                                      f"the ledger was truncated or rolled back")
            if count == st["count"] and st["seq"] > 0 and head != st["head"]:
                raise CheckpointError("refusing to sign: same count but a different head hash")
            cp = Checkpoint(st["seq"] + 1, round(self.clock(), 3), head, count, prev, self.key_id)
            cp = Checkpoint(**{**cp.__dict__, "sig": base64.b64encode(self._key.sign(cp.body())).decode()})
            self._state = {"seq": cp.seq, "count": cp.count, "head": cp.head, "digest": cp.digest()}
            self._save()
            return cp


class LocalSigner:
    """Same interface as the remote signer, in-process. For tests and development only: the key is readable by
    whoever runs the runtime, so this gives NO protection against an attacker with runtime access."""

    def __init__(self, service: SignerService):
        self.service = service

    def checkpoint(self, head: str, count: int, prev: str) -> Checkpoint:
        return self.service.checkpoint(head, count, prev)


# --------------------------------------------------------------------------------- store and sinks
class CheckpointStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def all(self) -> List[Checkpoint]:
        out: List[Checkpoint] = []
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        out.append(Checkpoint.from_dict(json.loads(line)))
        except FileNotFoundError:
            pass
        return out

    def last(self) -> Optional[Checkpoint]:
        cps = self.all()
        return cps[-1] if cps else None

    def append(self, cp: Checkpoint) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(cp.to_dict(), sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


class CheckpointSink(Protocol):
    name: str

    def publish(self, cp: Checkpoint) -> None: ...
    def fetch_all(self) -> List[Checkpoint]: ...


class DirectorySink:
    """One file per checkpoint in a directory that lives off the runtime's control (a mounted share from the second
    machine, a synced folder, a git work tree that a separate job pushes). Files are written once and never
    overwritten: an existing file with different content is an error, not an update."""
    name = "directory"

    def __init__(self, directory: str):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)

    def _file(self, seq: int) -> str:
        return os.path.join(self.dir, f"checkpoint_{seq:09d}.json")

    def publish(self, cp: Checkpoint) -> None:
        path = self._file(cp.seq)
        data = json.dumps(cp.to_dict(), sort_keys=True)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                if fh.read() != data:
                    raise CheckpointError(f"{path} already exists with different content")
            return
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def fetch_all(self) -> List[Checkpoint]:
        out = []
        for name in sorted(os.listdir(self.dir)):
            if name.startswith("checkpoint_") and name.endswith(".json"):
                with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
                    out.append(Checkpoint.from_dict(json.load(fh)))
        return sorted(out, key=lambda c: c.seq)


# ------------------------------------------------------------------------------------------ writer
class CheckpointWriter:
    """Best effort by design: if the signer or a sink is down the run still finishes, the failure is recorded, and the
    Auditor reports the resulting gap as a finding (staleness / sink disagreement). A silent gap is the thing to avoid."""

    def __init__(self, ledger: RunLedger, signer: SignerPort, store: CheckpointStore,
                 sinks: Iterable[CheckpointSink] = ()):
        self.ledger, self.signer, self.store, self.sinks = ledger, signer, store, list(sinks)
        self.failures: List[str] = []

    def write(self) -> Optional[Checkpoint]:
        head, count = self.ledger.head()
        last = self.store.last()
        try:
            cp = self.signer.checkpoint(head, count, last.digest() if last else ZERO)
        except Exception as e:  # noqa: BLE001
            self._fail(f"signer:{type(e).__name__}:{str(e)[:160]}")
            return None
        self.store.append(cp)
        for sink in self.sinks:
            try:
                sink.publish(cp)
            except Exception as e:  # noqa: BLE001
                self._fail(f"sink:{getattr(sink, 'name', '?')}:{type(e).__name__}:{str(e)[:160]}")
        return cp

    def _fail(self, msg: str) -> None:
        self.failures.append(msg)
        try:
            self.ledger.append("checkpoint_failed", {"detail": msg})
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------------------- verifier
@dataclass
class Problem:
    severity: str            # high | medium | low
    code: str
    detail: str


@dataclass
class CheckpointReport:
    problems: List[Problem] = field(default_factory=list)
    checked: int = 0
    latest_seq: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def add(self, severity: str, code: str, detail: str) -> None:
        self.problems.append(Problem(severity, code, detail))


def verify_checkpoints(ledger: RunLedger, checkpoints: List[Checkpoint], public_keys: Dict[str, Ed25519PublicKey], *,
                       off_box: Optional[List[Checkpoint]] = None, max_lag_entries: Optional[int] = None) -> CheckpointReport:
    rep = CheckpointReport(checked=len(checkpoints), latest_seq=checkpoints[-1].seq if checkpoints else 0)
    count_now = ledger.count()
    if not checkpoints:
        if count_now > 0:
            rep.add("high", "no_checkpoint", f"the run ledger has {count_now} entries but there is no checkpoint log")
        return rep
    prev_digest, prev_seq, prev_count, prev_head = ZERO, 0, 0, ZERO
    for cp in checkpoints:
        pub = public_keys.get(cp.key_id)
        if pub is None:
            rep.add("high", "unknown_signing_key", f"checkpoint {cp.seq} signed by unknown key {cp.key_id}")
        else:
            try:
                pub.verify(base64.b64decode(cp.sig), cp.body())
            except (InvalidSignature, ValueError):
                rep.add("high", "bad_signature", f"checkpoint {cp.seq} signature does not verify")
        if cp.seq != prev_seq + 1:
            rep.add("high", "checkpoint_gap", f"checkpoint {cp.seq} follows {prev_seq}: entries removed or reordered")
        if cp.prev != prev_digest:
            rep.add("high", "checkpoint_chain_broken", f"checkpoint {cp.seq} does not chain to the one before it")
        if cp.count < prev_count or (cp.count == prev_count and prev_seq and cp.head != prev_head):
            rep.add("high", "checkpoint_count_regressed", f"checkpoint {cp.seq} count/head disagrees with its predecessor")
        if cp.count > count_now:
            rep.add("high", "ledger_shorter_than_checkpoint",
                    f"checkpoint {cp.seq} covers {cp.count} entries but the ledger has {count_now}: truncated")
        else:
            actual = ledger.hash_at(cp.count) if cp.count > 0 else ZERO
            if actual != cp.head:
                rep.add("high", "ledger_head_mismatch",
                        f"entry {cp.count} hashes to {str(actual)[:12]}... but checkpoint {cp.seq} signed {cp.head[:12]}...: "
                        "the ledger was rewritten after this checkpoint (a recomputed chain is caught here)")
        prev_digest, prev_seq, prev_count, prev_head = cp.digest(), cp.seq, cp.count, cp.head
    if max_lag_entries is not None and count_now - checkpoints[-1].count > max_lag_entries:
        rep.add("medium", "checkpoint_stale",
                f"{count_now - checkpoints[-1].count} ledger entries are newer than the last checkpoint")
    if off_box is not None:
        local = {c.seq: c for c in checkpoints}
        for oc in off_box:
            lc = local.get(oc.seq)
            if lc is None:
                rep.add("high", "local_log_missing_checkpoint", f"off-box copy has checkpoint {oc.seq}; the local log does not")
            elif lc.digest() != oc.digest():
                rep.add("high", "off_box_mismatch", f"checkpoint {oc.seq} differs between the local log and the off-box copy")
        if off_box and checkpoints[-1].seq < max(c.seq for c in off_box):
            rep.add("high", "local_log_truncated", "the off-box copy is ahead of the local checkpoint log")
        if not off_box:
            rep.add("medium", "off_box_empty", "no checkpoint has been copied off this machine")
    return rep


def verify_ledger_integrity(ledger: RunLedger, checkpoints: List[Checkpoint], public_keys: Dict[str, Ed25519PublicKey], *,
                            off_box: Optional[List[Checkpoint]] = None,
                            max_lag_entries: Optional[int] = None) -> CheckpointReport:
    """What the Auditor runs: the hash chain AND the checkpoints. Neither alone is enough: the chain catches a naive
    edit, the checkpoints catch an edit whose chain was recomputed."""
    rep = verify_checkpoints(ledger, checkpoints, public_keys, off_box=off_box, max_lag_entries=max_lag_entries)
    ok, bad_id = ledger.verify_chain()
    if not ok:
        rep.add("high", "chain_broken", f"the run ledger hash chain breaks at entry id {bad_id}")
    return rep
