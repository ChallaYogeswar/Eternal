import json
import sqlite3

import pytest

from eternal_core.audit.ledger import GENESIS_HASH, Ledger
from orchestration.capability import generate_keypair
from orchestration.checkpoint import (Checkpoint, CheckpointError, CheckpointStore, CheckpointWriter, DirectorySink,
                                      LocalSigner, SignerService, ZERO, key_id_of, verify_checkpoints,
                                      verify_ledger_integrity)
from orchestration.contracts import RunStatus
from orchestration.ledger import RunLedger
from tests.orchestration.helpers import build, final, make_spec, task, tool_req


class Clock:
    t = 5000.0

    def __call__(self):
        return self.t


class Env:
    def __init__(self, tmp_path):
        self.priv, self.pub = generate_keypair()
        self.keys = {key_id_of(self.pub): self.pub}
        self.ledger = RunLedger(str(tmp_path / "run.db"))
        self.service = SignerService(self.priv, str(tmp_path / "signer_state.json"), clock=Clock())
        self.store = CheckpointStore(str(tmp_path / "cp" / "local.jsonl"))
        self.sink = DirectorySink(str(tmp_path / "offbox"))
        self.writer = CheckpointWriter(self.ledger, LocalSigner(self.service), self.store, [self.sink])
        self.db = str(tmp_path / "run.db")

    def fill(self, n, tag="e"):
        for i in range(n):
            self.ledger.append("evt", {"i": i, "tag": tag})

    def verify(self, **kw):
        return verify_checkpoints(self.ledger, self.store.all(), self.keys, off_box=self.sink.fetch_all(), **kw)


def codes(rep):
    return {p.code for p in rep.problems}


def rewrite_chain(db, from_id, new_payload):
    """What an attacker with write access to the database does: edit one entry, then recompute every hash after it so
    that `verify_chain` is satisfied again."""
    con = sqlite3.connect(db)
    rows = con.execute("SELECT id, timestamp, event_type, payload, prev_hash FROM ledger ORDER BY id").fetchall()
    prev = GENESIS_HASH
    for (i, ts, et, payload, _) in rows:
        if i == from_id:
            payload = json.dumps(new_payload, sort_keys=True)
        h = Ledger._compute_hash(prev, ts, et, payload)
        con.execute("UPDATE ledger SET payload=?, prev_hash=?, hash=? WHERE id=?", (payload, prev, h, i))
        prev = h
    con.commit()
    con.close()


# ----------------------------------------------------------------------------------------- happy path
def test_a_clean_ledger_with_checkpoints_verifies_and_chains(tmp_path):
    e = Env(tmp_path)
    e.fill(3)
    c1 = e.writer.write()
    e.fill(2)
    c2 = e.writer.write()
    assert (c1.seq, c1.count, c1.prev) == (1, 3, ZERO) and c2.seq == 2 and c2.prev == c1.digest()
    rep = e.verify()
    assert rep.ok and rep.checked == 2 and rep.latest_seq == 2
    assert [c.seq for c in e.sink.fetch_all()] == [1, 2]


def test_empty_ledger_needs_no_checkpoint_but_a_nonempty_one_does(tmp_path):
    e = Env(tmp_path)
    assert verify_checkpoints(e.ledger, [], e.keys).ok
    e.fill(1)
    assert codes(verify_checkpoints(e.ledger, [], e.keys)) == {"no_checkpoint"}


# --------------------------------------------------------------------------------------- tampering
def test_editing_an_entry_without_fixing_the_chain_is_caught_by_the_chain_itself(tmp_path):
    e = Env(tmp_path)
    e.fill(4)
    e.writer.write()
    con = sqlite3.connect(e.db)
    con.execute("UPDATE ledger SET payload='{\"x\":1}' WHERE id=2")
    con.commit(); con.close()
    assert e.ledger.verify_chain()[0] is False
    assert e.verify().ok                                           # stored hashes unchanged: the checkpoints alone cannot see it
    rep = verify_ledger_integrity(e.ledger, e.store.all(), e.keys, off_box=e.sink.fetch_all())
    assert codes(rep) == {"chain_broken"}                         # ...which is why the Auditor runs BOTH checks


def test_double_tamper_recomputed_chain_passes_verify_chain_but_not_the_checkpoint(tmp_path):     # I13
    e = Env(tmp_path)
    e.fill(5)
    e.writer.write()
    rewrite_chain(e.db, 2, {"forged": True})
    assert e.ledger.verify_chain() == (True, None)               # the attacker's work looks perfect to the chain check
    rep = verify_ledger_integrity(e.ledger, e.store.all(), e.keys, off_box=e.sink.fetch_all())
    assert "ledger_head_mismatch" in codes(rep) and "chain_broken" not in codes(rep) and not rep.ok


def test_truncating_the_ledger_is_caught(tmp_path):
    e = Env(tmp_path)
    e.fill(6)
    e.writer.write()
    con = sqlite3.connect(e.db)
    con.execute("DELETE FROM ledger WHERE id>3")
    con.commit(); con.close()
    assert "ledger_shorter_than_checkpoint" in codes(e.verify())


def test_a_rolled_back_ledger_cannot_get_a_fresh_valid_signature(tmp_path):
    e = Env(tmp_path)
    e.fill(6)
    e.writer.write()
    con = sqlite3.connect(e.db)
    con.execute("DELETE FROM ledger WHERE id>3")
    con.commit(); con.close()
    assert e.writer.write() is None                              # the signer refuses: count went backwards
    assert e.writer.failures and "went backwards" in e.writer.failures[0]
    assert any(x["event_type"] == "checkpoint_failed" for x in e.ledger.recent(10))


def test_forged_edited_or_foreign_checkpoints_fail_signature_check(tmp_path):
    e = Env(tmp_path)
    e.fill(3)
    cp = e.writer.write()
    evil_priv, evil_pub = generate_keypair()
    forged = Checkpoint(cp.seq, cp.ts, cp.head, cp.count, cp.prev, key_id_of(evil_pub))
    assert "unknown_signing_key" in codes(verify_checkpoints(e.ledger, [forged], e.keys))
    edited = Checkpoint(cp.seq, cp.ts, "f" * 64, cp.count, cp.prev, cp.key_id, cp.sig)
    assert "bad_signature" in codes(verify_checkpoints(e.ledger, [edited], e.keys))
    swapped = Checkpoint(cp.seq, cp.ts, cp.head, cp.count, cp.prev, cp.key_id, "AAAA")
    assert "bad_signature" in codes(verify_checkpoints(e.ledger, [swapped], e.keys))


def test_deleting_or_reordering_local_checkpoints_is_caught_and_the_off_box_copy_exposes_it(tmp_path):
    e = Env(tmp_path)
    for _ in range(3):
        e.fill(2)
        e.writer.write()
    cps = e.store.all()
    assert "checkpoint_gap" in codes(verify_checkpoints(e.ledger, [cps[0], cps[2]], e.keys))
    assert "checkpoint_chain_broken" in codes(verify_checkpoints(e.ledger, [cps[0], cps[2]], e.keys))
    # attacker removes the NEWEST local checkpoint (hides a recent rewrite): no gap, but off-box is ahead
    rep = verify_checkpoints(e.ledger, cps[:2], e.keys, off_box=e.sink.fetch_all())
    assert {"local_log_missing_checkpoint", "local_log_truncated"} <= codes(rep)
    # attacker wipes the whole local log
    assert "no_checkpoint" in codes(verify_checkpoints(e.ledger, [], e.keys, off_box=e.sink.fetch_all()))


def test_off_box_disagreement_and_absence(tmp_path):
    e = Env(tmp_path)
    e.fill(2)
    cp = e.writer.write()
    other = Checkpoint(cp.seq, cp.ts + 1, cp.head, cp.count, cp.prev, cp.key_id, cp.sig)
    assert "off_box_mismatch" in codes(verify_checkpoints(e.ledger, [cp], e.keys, off_box=[other]))
    assert "off_box_empty" in codes(verify_checkpoints(e.ledger, [cp], e.keys, off_box=[]))
    assert verify_checkpoints(e.ledger, [cp], e.keys).ok          # off-box not consulted -> not judged


def test_staleness_is_reported_when_entries_pile_up_after_the_last_checkpoint(tmp_path):
    e = Env(tmp_path)
    e.fill(2)
    e.writer.write()
    e.fill(30)
    assert "checkpoint_stale" in codes(e.verify(max_lag_entries=10))
    assert e.verify(max_lag_entries=100).ok


# ------------------------------------------------------------------------------------- signer rules
def test_signer_refuses_arbitrary_inputs_and_enforces_monotonic_rules(tmp_path):
    priv, pub = generate_keypair()
    svc = SignerService(priv, str(tmp_path / "s.json"), clock=Clock())
    h1, h2 = "a" * 64, "b" * 64
    c1 = svc.checkpoint(h1, 5, ZERO)
    with pytest.raises(CheckpointError):
        svc.checkpoint(h2, 6, ZERO)                               # prev must be the last issued digest
    with pytest.raises(CheckpointError):
        svc.checkpoint(h2, 4, c1.digest())                        # backwards
    with pytest.raises(CheckpointError):
        svc.checkpoint(h2, 5, c1.digest())                        # same count, different head
    for bad_head in ("short", "G" * 64, 7, None):
        with pytest.raises(CheckpointError):
            svc.checkpoint(bad_head, 9, c1.digest())
    for bad_count in (-1, "9", True, 1.5):
        with pytest.raises(CheckpointError):
            svc.checkpoint(h2, bad_count, c1.digest())
    assert svc.checkpoint(h1, 5, c1.digest()).seq == 2            # same count + same head is fine (idempotent)
    assert svc.checkpoint(h2, 6, svc._state["digest"]).seq == 3


def test_signer_state_survives_restart_so_a_restart_cannot_reset_the_monotonic_rule(tmp_path):
    priv, _ = generate_keypair()
    path = str(tmp_path / "s.json")
    c = SignerService(priv, path, clock=Clock()).checkpoint("a" * 64, 10, ZERO)
    again = SignerService(priv, path, clock=Clock())
    with pytest.raises(CheckpointError):
        again.checkpoint("b" * 64, 3, c.digest())
    with pytest.raises(CheckpointError):
        again.checkpoint("b" * 64, 11, ZERO)                      # cannot restart the chain from genesis


def test_directory_sink_is_write_once(tmp_path):
    e = Env(tmp_path)
    e.fill(1)
    cp = e.writer.write()
    e.sink.publish(cp)                                            # identical re-publish is fine
    other = Checkpoint(cp.seq, cp.ts, "c" * 64, cp.count, cp.prev, cp.key_id, cp.sig)
    with pytest.raises(CheckpointError):
        e.sink.publish(other)


def test_a_failing_sink_does_not_lose_the_local_checkpoint_but_is_recorded(tmp_path):
    e = Env(tmp_path)

    class Down:
        name = "down"

        def publish(self, cp):
            raise OSError("share unreachable")

        def fetch_all(self):
            return []
    e.writer.sinks.append(Down())
    e.fill(2)
    cp = e.writer.write()
    assert cp is not None and e.store.last().seq == cp.seq
    assert any("sink:down" in f for f in e.writer.failures)


def test_ledger_head_and_hash_at(tmp_path):
    e = Env(tmp_path)
    assert e.ledger.head() == (ZERO, 0) and e.ledger.hash_at(1) is None and e.ledger.hash_at(0) is None
    e.fill(3)
    head, count = e.ledger.head()
    assert count == 3 and e.ledger.hash_at(3) == head and e.ledger.hash_at(4) is None


# -------------------------------------------------------------------------- runner writes checkpoints
def test_runner_signs_a_checkpoint_at_the_end_of_every_run(tmp_path):
    spec = make_spec(("read_doc",))
    runner, _, _, ledger = build(tmp_path, spec, [tool_req("read_doc", {"path": "a"}), final({"answer": "ok"})])
    priv, pub = generate_keypair()
    svc = SignerService(priv, str(tmp_path / "s.json"), clock=Clock())
    store = CheckpointStore(str(tmp_path / "cp.jsonl"))
    runner.checkpoints = CheckpointWriter(ledger, LocalSigner(svc), store)
    assert runner.run(task(), "s1").status == RunStatus.OK
    last = store.last()
    assert last is not None and last.count == ledger.count()
    assert verify_checkpoints(ledger, store.all(), {key_id_of(pub): pub}).ok


def test_a_dead_signer_never_breaks_a_run_but_leaves_a_ledger_trace(tmp_path):
    spec = make_spec(("read_doc",))
    runner, _, _, ledger = build(tmp_path, spec, [final({"answer": "ok"})])

    class Dead:
        def checkpoint(self, *a):
            raise ConnectionRefusedError("signer down")
    runner.checkpoints = CheckpointWriter(ledger, Dead(), CheckpointStore(str(tmp_path / "cp.jsonl")))
    assert runner.run(task(), "s1").status == RunStatus.OK
    assert any(e["event_type"] == "checkpoint_failed" for e in ledger.recent(20))
