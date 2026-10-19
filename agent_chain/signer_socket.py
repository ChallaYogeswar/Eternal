"""Unix-socket front for the checkpoint signer (plan section 6.9).

The server runs as a SEPARATE OS USER that owns the private key; the runtime user can only connect to the socket and
send (head, count, prev). The protocol is one JSON line request, one JSON line reply, then close. The server is the
same `SignerService` the tests use, so the "no rollback, assigns its own seq and time, not a signing oracle" rules
apply unchanged. Nothing here accepts arbitrary bytes to sign.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
from typing import Optional

from orchestration.checkpoint import Checkpoint, CheckpointError, SignerService

MAX_LINE = 4096
TIMEOUT_S = 5.0
UNIX_SOCKET_SUPPORTED = hasattr(socket, "AF_UNIX") and hasattr(socketserver, "UnixStreamServer")


class SignerSocketClient:
    """Runtime side. Implements SignerPort."""

    def __init__(self, path: str, *, timeout_s: float = TIMEOUT_S):
        self.path, self.timeout_s = path, timeout_s

    def checkpoint(self, head: str, count: int, prev: str) -> Checkpoint:
        if not UNIX_SOCKET_SUPPORTED:
            raise CheckpointError("Unix-domain sockets are unsupported on this platform")
        req = (json.dumps({"op": "checkpoint", "head": head, "count": int(count), "prev": prev}) + "\n").encode()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout_s)
                s.connect(self.path)
                s.sendall(req)
                buf = b""
                while not buf.endswith(b"\n") and len(buf) < MAX_LINE:
                    chunk = s.recv(1024)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as e:
            raise CheckpointError(f"signer unreachable: {type(e).__name__}") from e
        try:
            reply = json.loads(buf.decode())
        except ValueError as e:
            raise CheckpointError("signer sent an unreadable reply") from e
        if not reply.get("ok"):
            raise CheckpointError(f"signer refused: {reply.get('error', 'unknown')}")
        return Checkpoint.from_dict(reply["checkpoint"])


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        service: SignerService = self.server.service            # type: ignore[attr-defined]
        try:
            line = self.rfile.readline(MAX_LINE + 1)
            if not line or len(line) > MAX_LINE:
                raise ValueError("bad request size")
            req = json.loads(line.decode())
            if req.get("op") != "checkpoint" or set(req) - {"op", "head", "count", "prev"}:
                raise ValueError("unsupported request")
            head, count, prev = req["head"], req["count"], req["prev"]
            if not (isinstance(head, str) and isinstance(prev, str) and isinstance(count, int) and not isinstance(count, bool)):
                raise ValueError("bad field types")
            cp = service.checkpoint(head, count, prev)
            out = {"ok": True, "checkpoint": cp.to_dict()}
        except (ValueError, KeyError, CheckpointError) as e:
            out = {"ok": False, "error": str(e)[:200]}
        self.wfile.write((json.dumps(out) + "\n").encode())


if UNIX_SOCKET_SUPPORTED:
    class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
else:
    class _Server:
        def __init__(self, *args, **kwargs):
            raise CheckpointError("Unix-domain sockets are unsupported on this platform")


class SignerSocketServer:
    def __init__(self, service: SignerService, path: str, *, mode: int = 0o660):
        self.service, self.path, self.mode = service, path, mode
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)
        self._server = _Server(self.path, _Handler)
        self._server.service = self.service                      # type: ignore[attr-defined]
        os.chmod(self.path, self.mode)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self.start()
        assert self._thread is not None
        self._thread.join()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if os.path.exists(self.path):
            os.unlink(self.path)
