"""Audit E-01 regression tests against the REAL daemons (FastAPI apps), each in its own subprocess because the daemons
build their gateways at import time from the current directory. The scenario is in `_scenario.py`."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCENARIO = os.path.join(os.path.dirname(__file__), "_scenario.py")


def run(module, sensitive="", mode="configured", tmp=None):
    code = ("import sys; sys.path.insert(0, %r); sys.argv=['x']+sys.argv[1:]; exec(open(%r).read())"
            % (ROOT, SCENARIO))
    p = subprocess.run([sys.executable, "-I", "-c", code, module, sensitive, mode], cwd=tmp, capture_output=True,
                       text=True, timeout=180)
    for line in p.stdout.splitlines():
        if line.startswith("RESULT="):
            return {k: v for k, v in json.loads(line[7:]).items()}
    raise AssertionError(f"scenario failed:\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")


DAEMONS = [("device_control_gateway.api", "ha_unlock"), ("trading_risk_engine.api", "ha_unlock"),
           ("voice_consent_gateway.api", ""),   # its real registry has no SENSITIVE tool
           ("rcap_p.api", "")]


@pytest.fixture(scope="module", params=DAEMONS, ids=[d[0] for d in DAEMONS])
def obs(request, tmp_path_factory):
    module, sens = request.param
    o = run(module, sens, tmp=str(tmp_path_factory.mktemp("daemon")))
    o["_module"], o["_sens"] = module, sens
    return o


def st(o, name):
    return o[name]["status"]


def test_health_is_open_everything_else_needs_a_token(obs):
    assert st(obs, "healthz") == 200
    for name in ("ledger_noauth", "call_noauth", "call_badtoken", "grant_noauth"):
        assert st(obs, name) == 401, name


def test_readonly_principal_cannot_call_or_grant(obs):
    assert st(obs, "call_ro") == 403 and st(obs, "grant_ro") == 403
    assert st(obs, "ledger_ro") == 200


def test_authenticated_callers_reach_the_gateway(obs):
    for who in ("voice", "auto", "owner"):
        assert st(obs, f"call_{who}") == 200, who


def test_body_cannot_claim_a_stronger_channel_or_caller_kind(obs):
    """The core of E-01: changing JSON fields alone must not elevate a caller."""
    names = ["auto_claims_owner"]
    if obs["_module"] != "rcap_p.api":                     # RCAP-P's own API has no channel field at all
        names += ["voice_claims_typed", "voice_claims_tap", "auto_claims_tap"]
    for name in names:
        assert st(obs, name) == 403, name


def test_consent_grant_is_owner_only_and_grantor_is_server_derived(obs):
    if obs["_module"] == "rcap_p.api":
        pytest.skip("RCAP-P daemon has no consent routes")
    assert st(obs, "consent_noauth") == 401
    assert st(obs, "consent_voice") == 403 and st(obs, "consent_auto") == 403
    assert st(obs, "consent_owner") == 200
    assert obs["consent_owner"]["body"]["granted_by"] == "owner@app_tap"
    assert obs["consent_owner_claims_grantor"]["body"]["granted_by"] == "owner@app_tap"    # claimed grantor ignored


def test_kill_switch_needs_the_owner(obs):
    if obs["_module"] in ("rcap_p.api", "voice_consent_gateway.api"):
        pytest.skip("no kill switch on this daemon")
    assert st(obs, "kill_noauth") == 401 and st(obs, "kill_voice") == 403 and st(obs, "kill_auto") == 403


def test_sensitive_actions_need_a_separate_human_confirmation(obs):
    if not obs["_sens"]:
        pytest.skip("no SENSITIVE tool on this daemon")
    assert st(obs, "sens_owner_noapproval") == 403
    assert st(obs, "sens_auto") == 403 and st(obs, "sens_voice") == 403          # never over voice or automated
    assert st(obs, "approval_pending_voice") == 403                               # voice cannot even list approvals
    assert st(obs, "approval_self_confirm") == 403                                # requester cannot confirm itself
    assert st(obs, "sens_owner_unconfirmed") == 403
    assert st(obs, "approval_voice_confirm") == 403
    assert st(obs, "approval_owner2_confirm") == 200                              # a different human can
    assert st(obs, "sens_other_args") == 403                                      # bound to exact arguments
    assert st(obs, "sens_owner_approved") == 200                                  # now it reaches the gateway chain
    assert st(obs, "sens_owner_reuse") == 403                                     # single use


def test_the_gateway_chain_still_decides_after_authentication(obs):
    """A valid, approved call can still be refused by the existing chain (consent, ROTATE, trust)."""
    if not obs["_sens"]:
        pytest.skip("no SENSITIVE tool on this daemon")
    assert obs["sens_owner_approved"]["body"]["ok"] is False


def test_qap_routes_are_protected_and_token_issuance_is_validated(obs):
    if obs["_module"] not in ("device_control_gateway.api", "trading_risk_engine.api"):
        pytest.skip("QAP-P is mounted only on the Phase 5/6 daemons")
    assert st(obs, "qap_enroll_noauth") == 401 and st(obs, "qap_enroll_voice") == 403
    assert st(obs, "qap_enroll_owner") == 200
    assert st(obs, "qap_token_noauth") == 401 and st(obs, "qap_token_auto") == 403
    assert st(obs, "qap_token_badscope") == 400 and st(obs, "qap_token_longttl") == 400
    assert st(obs, "qap_token_unknown_dev") == 404 and st(obs, "qap_token_ok") == 200
    assert st(obs, "qap_chaos_auto") == 403 and st(obs, "qap_session_ro") == 403
    assert st(obs, "qap_ledger_noauth") == 401 and st(obs, "qap_ledger_ro") == 200


@pytest.mark.parametrize("module", [d[0] for d in DAEMONS])
def test_fail_closed_when_no_principals_are_configured(module, tmp_path):
    o = run(module, "", mode="none", tmp=str(tmp_path))
    assert st(o, "healthz") == 200
    for name in ("call_owner", "ledger_ro", "grant_ro"):
        assert st(o, name) == 503, name


def test_healthz_labels_mirrors_stubs_and_simulations(obs):
    if obs["_module"] == "rcap_p.api":
        pytest.skip("RCAP-P daemon health is minimal")
    body = obs["healthz"]["body"]
    assert "STUB" in body["integration_modes"]["web_search"] and "MIRROR" in body["integration_modes"]["aether"]
    assert body["auth_configured"] is True
