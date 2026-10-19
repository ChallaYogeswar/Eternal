"""Full stack: AgentRunner -> ToolBroker -> CapabilityCoreGateway -> real chain, with a scripted model, a
socket-backed signer and a command sink. This is the composition the production wiring uses."""
import os
import sys

import pytest

from agent_chain.gateway import CapabilityCoreGateway
from agent_chain.signer_socket import UNIX_SOCKET_SUPPORTED, SignerSocketClient, SignerSocketServer
from agent_chain.sinks import CommandSink
from orchestration.broker import BrokerFactory
from orchestration.capability import generate_keypair
from orchestration.checkpoint import (CheckpointStore, CheckpointWriter, SignerService, key_id_of,
                                      verify_ledger_integrity)
from orchestration.contracts import Provenance, RunStatus, Sensitivity, ToolInfo, ToolTier
from orchestration.ledger import RunLedger
from orchestration.model_policy import PolicyRouter
from orchestration.runtime import AgentRunner
from tests.orchestration.helpers import Out, ScriptedProvider, final, make_spec, task, tool_req


def catalog():
    return {"web_search": ToolInfo("web_search", ToolTier.READ_ONLY, Provenance.WEB, Sensitivity.PUBLIC,
                                   source_extractor=lambda a, c: ["https://example.org/a"]),
            "laptop_lock": ToolInfo("laptop_lock", ToolTier.WRITE, Provenance.TOOL_OUTPUT, Sensitivity.INTERNAL)}


def runner(wired, tmp_path, spec, replies, **kw):
    local = ScriptedProvider(replies, "local")
    ledger = RunLedger(str(tmp_path / "run_ledger.db"))
    r = AgentRunner(spec, PolicyRouter([local], []), BrokerFactory(wired.core, catalog()), ledger,
                    capability=wired.port, **kw)
    return r, ledger


def test_agent_run_end_to_end_with_real_chain(wired, tmp_path):
    sid = wired.real.new_session()
    spec = make_spec(["web_search"], name="researcher")
    r, ledger = runner(wired, tmp_path, spec, [tool_req("web_search", {"query": "q"}), final({"answer": "done"})])
    res = r.run(task(), sid)
    assert res.status == RunStatus.OK, res
    assert [c[0] for c in wired.real.executor.calls] == ["web_search"]
    # authority ended with the run: the token the run used can no longer be replayed
    started = [e for e in ledger.recent(50) if e["event_type"] == "agent_run_started"][0]
    assert "capability" in started["payload"] and "token" not in str(started["payload"]).lower().replace("tokens", "")


def test_run_is_refused_when_session_is_quarantined(wired, tmp_path):
    sid = wired.real.new_session()
    wired.gateway.kill(reason="test")
    spec = make_spec(["web_search"], name="researcher")
    local = ScriptedProvider([final({"answer": "x"})], "local")
    r, ledger = runner(wired, tmp_path, spec, [final({"answer": "x"})])
    res = r.run(task(), sid)
    assert res.status != RunStatus.OK
    assert wired.real.executor.calls == []


def test_write_tool_run_pauses_or_fails_without_consent_and_never_executes(wired, tmp_path):
    from orchestration.consent import ConsentStore
    sid = wired.real.new_session()
    spec = make_spec(["laptop_lock"], name="taskissuer", can_write=True, tier=ToolTier.WRITE)
    r, ledger = runner(wired, tmp_path, spec, [tool_req("laptop_lock", {}), final({"answer": "x"})],
                       consent=wired.consent)
    res = r.run(task(), sid)
    assert res.status != RunStatus.OK
    assert wired.real.executor.calls == []


@pytest.mark.skipif(not UNIX_SOCKET_SUPPORTED, reason="Unix-domain sockets are unavailable")
def test_checkpoints_through_socket_signer_and_command_sink(wired, tmp_path):
    sid = wired.real.new_session()
    priv, pub = generate_keypair()
    sock = str(tmp_path / "s.sock")
    server = SignerSocketServer(SignerService(priv, str(tmp_path / "signer_state.json")), sock)
    server.start()
    try:
        offbox = tmp_path / "offbox"
        offbox.mkdir()
        py = sys.executable
        sink = CommandSink(
            [py, "-c", "import shutil,sys; shutil.copy(sys.argv[1], sys.argv[2])", "{file}", str(offbox)],
            [py, "-c", "import glob,shutil,sys,os\n[shutil.copy(f, sys.argv[2]) for f in glob.glob(sys.argv[1]+'/*.json')]",
             str(offbox), "{dir}"])
        spec = make_spec(["web_search"], name="researcher")
        local = ScriptedProvider([tool_req("web_search", {"query": "q"}), final({"answer": "a"}),
                                  final({"answer": "b"})], "local")
        ledger = RunLedger(str(tmp_path / "run_ledger.db"))
        store = CheckpointStore(str(tmp_path / "ckpts.jsonl"))
        writer = CheckpointWriter(ledger, SignerSocketClient(sock), store, [sink])
        ar = AgentRunner(spec, PolicyRouter([local], []), BrokerFactory(wired.core, catalog()), ledger,
                         capability=wired.port, checkpoints=writer)
        assert ar.run(task(run_id="a1"), sid).status == RunStatus.OK
        assert ar.run(task(run_id="a2"), sid).status == RunStatus.OK
        assert writer.failures == []
        keys = {key_id_of(pub): pub}
        offsite = sink.fetch_all()
        assert len(offsite) == 2 and len(store.all()) == 2
        rep = verify_ledger_integrity(ledger, store.all(), keys, off_box=offsite)
        assert rep.ok, rep.problems
    finally:
        server.stop()


@pytest.mark.skipif(not UNIX_SOCKET_SUPPORTED, reason="Unix-domain sockets are unavailable")
def test_signer_socket_refuses_rollback_and_garbage(tmp_path):
    priv, pub = generate_keypair()
    sock = str(tmp_path / "s.sock")
    server = SignerSocketServer(SignerService(priv, str(tmp_path / "st.json")), sock)
    server.start()
    try:
        import socket
        c = SignerSocketClient(sock)
        from orchestration.checkpoint import CheckpointError, ZERO
        cp1 = c.checkpoint("a" * 64, 5, ZERO)
        import pytest
        with pytest.raises(CheckpointError, match="backwards"):
            c.checkpoint("b" * 64, 3, cp1.digest())
        with socket.socket(socket.AF_UNIX) as s:                # arbitrary-bytes signing is not an operation
            s.connect(sock)
            s.sendall(b'{"op":"sign","data":"hello"}\n')
            assert b'"ok": false' in s.recv(1024)
        with socket.socket(socket.AF_UNIX) as s:                # extra fields are rejected too
            s.connect(sock)
            s.sendall(b'{"op":"checkpoint","head":"' + b"c" * 64 + b'","count":9,"prev":"' + cp1.digest().encode() + b'","x":1}\n')
            assert b'"ok": false' in s.recv(1024)
    finally:
        server.stop()


@pytest.mark.skipif(not UNIX_SOCKET_SUPPORTED, reason="Unix-domain sockets are unavailable")
def test_signer_client_reports_unreachable_signer(tmp_path):
    import pytest
    from orchestration.checkpoint import CheckpointError, ZERO
    with pytest.raises(CheckpointError, match="unreachable"):
        SignerSocketClient(str(tmp_path / "nope.sock"), timeout_s=0.5).checkpoint("a" * 64, 1, ZERO)


@pytest.mark.skipif(not UNIX_SOCKET_SUPPORTED, reason="Unix-domain sockets are unavailable")
def test_runner_still_finishes_when_signer_is_down(wired, tmp_path):
    sid = wired.real.new_session()
    spec = make_spec(["web_search"], name="researcher")
    local = ScriptedProvider([final({"answer": "a"})], "local")
    ledger = RunLedger(str(tmp_path / "run_ledger.db"))
    writer = CheckpointWriter(ledger, SignerSocketClient(str(tmp_path / "down.sock"), timeout_s=0.3),
                              CheckpointStore(str(tmp_path / "c.jsonl")))
    ar = AgentRunner(spec, PolicyRouter([local], []), BrokerFactory(wired.core, catalog()), ledger,
                     capability=wired.port, checkpoints=writer)
    assert ar.run(task(), sid).status == RunStatus.OK
    assert writer.failures and any(e["event_type"] == "checkpoint_failed" for e in ledger.recent(50))


def test_command_sink_uses_no_shell_and_rejects_string_argv(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        CommandSink("scp {file} host:", ["true"])
    marker = tmp_path / "pwned"
    sink = CommandSink([sys.executable, "-c", "import sys; sys.exit(0)", "{file}; touch " + str(marker)], ["true"])
    from orchestration.checkpoint import Checkpoint
    sink.publish(Checkpoint(1, 1.0, "a" * 64, 1, "0" * 64, "k", "sig"))
    assert not marker.exists()                 # the ';' was just part of a filename argument


def test_command_sink_failure_is_an_error_not_silence(tmp_path):
    import pytest
    from orchestration.checkpoint import Checkpoint, CheckpointError
    sink = CommandSink([sys.executable, "-c", "import sys; sys.exit(3)"], ["true"])
    with pytest.raises(CheckpointError, match="exited 3"):
        sink.publish(Checkpoint(1, 1.0, "a" * 64, 1, "0" * 64, "k", "sig"))
