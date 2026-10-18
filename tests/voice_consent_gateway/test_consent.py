def test_no_consent_by_default(consent_registry):
    assert consent_registry.has_consent("aether_add_expense") is False
    assert consent_registry.get("aether_add_expense") is None


def test_grant_then_has_consent(consent_registry):
    consent_registry.grant("aether_add_expense", granted_by="app_tap")
    assert consent_registry.has_consent("aether_add_expense") is True
    rec = consent_registry.get("aether_add_expense")
    assert rec.granted is True
    assert rec.granted_by == "app_tap"


def test_revoke_after_grant(consent_registry):
    consent_registry.grant("aether_add_expense", granted_by="app_tap")
    consent_registry.revoke("aether_add_expense", revoked_by="app_tap")
    assert consent_registry.has_consent("aether_add_expense") is False


def test_grant_is_per_tool_not_global(consent_registry):
    consent_registry.grant("aether_add_expense", granted_by="app_tap")
    assert consent_registry.has_consent("aether_log_habit") is False


def test_regrant_after_revoke(consent_registry):
    consent_registry.grant("aether_add_expense", granted_by="app_tap")
    consent_registry.revoke("aether_add_expense", revoked_by="app_tap")
    consent_registry.grant("aether_add_expense", granted_by="app_tap_again")
    assert consent_registry.has_consent("aether_add_expense") is True


def test_grant_and_revoke_are_ledger_logged(consent_registry, ledger):
    consent_registry.grant("aether_add_expense", granted_by="app_tap")
    consent_registry.revoke("aether_add_expense", revoked_by="app_tap")
    events = [e["event_type"] for e in ledger.recent(10)]
    assert "consent_granted" in events
    assert "consent_revoked" in events


def test_persists_across_new_instance_same_db(config, ledger):
    from voice_consent_gateway.consent import ConsentRegistry
    r1 = ConsentRegistry(config.consent_db_path, ledger)
    r1.grant("aether_add_expense", granted_by="app_tap")
    r2 = ConsentRegistry(config.consent_db_path, ledger)
    assert r2.has_consent("aether_add_expense") is True
