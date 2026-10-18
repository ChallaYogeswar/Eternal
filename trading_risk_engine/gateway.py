"""
Phase 6 gateway.

Composes device_control_gateway.gateway.Phase5Gateway rather than modifying it or
RCAP-P/Phase4's gateways underneath it — the exact pattern every phase
since Phase 4 has used. Two things are genuinely NEW here, and both are
specific to `trading_place_live_order`; everything else this phase's
tools need is inherited automatically, for free, by virtue of being
registered in the shared registry (trading_risk_engine/registry_ext.py) and routed
through the SAME call_tool/grant_tool_permission path every earlier
phase's tools already use:

- Kill-switch coverage: Phase5Gateway.grant_tool_permission already
  refuses to mint a fresh ROTATE token for any non-ACTIVE session,
  regardless of tool_name (checked directly against that method's source
  before writing this module — it applies no tool_name filter at all).
  A live order requires a ROTATE token like every other WRITE/SENSITIVE
  tool, so pulling Phase 5's kill switch already blocks live trading
  system-wide. No new code needed for this; re-implementing it here
  would be duplicating a guarantee that already exists.
- Non-voice confirmation + per-tool consent: `trading_place_live_order`
  is registered SENSITIVE tier, and voice_consent_gateway.gateway.Phase4Gateway already
  runs its voice-gate and consent checks unconditionally, before this
  gateway is ever reached, for every SENSITIVE tool regardless of which
  phase registered it.

What's genuinely new:

1. Top trust tier — build plan Section 7.1 names "trade execution" in
   the same breath as "full laptop control" as needing this SAME extra
   bar beyond ordinary SENSITIVE-tier gating. This class reuses
   device_control_gateway.trust_tier.TopTrustTierGate directly (same class, same
   threshold field, same qap_p.trust.gates_sensitive_tier underneath) —
   NOT a re-implementation with a different number, a literal shared
   instance wired to the SAME trust_lookup Phase 5 already uses.
2. The regulatory gate chain (trading_risk_engine/regulatory.py's LiveTradingGate) —
   fixed-IP host role, daily Kite re-auth, order rate limit, and market-
   order protection. These are trading-specific rules that exist nowhere
   else in the stack.
"""
from typing import TYPE_CHECKING

from voice_consent_gateway.models import ConfirmationChannel
from device_control_gateway.gateway import Phase5Gateway
from device_control_gateway.trust_tier import TopTrustTierGate

from .ledger_bridge import Ledger
from .models import OrderType
from .regulatory import LiveTradingGate

if TYPE_CHECKING:
    from voice_consent_gateway.models import WriteCallRequest, WriteCallResult

# A set, not a single name, on purpose: Phase 6 Extended (F&O) adds two
# MORE tools with real, irreversible, real-money consequences
# (fo_place_live_option_order, fo_place_live_future_order). Checking
# membership in this set — rather than equality against one hardcoded
# name — is what makes sure those two get the SAME top-trust-tier +
# regulatory-gate treatment automatically; changing this to a set (from
# equality against LIVE_ORDER_TOOL_NAME) is the one code change this
# gateway needed for F&O, everything else about the F&O tools is
# covered by the existing registry/voice-gate/kill-switch machinery
# this module's own docstring already explains.
LIVE_ORDER_TOOL_NAMES = {
    "trading_place_live_order", "fo_place_live_option_order", "fo_place_live_future_order",
}
# Backward-compatible alias — tests/trading_risk_engine/test_gateway.py (written before
# the F&O extension existed) imports the old singular name directly. Kept
# so that pre-existing, already-passing test is not touched; the gateway
# logic itself only ever checks LIVE_ORDER_TOOL_NAMES above.
LIVE_ORDER_TOOL_NAME = "trading_place_live_order"


class Phase6Gateway:
    def __init__(
        self,
        phase5_gateway: Phase5Gateway,
        top_trust_tier_gate: TopTrustTierGate,
        live_trading_gate: LiveTradingGate,
        ledger: Ledger,
    ):
        self.phase5_gateway = phase5_gateway
        self.top_trust_tier_gate = top_trust_tier_gate
        self.live_trading_gate = live_trading_gate
        self.ledger = ledger

    async def call_tool(self, request: "WriteCallRequest") -> "WriteCallResult":
        from voice_consent_gateway.models import WriteCallResult

        if request.tool_name in LIVE_ORDER_TOOL_NAMES:
            trust_decision = self.top_trust_tier_gate.check(request.session_id)
            if not trust_decision.granted:
                h = self.ledger.append("phase6_top_trust_tier_denied", {
                    "tool_name": request.tool_name, "session_id": request.session_id,
                    "confirmation_channel": request.confirmation_channel.value,
                    "reason": trust_decision.reason, "trust_score": trust_decision.trust_score,
                })
                return WriteCallResult(
                    tool_name=request.tool_name, ok=False, content=None,
                    denied_reason=trust_decision.reason, underlying_result=None, ledger_entry_hashes=[h],
                )

            order_type_raw = str(request.arguments.get("order_type", "LIMIT")).upper()
            try:
                order_type = OrderType(order_type_raw)
            except ValueError:
                order_type = OrderType.LIMIT
            market_protection_pct = request.arguments.get("market_protection_pct")

            gate_result = self.live_trading_gate.check_before_order(order_type, market_protection_pct)
            if not gate_result.allowed:
                h = self.ledger.append("phase6_live_trading_gate_denied", {
                    "tool_name": request.tool_name, "session_id": request.session_id,
                    "order_type": order_type.value, "reason": gate_result.reason,
                })
                return WriteCallResult(
                    tool_name=request.tool_name, ok=False, content=None,
                    denied_reason=gate_result.reason, underlying_result=None, ledger_entry_hashes=[h],
                )

            self.ledger.append("phase6_live_trading_gates_cleared", {
                # F&O live orders use underlying_symbol/num_lots, not equity's
                # symbol/quantity — capture either shape so this entry stays meaningful.
                "session_id": request.session_id, "tool_name": request.tool_name,
                "symbol": request.arguments.get("symbol") or request.arguments.get("underlying_symbol"),
                "side": request.arguments.get("side"),
                "quantity": request.arguments.get("quantity") or request.arguments.get("num_lots"),
                "order_type": order_type.value, "trust_score": trust_decision.trust_score,
            })

        return await self.phase5_gateway.call_tool(request)

    def grant_tool_permission(self, session_id: str, tool_name: str) -> str:
        """Pure passthrough — Phase5Gateway.grant_tool_permission's
        ACTIVE-session guard already covers every tool_name, including
        trading_place_live_order, with no changes needed here. Kept as a
        method (rather than having callers reach past this gateway to
        phase5_gateway directly) purely so every call in a real
        deployment goes through exactly one object, matching how
        Phase 5 itself relates to Phase 4."""
        return self.phase5_gateway.grant_tool_permission(session_id, tool_name)
