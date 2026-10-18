"""
Remote control data layer — Wake-on-LAN + full-control handoff.

Eternal_Master_Build_Plan.md Section 7.1, verbatim: "Full control (screen/
mouse/keyboard): don't build this from scratch. Run it over an existing
hardened remote-desktop protocol on the Tailscale network — OS-native
remote desktop restricted to Tailscale-only, or self-hosted RustDesk.
**Decision not yet made, open item.** Eternal's job is Wake-on-LAN +
handoff, not reimplementing remote desktop." And: "'take over full control
of my laptop' is the single most sensitive action in the whole system —
the top trust tier, non-voice confirmation always required, plus a kill
switch."

This server runs co-located with Eternal's core (the Pi/mini-PC), not on
the target laptop — deliberately separate from mcp_servers/laptop_agent,
because it must stay reachable and functional while the laptop sleeps
(laptop_agent, by definition, cannot be: nothing runs on a sleeping
machine). Concretely, the Pi is presumed to share the target laptop's home
LAN segment, since Wake-on-LAN's broadcast magic packet does not cross
routers/subnets — a real deployment constraint worth stating rather than
leaving implicit.

Wake-on-LAN itself is REAL, not simulated: constructing and sending a
magic packet needs no OS session, no GUI, and no model weights on either
end, unlike laptop_agent's lock/sleep/shutdown — there is no honest reason
to simulate it, so it isn't (module-level build_magic_packet/send_wol
below are exercised for real in tests, including a real UDP round trip
to a loopback listener).

The remote-desktop PROTOCOL itself (RustDesk vs. OS-native) is
deliberately NOT chosen here — that open item is unaffected by this file,
same as every other unresolved Section 9 item earlier phases left open.
TARGET_DEVICES below is a fixed, server-side registry (never caller-
supplied), so a caller can request "full control of my laptop" but can
never redirect the handoff to an arbitrary address — same allow-list
discipline laptop_agent applies to app/script names.
"""
import socket
import time
import uuid
from typing import Dict, NamedTuple, Optional


class RemoteControlError(ValueError):
    """Caller-facing failure (unknown device label, invalid MAC) — a
    ValueError subclass, same convention as every other data layer's own
    error type in this project."""


class TargetDevice(NamedTuple):
    device_label: str
    mac_address: str
    protocol: str            # "rustdesk" | "os_native" — deployment config, not caller-supplied
    connection_hint: str      # a RustDesk ID, or a Tailscale hostname:port


# Fixed, server-side registry — build plan Section 9 leaves RustDesk vs.
# OS-native as an open decision, so this project models BOTH here so a
# real deployment can pick either without touching this module's logic,
# only its config data. Placeholder values: replace with the real target
# laptop's MAC + chosen protocol's real connection details at deployment
# time (see README "Open Decisions").
TARGET_DEVICES: Dict[str, TargetDevice] = {
    "primary_laptop": TargetDevice(
        device_label="primary_laptop",
        mac_address="00:11:22:33:44:55",
        protocol="rustdesk",
        connection_hint="rustdesk-id-placeholder-000000000",
    ),
}

HANDOFF_TTL_SECONDS = 300.0


def build_magic_packet(mac_address: str) -> bytes:
    """Standard Wake-on-LAN magic packet: 6 bytes of 0xFF followed by the
    target MAC repeated 16 times. Pure bytes construction — testable with
    zero network access."""
    cleaned = mac_address.replace(":", "").replace("-", "").strip().lower()
    if len(cleaned) != 12 or any(c not in "0123456789abcdef" for c in cleaned):
        raise RemoteControlError(f"'{mac_address}' is not a valid MAC address")
    mac_bytes = bytes.fromhex(cleaned)
    return b"\xff" * 6 + mac_bytes * 16


class WakeOnLanOutcome:
    def __init__(self, ok: bool, target_mac: str, bytes_sent: int, sent_to: str, detail: str = ""):
        self.ok = ok
        self.target_mac = target_mac
        self.bytes_sent = bytes_sent
        self.sent_to = sent_to
        self.detail = detail

    def as_dict(self) -> dict:
        return {"ok": self.ok, "target_mac": self.target_mac, "bytes_sent": self.bytes_sent,
                "sent_to": self.sent_to, "detail": self.detail}


def send_wol(mac_address: str, target_ip: str = "255.255.255.255", port: int = 9) -> WakeOnLanOutcome:
    """Sends the magic packet over UDP. `target_ip` defaults to the
    subnet-broadcast convention real deployments use; tests pass a
    loopback/unicast address so the packet's actual receipt can be
    asserted end-to-end without needing a real broadcast-capable network
    namespace."""
    packet = build_magic_packet(mac_address)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        if target_ip == "255.255.255.255":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (target_ip, port))
        return WakeOnLanOutcome(True, mac_address, len(packet), f"{target_ip}:{port}",
                                 detail=f"magic packet ({len(packet)} bytes) sent to {target_ip}:{port}")
    finally:
        sock.close()


class FullControlHandoff:
    def __init__(self, wake_on_lan_sent: bool, target_mac_address: str, protocol: str,
                 connection_hint: str, session_id: str, expires_at: float, note: str):
        self.wake_on_lan_sent = wake_on_lan_sent
        self.target_mac_address = target_mac_address
        self.protocol = protocol
        self.connection_hint = connection_hint
        self.session_id = session_id
        self.expires_at = expires_at
        self.note = note

    def as_dict(self) -> dict:
        return {
            "wake_on_lan_sent": self.wake_on_lan_sent, "target_mac_address": self.target_mac_address,
            "protocol": self.protocol, "connection_hint": self.connection_hint,
            "session_id": self.session_id, "expires_at": self.expires_at, "note": self.note,
        }


def request_full_control(device_label: str, *, send_wake: bool = True,
                          target_ip: str = "255.255.255.255", port: int = 9) -> FullControlHandoff:
    """Composes wake + handoff for one fixed, registered device label.
    This function does NOT check consent, voice-gate, or top-trust-tier —
    those are device_control_gateway.gateway's job, applied BEFORE this is ever called
    (same layering every other tool in this project uses: the data layer
    only knows how to do the thing once asked, gating lives one level up).
    `port` defaults to the Wake-on-LAN standard (9) and is threaded
    through to send_wol so tests can point it at a loopback listener on
    an arbitrary port instead — added after an early demo run sent a real
    packet to 127.0.0.1:9 while a test listener waited on a different
    ephemeral port and never saw it arrive; the packet was genuinely
    sent, just not to where the test was listening.
    """
    if device_label not in TARGET_DEVICES:
        raise RemoteControlError(f"'{device_label}' is not a registered target device: {list(TARGET_DEVICES)}")
    target = TARGET_DEVICES[device_label]

    wol_ok = False
    if send_wake:
        outcome = send_wol(target.mac_address, target_ip=target_ip, port=port)
        wol_ok = outcome.ok

    session_id = f"fcs-{uuid.uuid4()}"
    expires_at = time.time() + HANDOFF_TTL_SECONDS
    return FullControlHandoff(
        wake_on_lan_sent=wol_ok,
        target_mac_address=target.mac_address,
        protocol=target.protocol,
        connection_hint=target.connection_hint,
        session_id=session_id,
        expires_at=expires_at,
        note=(
            f"Wake-on-LAN {'sent' if wol_ok else 'not sent'} to {target.device_label}. "
            f"Connect via {target.protocol} using the connection hint. This handoff expires in "
            f"{HANDOFF_TTL_SECONDS:.0f}s."
        ),
    )
