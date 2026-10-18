"""
Phase 6 registry extension.

Same composition pattern every phase since Phase 4 has used: build_registry()
returns Phase 5's own registry (unmodified, imported directly) plus Phase
6's new trading_engine tools. Phase 6 introduces no new demo-only fixture
of its own, so build_demo_registry() simply carries Phase 3/4/5's existing
demo fixtures forward unchanged, for the same reason Phase 5's own
registry_ext.py did.

Tiering rationale (build plan Section 7.2 + Section 8), summarized here
for one-place reference; mcp_servers/trading_engine/server.py's own tool
docstrings carry the same reasoning locally:

  trading_get_quote                 READ_ONLY   price lookup only
  trading_get_signal                READ_ONLY   a rule-based opinion, no state change
  trading_run_backtest               READ_ONLY   pure computation, touches no real or paper account
  trading_check_recovery_math        READ_ONLY   pure arithmetic
  trading_get_paper_positions        READ_ONLY   state lookup only
  trading_get_paper_margins          READ_ONLY   state lookup only
  trading_daily_reauth_status        READ_ONLY   state lookup only
  trading_ip_change_history          READ_ONLY   state lookup only
  trading_place_paper_order          WRITE       real accounting, but no real-world consequence
  trading_run_paper_trading_day      WRITE       real accounting, but no real-world consequence
  trading_record_ip_change           WRITE       tracking only, never itself blocks an order
  trading_place_live_order           SENSITIVE   real money, real exchange, irreversible — build plan
                                                  Section 7.2: "never voice-triggered, always the
                                                  phone-tap confirmation" — gets the SAME additional
                                                  top-trust-tier check device_control_gateway/gateway.py already applies
                                                  to remote_control_request_full_session, PLUS every
                                                  regulatory gate in trading_risk_engine/regulatory.py, both enforced
                                                  in trading_risk_engine/gateway.py before this tool is ever reached.

Phase 6 EXTENDED (F&O — Index Options + Index Futures) tiering, same
rationale pattern, one row per new tool in
mcp_servers/trading_engine/server.py:

  fo_get_option_chain                READ_ONLY   simulated price lookup only
  fo_get_signal                      READ_ONLY   a rule-based opinion, no state change
  fo_run_options_backtest            READ_ONLY   pure computation, touches no real or paper account
  fo_run_futures_backtest            READ_ONLY   pure computation, touches no real or paper account
  fo_get_paper_positions             READ_ONLY   state lookup only
  fo_get_paper_margins               READ_ONLY   state lookup only
  fo_place_paper_option_order        WRITE       real accounting, but no real-world consequence
  fo_place_paper_future_order        WRITE       real accounting, but no real-world consequence
  fo_close_paper_position            WRITE       real accounting, but no real-world consequence
  fo_place_live_option_order         SENSITIVE   real money, real exchange, irreversible — identical
  fo_place_live_future_order         SENSITIVE   gate chain to trading_place_live_order (see gateway.py's
                                                  LIVE_ORDER_TOOL_NAMES, which now covers all three).
"""
from typing import Dict

from device_control_gateway.registry_ext import build_demo_registry as build_phase5_demo_registry
from device_control_gateway.registry_ext import build_registry as build_phase5_registry
from rcap_p.models import ToolManifestEntry, ToolSensitivity


def _phase6_entries() -> Dict[str, ToolManifestEntry]:
    read_only_specs = [
        ("trading_get_quote", "Get the latest price for a symbol."),
        ("trading_get_signal", "Get the current rule-based BUY/SELL/HOLD signal for a symbol."),
        ("trading_run_backtest", "Run a full historical backtest over one or more symbols."),
        ("trading_check_recovery_math", "Compute the real percentage gain needed to recover a given loss."),
        ("trading_get_paper_positions", "List currently open paper-trading positions."),
        ("trading_get_paper_margins", "Get the paper-trading cash balance."),
        ("trading_daily_reauth_status", "Check whether today's Kite Connect login is still valid."),
        ("trading_ip_change_history", "View the recorded order-placement host IP change history."),
        # -- F&O (Index Options + Index Futures) --
        ("fo_get_option_chain", "Get a simulated index option chain (CE+PE, priced via Black-Scholes-Merton)."),
        ("fo_get_signal", "Get the current rule-based BUY/SELL/HOLD signal for an index underlying."),
        ("fo_run_options_backtest", "Run a historical backtest of an index options strategy."),
        ("fo_run_futures_backtest", "Run a historical backtest of an index futures strategy."),
        ("fo_get_paper_positions", "List currently open F&O paper-trading positions."),
        ("fo_get_paper_margins", "Get the F&O paper-trading cash/margin balances."),
    ]
    write_specs = [
        ("trading_place_paper_order", "Place a simulated (paper) order — no real money."),
        ("trading_run_paper_trading_day", "Advance the paper-trading simulation by one trading day."),
        ("trading_record_ip_change", "Record the current order-placement host's IP for tracking."),
        # -- F&O (Index Options + Index Futures) --
        ("fo_place_paper_option_order", "Place a simulated (paper) index option order — no real money."),
        ("fo_place_paper_future_order", "Place a simulated (paper) index future order — no real money."),
        ("fo_close_paper_position", "Close an open F&O paper position and realize its P&L."),
    ]

    entries = {
        name: ToolManifestEntry(tool_name=name, server_key="trading_engine", description=desc,
                                sensitivity=ToolSensitivity.READ_ONLY, rate_limit_per_minute=60)
        for name, desc in read_only_specs
    }
    entries.update({
        name: ToolManifestEntry(tool_name=name, server_key="trading_engine", description=desc,
                                sensitivity=ToolSensitivity.WRITE, rate_limit_per_minute=30)
        for name, desc in write_specs
    })
    entries["trading_place_live_order"] = ToolManifestEntry(
        tool_name="trading_place_live_order", server_key="trading_engine",
        description="Place a REAL order via Kite Connect. Real money, real exchange, irreversible. "
                    "The single most sensitive action Phase 6 adds; gated by top-trust-tier and the "
                    "full regulatory gate chain in addition to the ordinary SENSITIVE voice-gate.",
        sensitivity=ToolSensitivity.SENSITIVE, rate_limit_per_minute=10,
    )
    entries["fo_place_live_option_order"] = ToolManifestEntry(
        tool_name="fo_place_live_option_order", server_key="trading_engine",
        description="Place a REAL index option order via Kite Connect. Real money, real exchange, "
                    "irreversible. Same top-trust-tier + full regulatory gate chain as "
                    "trading_place_live_order.",
        sensitivity=ToolSensitivity.SENSITIVE, rate_limit_per_minute=10,
    )
    entries["fo_place_live_future_order"] = ToolManifestEntry(
        tool_name="fo_place_live_future_order", server_key="trading_engine",
        description="Place a REAL index future order via Kite Connect. Real money, real exchange, "
                    "irreversible. Same top-trust-tier + full regulatory gate chain as "
                    "trading_place_live_order.",
        sensitivity=ToolSensitivity.SENSITIVE, rate_limit_per_minute=10,
    )
    return entries


def build_registry() -> Dict[str, ToolManifestEntry]:
    """The real Phase 6 registry: Phase 5's real (non-demo) registry,
    unmodified, plus Phase 6's trading_engine tools."""
    registry = dict(build_phase5_registry())
    registry.update(_phase6_entries())
    return registry


def build_demo_registry() -> Dict[str, ToolManifestEntry]:
    """Adds Phase 3/4/5's demo fixtures on top of the real Phase 6
    registry, purely so older test scenarios keep working. Phase 6
    introduces no new demo-only fixture of its own."""
    registry = dict(build_phase5_demo_registry())
    registry.update(_phase6_entries())
    return registry
