"""
Exercises the QAP-P Orchestrator over real HTTP (the actual network-facing
daemon a phone/laptop on the Tailscale mesh would talk to), not through the
Python class directly. Run against a live `uvicorn qap_p.api:app` process
started from the project root:

    QAP_QUIESCE_TTL=3 uvicorn qap_p.api:app --host 127.0.0.1 --port 8421
    python scripts/http_e2e.py
"""
import base64
import os
import sqlite3
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qap_p.hardware_key import SimulatedHardwareKey

BASE = "http://127.0.0.1:8421"


def main():
    client = httpx.Client(base_url=BASE, timeout=10)

    phone_key = SimulatedHardwareKey()
    laptop_key = SimulatedHardwareKey()

    r = client.post("/devices/enroll", json={
        "device_id": "http-phone-1", "role": "phone", "public_key_pem": phone_key.public_key_pem(),
    })
    print("enroll phone:", r.status_code, r.json())
    assert r.status_code == 200

    r = client.post("/devices/enroll", json={
        "device_id": "http-laptop-1", "role": "laptop", "public_key_pem": laptop_key.public_key_pem(),
    })
    print("enroll laptop:", r.status_code, r.json())
    assert r.status_code == 200

    # backdate the phone's enrollment directly in the daemon's DB so it
    # clears the issuance threshold, same trick as the in-process demo
    r = client.get("/devices/http-phone-1/trust-score")
    print("phone trust before aging:", r.json())

    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "qap_p.db")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE devices SET enrolled_at = ? WHERE device_id = ?",
                 (time.time() - 40 * 86400, "http-phone-1"))
    conn.commit()
    conn.close()

    r = client.get("/devices/http-phone-1/trust-score")
    print("phone trust after aging:", r.json())
    assert r.json()["trust_score"] >= 0.55

    r = client.post("/sessions", params={"device_id": "http-laptop-1"})
    print("create session:", r.status_code, r.json())
    session_id = r.json()["session_id"]
    assert r.json()["state"] == "ACTIVE"

    r = client.post(f"/sessions/{session_id}/quiesce", json={"cpu_load": 0.1})
    print("quiesce:", r.status_code, r.json())
    assert r.json()["state"] == "QUIESCE"

    r = client.post("/authorize/challenge", json={"target_session_id": session_id})
    nonce = r.json()["nonce"]
    print("challenge nonce:", nonce)

    signature = phone_key.sign(nonce.encode())
    sig_b64 = base64.b64encode(signature).decode()

    r = client.post("/authorize", json={
        "issuing_device_id": "http-phone-1",
        "target_session_id": session_id,
        "nonce": nonce,
        "signature_b64": sig_b64,
    })
    print("authorize:", r.status_code, r.json())
    assert r.status_code == 200
    token = r.json()["authorization_token"]

    r = client.post(f"/sessions/{session_id}/revive", json={"authorization_token": token})
    print("revive:", r.status_code, r.json())
    assert r.json()["state"] == "ACTIVE"

    # spoofed reauth over the wire
    r = client.post(f"/sessions/{session_id}/revive", json={"authorization_token": "forged.garbage.token"})
    print("spoofed revive attempt:", r.status_code, r.json())
    assert r.status_code == 403

    r = client.get(f"/sessions/{session_id}")
    print("session state after spoof:", r.json())
    assert r.json()["state"] == "QUARANTINE"

    r = client.get("/ledger/verify")
    print("ledger verify:", r.json())
    assert r.json()["intact"] is True

    r = client.get("/ledger", params={"limit": 5})
    print("last 5 ledger entries:")
    for entry in r.json()["entries"]:
        print(" ", entry["event_type"])

    print("\nALL HTTP END-TO-END CHECKS PASSED")


if __name__ == "__main__":
    main()
