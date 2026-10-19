"""Composition root for agent calls (orchestrator layer, O0b).

`orchestration/` is deliberately blind to the real gateway chain: a tripwire test forbids it from importing any phase
package, a network client or a subprocess. Everything that has to touch those lives here instead, in one small
package that WRAPS the existing chain without editing it:

  request.py        AgentCallRequest = WriteCallRequest + a capability token (amendment A2)
  gateway.py        CapabilityGateway: verify the token, attach the signed identity envelope, hand a plain
                    WriteCallRequest to the unchanged Phase 6/5/4 + RCAP chain; CapabilityCoreGateway is the
                    synchronous adapter the ToolBroker calls (same shape as Phase6CoreGateway)
  port.py           ChainCapabilityPort: issues one token per run and the per-tool ROTATE permissions (A3)
  signer_socket.py  unix-socket server/client for the checkpoint signer that runs as a separate OS user
  sinks.py          off-box checkpoint copies through an operator-supplied command (scp over Tailscale, git push)
"""
