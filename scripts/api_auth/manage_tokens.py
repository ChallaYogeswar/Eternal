#!/usr/bin/env python3
"""Create API principals for the Eternal daemons.

  python scripts/api_auth/manage_tokens.py add --file ~/.eternal/auth.json --id phone --role owner_app

Prints the bearer token ONCE (give it to the client); the file stores only its SHA-256. Then run the daemons with
ETERNAL_AUTH_FILE=~/.eternal/auth.json. Roles: owner_app, voice_client, automation, readonly.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from api_auth.principals import ROLES, hash_token, new_token   # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("--file", required=True); a.add_argument("--id", required=True)
    a.add_argument("--role", required=True, choices=sorted(ROLES))
    r = sub.add_parser("remove"); r.add_argument("--file", required=True); r.add_argument("--id", required=True)
    sub.add_parser("list").add_argument("--file", required=True)
    args = p.parse_args()
    data = {"principals": []}
    if os.path.exists(args.file):
        with open(args.file, encoding="utf-8") as f:
            data = json.load(f)
    if args.cmd == "list":
        for e in data["principals"]:
            print(e["id"], e["role"])
        return 0
    if args.cmd == "add":
        if any(e["id"] == args.id for e in data["principals"]):
            print("id already exists; remove it first to rotate", file=sys.stderr)
            return 2
        tok = new_token()
        data["principals"].append({"id": args.id, "role": args.role, "token_sha256": hash_token(tok)})
        print(f"token for '{args.id}' (shown once): {tok}")
    else:
        data["principals"] = [e for e in data["principals"] if e["id"] != args.id]
    fd = os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
