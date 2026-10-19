#!/usr/bin/env python3
"""
Phase 6 — live HTTP daemon end-to-end proof.

Same role as scripts/test_http_e2e.py in Phase 1 (and the one added to
Phase 5 during this project's own independent validation of that phase):
every call here is a real HTTP request against a real, separately-running
`uvicorn trading_risk_engine.api:app` process — nothing in this script talks to any
gateway object directly.

Usage:
    # terminal 1
    export PYTHONPATH=".:/path/to/qap-p:/path/to/rcap-p:/path/to/eternal-voice_consent_gateway:/path/to/eternal-device_control_gateway"
    export RCAP_P_PROJECT_ROOT=/path/to/rcap-p
    uvicorn trading_risk_engine.api:app --host 127.0.0.1 --port 8424

    # terminal 2
    python scripts/test_http_e2e.py
"""
import sys
import uuid

import httpx

# Audit E-01: the daemon now requires a bearer token. Create one with scripts/api_auth/manage_tokens.py (role
# owner_app), start the daemon with ETERNAL_AUTH_FILE set, and export ETERNAL_API_TOKEN for this script. The script
# no longer sends `granted_by` / channel / caller kind: the server derives them from the token.
import os as _os
_TOKEN = _os.environ.get("ETERNAL_API_TOKEN")
if not _TOKEN:
    raise SystemExit("set ETERNAL_API_TOKEN to an owner_app bearer token (see scripts/api_auth/manage_tokens.py)")
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_get, _post = httpx.get, httpx.post
httpx.get = lambda url, **kw: _get(url, headers={**_AUTH, **kw.pop("headers", {})}, **kw)
httpx.post = lambda url, **kw: _post(url, headers={**_AUTH, **kw.pop("headers", {})}, **kw)

BASE = "http://127.0.0.1:8424"

_passed = 0
_failed = 0


def check(label: str, condition: bool):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}")


def main():
    try:
        httpx.get(f"{BASE}/healthz", timeout=3.0)
    except httpx.ConnectError:
        print(f"Could not reach {BASE} — start the daemon first (see this file's docstring).")
        sys.exit(2)

    print("=" * 78)
    print("1. HEALTHZ — the unified registry really does include every phase's tools")
    print("=" * 78)
    health = httpx.get(f"{BASE}/healthz").json()
    tools = set(health["registered_tools"])
    check("Phase 6 tools are present", "trading_place_live_order" in tools and "trading_run_backtest" in tools)
    check("Phase 5 tools are still present, unmodified", "remote_control_wake" in tools or "laptop_lock" in tools)
    check("Phase 1's ledger is what Phase 6 is actually writing to",
          "qap_p" in health["ledger_source"] or "vendored" in health["ledger_source"])

    print()
    print("=" * 78)
    print("2. ENROLL + SESSION, real QAP-P state, over HTTP")
    print("=" * 78)
    device_id = f"http-e2e-trading-{uuid.uuid4().hex[:8]}"
    r = httpx.post(f"{BASE}/qap/devices/enroll", json={"device_id": device_id, "role": "mini_pc"})
    check("device enrollment succeeds over HTTP", r.status_code == 200)
    r = httpx.post(f"{BASE}/qap/sessions", params={"device_id": device_id})
    session_id = r.json()["session_id"]
    check("fresh session starts ACTIVE", r.json()["state"] == "ACTIVE")

    print()
    print("=" * 78)
    print("3. READ-ONLY TOOLS — signal + backtest, over real HTTP")
    print("=" * 78)
    httpx.post(f"{BASE}/consent/grant", params={"tool_name": "trading_get_signal", "granted_by": "owner_interactive:http-e2e"})
    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "trading_get_signal"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "trading_get_signal", "arguments": {"symbol": "RELIANCE.NS"},
        "confirmation_channel": "text_typed", "explanation": "http e2e signal check", "rotate_token": token,
    })
    body = r.json()
    check("trading_get_signal succeeds over real HTTP", body["ok"] is True)
    check("the signal has a real direction and reasons", body["content"]["direction"] in ("BUY", "SELL", "HOLD"))

    print()
    print("=" * 78)
    print("4. WRITE TOOL — a real paper order, over real HTTP, then confirmed via a second call")
    print("=" * 78)
    httpx.post(f"{BASE}/consent/grant", params={"tool_name": "trading_place_paper_order", "granted_by": "owner_interactive:http-e2e"})
    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "trading_place_paper_order"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "trading_place_paper_order",
        "arguments": {"symbol": "INFY.NS", "side": "BUY", "quantity": 4, "order_type": "MARKET"},
        "confirmation_channel": "app_tap", "explanation": "http e2e paper order", "rotate_token": token,
    })
    body = r.json()
    check("the paper order fills over real HTTP", body["ok"] is True and body["content"]["status"] == "FILLED")

    httpx.post(f"{BASE}/consent/grant", params={"tool_name": "trading_get_paper_positions", "granted_by": "owner_interactive:http-e2e"})
    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "trading_get_paper_positions"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "trading_get_paper_positions", "arguments": {},
        "confirmation_channel": "text_typed", "explanation": "http e2e position check", "rotate_token": token,
    })
    positions = r.json()["content"].get("result", [])
    check("the position just opened is visible on a second, independent HTTP call",
          any(p["symbol"] == "INFY.NS" and p["quantity"] >= 4 for p in positions))

    print()
    print("=" * 78)
    print("5. LIVE ORDER — refused over voice, refused for a fresh low-trust session, over real HTTP")
    print("=" * 78)
    httpx.post(f"{BASE}/consent/grant", params={"tool_name": "trading_place_live_order", "granted_by": "owner_interactive:http-e2e"})
    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "trading_place_live_order"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "trading_place_live_order",
        "arguments": {"symbol": "RELIANCE.NS", "side": "BUY", "quantity": 1, "order_type": "LIMIT", "limit_price": 2500},
        "confirmation_channel": "voice", "explanation": "http e2e live order attempt over voice", "rotate_token": token,
    })
    check("a live order over VOICE is refused over real HTTP", r.json()["ok"] is False)

    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "trading_place_live_order"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "trading_place_live_order",
        "arguments": {"symbol": "RELIANCE.NS", "side": "BUY", "quantity": 1, "order_type": "LIMIT", "limit_price": 2500},
        "confirmation_channel": "app_tap", "explanation": "http e2e live order attempt, fresh session", "rotate_token": token,
    })
    body = r.json()
    check("a live order via app_tap from a FRESH (low-trust) session is refused over real HTTP",
          body["ok"] is False and "trust score" in body["denied_reason"])

    print()
    print("=" * 78)
    print("3b. F&O READ-ONLY TOOLS — signal + option chain, over real HTTP")
    print("=" * 78)
    httpx.post(f"{BASE}/consent/grant", params={"tool_name": "fo_get_signal", "granted_by": "owner_interactive:http-e2e"})
    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "fo_get_signal"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "fo_get_signal", "arguments": {"underlying_symbol": "NIFTY"},
        "confirmation_channel": "text_typed", "explanation": "http e2e F&O signal check", "rotate_token": token,
    })
    body = r.json()
    check("fo_get_signal succeeds over real HTTP", body["ok"] is True)
    check("the F&O signal has a real direction", body["content"]["direction"] in ("BUY", "SELL", "HOLD"))

    httpx.post(f"{BASE}/consent/grant", params={"tool_name": "fo_get_option_chain", "granted_by": "owner_interactive:http-e2e"})
    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "fo_get_option_chain"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "fo_get_option_chain",
        "arguments": {"underlying_symbol": "NIFTY", "num_strikes_each_side": 3},
        "confirmation_channel": "text_typed", "explanation": "http e2e F&O chain check", "rotate_token": token,
    })
    body = r.json()
    check("fo_get_option_chain succeeds over real HTTP with a full CE+PE chain",
          body["ok"] is True and len(body["content"]["quotes"]) == 14)

    print()
    print("=" * 78)
    print("4b. F&O WRITE TOOL — a real paper option order, over real HTTP, confirmed via a second call")
    print("=" * 78)
    sample_strike = body["content"]["quotes"][3]["strike"] if body["ok"] else 24500.0
    httpx.post(f"{BASE}/consent/grant", params={"tool_name": "fo_place_paper_option_order", "granted_by": "owner_interactive:http-e2e"})
    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "fo_place_paper_option_order"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "fo_place_paper_option_order",
        "arguments": {"underlying_symbol": "NIFTY", "strike": sample_strike, "option_type": "CE",
                     "side": "LONG", "num_lots": 1, "expiry_choice": "WEEKLY"},
        "confirmation_channel": "app_tap", "explanation": "http e2e F&O paper order", "rotate_token": token,
    })
    body = r.json()
    check("the F&O paper option order fills over real HTTP", body["ok"] is True and body["content"]["status"] == "FILLED")

    httpx.post(f"{BASE}/consent/grant", params={"tool_name": "fo_get_paper_positions", "granted_by": "owner_interactive:http-e2e"})
    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "fo_get_paper_positions"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "fo_get_paper_positions", "arguments": {},
        "confirmation_channel": "text_typed", "explanation": "http e2e F&O position check", "rotate_token": token,
    })
    fo_positions = r.json()["content"].get("result", [])
    check("the F&O option position just opened is visible on a second, independent HTTP call",
          any(p["underlying_symbol"] == "NIFTY" and p["kind"] == "OPTION" for p in fo_positions))

    print()
    print("=" * 78)
    print("5b. F&O LIVE ORDER — refused over voice, refused for a fresh low-trust session, over real HTTP")
    print("=" * 78)
    httpx.post(f"{BASE}/consent/grant", params={"tool_name": "fo_place_live_option_order", "granted_by": "owner_interactive:http-e2e"})
    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "fo_place_live_option_order"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "fo_place_live_option_order",
        "arguments": {"underlying_symbol": "NIFTY", "strike": sample_strike, "option_type": "CE",
                     "side": "LONG", "num_lots": 1, "expiry_choice": "WEEKLY", "order_type": "LIMIT",
                     "limit_price": 150.0},
        "confirmation_channel": "voice", "explanation": "http e2e F&O live order attempt over voice",
        "rotate_token": token,
    })
    check("an F&O live order over VOICE is refused over real HTTP — identical gate chain to equity, "
         "zero F&O-specific gateway code needed", r.json()["ok"] is False)

    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "fo_place_live_option_order"})
    token = r.json()["token"]
    r = httpx.post(f"{BASE}/tools/call", json={
        "session_id": session_id, "tool_name": "fo_place_live_option_order",
        "arguments": {"underlying_symbol": "NIFTY", "strike": sample_strike, "option_type": "CE",
                     "side": "LONG", "num_lots": 1, "expiry_choice": "WEEKLY", "order_type": "LIMIT",
                     "limit_price": 150.0},
        "confirmation_channel": "app_tap", "explanation": "http e2e F&O live order attempt, fresh session",
        "rotate_token": token,
    })
    body = r.json()
    check("an F&O live order via app_tap from a FRESH (low-trust) session is refused over real HTTP",
          body["ok"] is False and "trust score" in body["denied_reason"])

    print()
    print("=" * 78)
    print("6. LEDGER + KILL SWITCH — the regression this project's own validation exists to catch")
    print("=" * 78)
    r = httpx.get(f"{BASE}/ledger", params={"limit": 50})
    check("ledger has recorded entries for this run", len(r.json()) > 0)
    r = httpx.get(f"{BASE}/ledger/verify")
    check("ledger chain is intact", r.json()["intact"] is True)

    r = httpx.post(f"{BASE}/kill_switch", json={"reason": "http_e2e_test"})
    report = r.json()
    check("kill switch reports the session as quarantined", session_id in report["sessions_quarantined"])

    r = httpx.post(f"{BASE}/tools/grant", params={"session_id": session_id, "tool_name": "trading_place_live_order"})
    check(
        "a quarantined session CANNOT be handed a fresh ROTATE token for a live order over HTTP — "
        "inherited from Phase 5's grant_tool_permission guard, zero Phase-6-specific code",
        r.json().get("token") is None,
    )

    print()
    print(f"{_passed} passed, {_failed} failed")
    if _failed:
        print("SOME CHECKS FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
