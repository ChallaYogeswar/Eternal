#!/usr/bin/env python3
"""Checkpoint signer daemon. Run it as a SEPARATE OS USER that owns the key and state files.

  # one-time, as the signer user:
  python scripts/orchestration/checkpoint_signerd.py init --key ~/signer/ckpt.key --pub ~/signer/ckpt.pub
  # then copy ckpt.pub (public, safe) to where the Auditor / runtime can read it, and run:
  python scripts/orchestration/checkpoint_signerd.py serve --key ~/signer/ckpt.key \
      --state ~/signer/state.json --socket /run/eternal-signer/signer.sock

The socket's group should be the runtime user's group (mode 660) so the runtime can connect but cannot read the key.
The private key never leaves this process; the runtime only ever receives signed checkpoints.
"""
import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey              # noqa: E402
from cryptography.hazmat.primitives import serialization                                      # noqa: E402

from agent_chain.signer_socket import SignerSocketServer                                       # noqa: E402
from orchestration.checkpoint import SignerService                                             # noqa: E402
from orchestration.capability import public_key_bytes                                          # noqa: E402


def _write_private(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def cmd_init(a) -> int:
    if os.path.exists(a.key):
        print(f"refusing to overwrite existing key {a.key}", file=sys.stderr)
        return 2
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    _write_private(a.key, base64.b64encode(raw))
    with open(a.pub, "wb") as f:
        f.write(base64.b64encode(public_key_bytes(key.public_key())))
    print(f"wrote private key (0600) to {a.key} and public key to {a.pub}")
    return 0


def cmd_serve(a) -> int:
    mode = os.stat(a.key).st_mode & 0o077
    if mode:
        print(f"refusing to start: {a.key} is accessible to group/other (mode bits {oct(mode)})", file=sys.stderr)
        return 2
    with open(a.key, "rb") as f:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(f.read()))
    SignerSocketServer(SignerService(key, a.state), a.socket).serve_forever()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init"); i.add_argument("--key", required=True); i.add_argument("--pub", required=True)
    s = sub.add_parser("serve"); s.add_argument("--key", required=True); s.add_argument("--state", required=True)
    s.add_argument("--socket", required=True)
    a = p.parse_args()
    return cmd_init(a) if a.cmd == "init" else cmd_serve(a)


if __name__ == "__main__":
    raise SystemExit(main())
