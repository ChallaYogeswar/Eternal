"""
Simple CLI for poking at QAP-P locally without the HTTP layer.
The real proof-of-mechanism script is demo_exit_criteria.py; this is just a
convenience for inspecting state afterwards.

Usage:
    python scripts/qap_cli.py ledger-verify
    python scripts/qap_cli.py ledger-show --limit 20
    python scripts/qap_cli.py devices
"""
import argparse
import json

from qap_p.orchestrator import Orchestrator


def main():
    parser = argparse.ArgumentParser(description="QAP-P CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ledger-verify")
    p_show = sub.add_parser("ledger-show")
    p_show.add_argument("--limit", type=int, default=50)
    sub.add_parser("devices")

    args = parser.parse_args()
    orch = Orchestrator()

    if args.command == "ledger-verify":
        ok, bad_id = orch.verify_ledger()
        print(json.dumps({"intact": ok, "first_broken_entry_id": bad_id, "total_entries": orch.ledger.count()}))
    elif args.command == "ledger-show":
        for entry in orch.ledger_entries(args.limit):
            print(json.dumps(entry))
    elif args.command == "devices":
        for d in orch.store.list_devices():
            print(json.dumps({
                "device_id": d.device_id, "role": d.role.value,
                "quarantine_count": d.quarantine_count, "anomaly_count": d.anomaly_count,
                "trust_score": round(orch.compute_trust_score(d.device_id), 3),
            }))


if __name__ == "__main__":
    main()
