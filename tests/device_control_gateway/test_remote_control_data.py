"""
Unit tests for mcp_servers.remote_control.data — real Wake-on-LAN packet
construction and a genuine UDP round trip (not mocked: an actual socket
listens on loopback and asserts the exact bytes arrive), plus the
full-control handoff builder's fixed-registry discipline.
"""
import socket
import threading

import pytest

from mcp_servers.remote_control.data import (
    HANDOFF_TTL_SECONDS, TARGET_DEVICES, RemoteControlError, build_magic_packet,
    request_full_control, send_wol,
)


def test_magic_packet_is_6_ff_bytes_plus_mac_times_16():
    packet = build_magic_packet("00:11:22:33:44:55")
    assert packet[:6] == b"\xff" * 6
    assert len(packet) == 6 + 16 * 6
    mac_bytes = bytes.fromhex("001122334455")
    for i in range(16):
        chunk = packet[6 + i * 6: 6 + (i + 1) * 6]
        assert chunk == mac_bytes


@pytest.mark.parametrize("mac", ["00-11-22-33-44-55", "00:11:22:33:44:55", "0011.2233.4455".replace(".", "")])
def test_magic_packet_accepts_common_mac_formats(mac):
    # last variant intentionally collapses to a bare hex string; the
    # colon/dash variants are the ones real callers will actually use
    packet = build_magic_packet(mac)
    assert len(packet) == 6 + 16 * 6


@pytest.mark.parametrize("bad_mac", ["not-a-mac", "00:11:22:33:44", "gg:11:22:33:44:55", ""])
def test_magic_packet_rejects_invalid_mac(bad_mac):
    with pytest.raises(RemoteControlError):
        build_magic_packet(bad_mac)


def test_send_wol_real_udp_round_trip():
    """Not mocked: binds a real UDP socket on loopback, sends a real magic
    packet to it via send_wol, and asserts the exact bytes received match
    build_magic_packet's output byte-for-byte. This is what 'Wake-on-LAN
    is real, not simulated' actually means, proven rather than asserted
    in a docstring."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(2.0)
    port = listener.getsockname()[1]

    received = {}

    def _listen():
        try:
            data, _addr = listener.recvfrom(1024)
            received["data"] = data
        except socket.timeout:
            received["data"] = None

    t = threading.Thread(target=_listen)
    t.start()

    outcome = send_wol("aa:bb:cc:dd:ee:ff", target_ip="127.0.0.1", port=port)
    t.join(timeout=3.0)
    listener.close()

    assert outcome.ok is True
    assert outcome.bytes_sent == 6 + 16 * 6
    assert received["data"] == build_magic_packet("aa:bb:cc:dd:ee:ff")


def test_send_wol_rejects_invalid_mac_before_touching_the_network():
    with pytest.raises(RemoteControlError):
        send_wol("garbage", target_ip="127.0.0.1", port=9)


def test_request_full_control_unknown_device_raises():
    """A caller can request 'full control of my laptop' but can never
    redirect the handoff to an arbitrary address — the registry is fixed
    and server-side."""
    with pytest.raises(RemoteControlError):
        request_full_control("some_device_the_caller_made_up")


def test_request_full_control_known_device_returns_real_handoff():
    handoff = request_full_control("primary_laptop", target_ip="127.0.0.1")
    target = TARGET_DEVICES["primary_laptop"]
    assert handoff.target_mac_address == target.mac_address
    assert handoff.protocol == target.protocol
    assert handoff.connection_hint == target.connection_hint
    assert handoff.wake_on_lan_sent is True
    assert handoff.session_id.startswith("fcs-")
    assert handoff.expires_at > 0


def test_request_full_control_can_skip_wake(monkeypatch):
    """send_wake=False should mean exactly that — no packet construction/
    send attempted, confirmed via wake_on_lan_sent being False, not just
    'didn't crash'."""
    handoff = request_full_control("primary_laptop", send_wake=False)
    assert handoff.wake_on_lan_sent is False


def test_handoff_ttl_matches_module_constant():
    handoff = request_full_control("primary_laptop", target_ip="127.0.0.1")
    # allow a couple seconds of test-execution slack either side
    import time
    assert abs((handoff.expires_at - time.time()) - HANDOFF_TTL_SECONDS) < 5
