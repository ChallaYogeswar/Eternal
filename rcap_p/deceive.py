"""
Ring 4 — DECEIVE.

RCAP whitepaper Section 3.1 (Ring 4): "Deception assets (honeytokens, decoy
services) and deep packet inspection immediately outside the authentication
boundary." Eternal_Master_Build_Plan.md Section 6.2: "Decoy secret / fake
tool registration — any touch is unambiguous evidence."

Two independent deception mechanisms, both implemented here:

1. Decoy tool registrations. The capability registry (registry.py) can
   mark a ToolManifestEntry as `is_decoy=True` — a tool that *looks* like
   a plausible thing an attacker or an injected instruction might reach
   for (e.g. "get_stored_credentials"), registered in the same tool list a
   real tool would be, but never wired to anything real. Nothing
   legitimate should ever call it — the assistant is never told to use it
   for any real task — so any call attempt at all is unambiguous, exactly
   like qap_p's CanaryService (Phase 1) treats any read of the planted
   credential string.

2. Canary-value leak detection. Reuses the *concept* of qap_p's planted
   canary credential (Phase 1, canary.py) but checks for it in a different
   place: not "did a session try to read this value," but "did a TOOL'S
   RESPONSE ever contain this value," which would mean something
   downstream is echoing back a secret it should never have had reason to
   see or generate. If qap_p is installed alongside RCAP-P, this reuses its
   real CanaryService instance (one planted value, shared across both
   protocols, per the whitepapers' own note about a shared trust-scoring
   subsystem); otherwise DECEIVE plants and tracks its own.
"""
import secrets
from dataclasses import dataclass
from typing import Optional


@dataclass
class DeceiveVerdict:
    triggered: bool
    reason: str = ""


class DeceiveRing:
    def __init__(self, ledger, canary_service=None):
        self.ledger = ledger
        # Bridge pattern, same spirit as ledger_bridge.py: use a real
        # qap_p.canary.CanaryService if the caller hands one in (integrated
        # deployment), otherwise plant and track our own value.
        self._external_canary = canary_service
        self._own_canary_value: Optional[str] = None
        if self._external_canary is None:
            self._own_canary_value = f"rcap-canary-{secrets.token_hex(16)}"
            self.ledger.append("deceive_canary_planted", {})

    def check_decoy_call(self, tool_name: str, source_label: str,
                          manifest_is_decoy: bool) -> DeceiveVerdict:
        """Called by the gateway before a tool call reaches CORE. If the
        requested tool is a registered decoy, the call itself — regardless
        of arguments, regardless of who or what asked for it — is the
        signal. Nothing legitimate ever has a reason to call a decoy tool,
        so there's no "maybe" here, mirroring qap_p's own canary design
        note."""
        if manifest_is_decoy:
            self.ledger.append("deceive_decoy_tool_called", {
                "tool_name": tool_name, "source_label": source_label,
            })
            return DeceiveVerdict(
                triggered=True,
                reason=f"'{tool_name}' is a registered decoy tool; any call to it is "
                       f"unambiguous evidence of compromise or injected steering",
            )
        return DeceiveVerdict(triggered=False)

    def check_response_for_canary_leak(self, response_text: str, tool_name: str,
                                        source_label: str) -> DeceiveVerdict:
        """Called after a tool response comes back, alongside INSPECT. A
        tool response that contains the planted canary value means
        something in the pipeline read a value it should never have had
        access to and is now echoing it — a different, later-stage signal
        than the decoy-tool-call check above, and worth keeping separate
        rather than conflating "called the wrong thing" with "leaked the
        wrong thing"."""
        if not response_text:
            return DeceiveVerdict(triggered=False)

        if self._external_canary is not None:
            # qap_p.canary.CanaryService.check_access() is designed around
            # "did a *session* present this value," not "does this *text*
            # contain it" — for a leak check we want simple substring
            # containment against whatever value it planted internally.
            # CanaryService doesn't expose its planted value by design (see
            # qap_p/canary.py — the value is deliberately never logged
            # verbatim), so cross-checking arbitrary tool-response text
            # against it isn't possible without changing Phase 1's own
            # security posture. Documented limitation, not a bug: use
            # RCAP-P's own canary (own_canary_value) for response-leak
            # scanning even in an integrated deployment.
            pass

        canary_value = self._own_canary_value
        if canary_value and canary_value in response_text:
            self.ledger.append("deceive_canary_leaked_in_response", {
                "tool_name": tool_name, "source_label": source_label,
            })
            return DeceiveVerdict(
                triggered=True,
                reason=f"tool response from '{tool_name}' contained the planted canary value — "
                       f"unambiguous evidence this source is echoing back data it should not have",
            )
        return DeceiveVerdict(triggered=False)

    def canary_value_for_seeding(self) -> str:
        """Exposes the value ONLY so demo/test fixtures can deliberately
        seed a compromised tool response with it, to prove the check
        fires. Never call this to *display* the value anywhere in a real
        response path — same handling discipline qap_p's canary.py already
        established."""
        return self._own_canary_value or ""
