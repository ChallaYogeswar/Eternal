# Audit fixes (forensic audit, 19 Oct 2026)

Status of each finding after this drop. "Fixed" means changed in code and covered by tests that fail without the fix
(checked by temporarily disabling the fix). "Cannot fix here" means it needs real hardware, credentials or live
systems, so it is labelled honestly instead.

| Finding | Status | What changed |
|---|---|---|
| E-01 self-asserted channel / caller / consent / token routes | **Fixed** for the RCAP, Phase 4, Phase 5, Phase 6 and mounted QAP-P APIs | `api_auth/` package, see below |
| E-02 simulated keys marked hardware-backed | **Fixed** | `qap_p/attestation.py`; `hardware_backed` is set only by a verifier, default fail-closed |
| E-03 live trading unvalidated | **Cannot fix here** | Needs real Kite credentials and market data. Live trading stays disabled; nothing in this drop enables it |
| E-04 real voice recognition | **Cannot fix here** | Whisper/openWakeWord/ECAPA need models and recordings; the seams still raise `NotImplementedError`. Voice remains unable to authorise SENSITIVE actions, now enforced at the API too |
| E-05 laptop control / remote desktop | **Cannot fix here** | Needs a real OS provider and a chosen remote-desktop tool; now labelled `SIMULATED` / `PLACEHOLDER` in `/healthz` |
| E-06 tracker mirrors | **Labelled, not connected** | `/healthz` reports `LOCAL_SQLITE_MIRROR` unless the real ÆTHER API is fully configured (`mcp_servers/integration_status.py`) |
| E-07 web search stub | **Labelled, not connected** | `/healthz` reports `OFFLINE_STUB`; a real provider still has to be wired |
| E-08 no frontend | **Open** | The approval API a phone app would call now exists (`/approvals/*`); the app does not |
| E-09 CI / status evidence | **Fixed** | `.github/workflows/ci.yml`, `constraints.txt`, one canonical test-status statement, machine-specific path removed from `docs/README.md` |
| E-10 paper broker type hint | **Fixed** | `close_position(exit_price: float)` validates positive finite numbers |

## E-01 in detail

* Every protected route needs `Authorization: Bearer <token>`. Tokens map to **principals** with a fixed role:
  `owner_app` (human channels, can confirm approvals, grant consent, trigger the kill switch), `voice_client`
  (channel `voice` only), `automation` (channel `automated`, caller kind `automated_routine`), `readonly`.
* The server stores only SHA-256 of each token. Create them with `scripts/api_auth/manage_tokens.py add --file
  <auth.json> --id phone --role owner_app`, then start the daemons with `ETERNAL_AUTH_FILE=<auth.json>`.
* **Fail closed:** with no principals configured, every protected route returns 503. `/healthz` stays open.
* `confirmation_channel` and `caller_kind` in a request body can only restate what the token already is; claiming
  more is a 403. `granted_by` is no longer accepted: consent records the authenticated principal (`owner@app_tap`).
* SENSITIVE tools need a **separate human approval**: the caller requests one (`POST /approvals/request`), a
  *different* principal on a human channel decides it (`POST /approvals/{id}/decision`), and the caller presents the
  `approval_id` with the call. It is bound to session, tool and exact arguments, single use, and expires. Voice and
  automated callers are refused for SENSITIVE tools outright.
* `POST /qap/vault/tokens` now validates the scope, caps the TTL (`QAP_MAX_TOKEN_TTL`, default 3600 s) and requires an
  existing device. Enrollment, recovery, revive, chaos and token issuance need `qap:admin`.
* Repeated failed logins from one address get HTTP 429 (10 per minute).
* The existing chain still decides afterwards: a valid, approved call can still be refused by consent, ROTATE, the
  trust gate or RCAP.

### Still open in E-01
* `eternal_core/api.py` (chat, RAG) is **not** behind this auth yet; it makes no HTTP tool calls with a claimed channel,
  but keep it on loopback.
* Tokens are bearer secrets over plain HTTP: run behind loopback or Tailscale, ideally with TLS. Per-device
  certificate or request signing is the stronger next step.
* QAP-P soft recovery still takes self-reported booleans (`is_enrolled_device`, `biometric_verified`); it is now
  owner-only, but the booleans themselves are not verified.
* The approval store is in memory; a daemon restart voids pending approvals (safe, but they must be re-requested).
* The phone UI that would show `/approvals/pending` and tap-confirm does not exist (E-08).

## E-02 in detail

`Orchestrator(config, attestation_verifier=...)`. The default `FailClosedVerifier` never verifies, so **no device is
hardware-backed today and the top-trust-tier gate (full laptop control) refuses everyone**. That is the honest state
until a real Android Key Attestation / App Attest / TPM / WebAuthn verifier is written. `QAP_REQUIRE_ATTESTATION=1`
refuses enrollment of unverified devices. Tests use `StaticVerifierForTests` (named so it cannot be mistaken for real).
Every enrollment ledger entry records the verdict and verifier.

## Repository-specific follow-up

The repository-specific setup notes have been applied: the stale `docs/docs/`
snapshot is preserved under `docs/archive/legacy_docs/`, the root README uses a
portable path and constrained dependency install, `.env.example` documents the
new settings, and `secrets/` is ignored by Git. A signed commit can be used if
provenance is required.
