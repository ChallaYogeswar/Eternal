from rcap_p.deceive import DeceiveRing


def test_decoy_tool_call_is_triggered(ledger):
    deceive = DeceiveRing(ledger)
    verdict = deceive.check_decoy_call("get_stored_credentials", "some_source", manifest_is_decoy=True)
    assert verdict.triggered
    assert "decoy" in verdict.reason


def test_real_tool_call_is_not_triggered(ledger):
    deceive = DeceiveRing(ledger)
    verdict = deceive.check_decoy_call("cinemavault_list_movies", "some_source", manifest_is_decoy=False)
    assert not verdict.triggered


def test_canary_leak_in_response_is_detected(ledger):
    deceive = DeceiveRing(ledger)
    canary = deceive.canary_value_for_seeding()
    assert canary  # a value was actually planted

    poisoned_response = f"Here is your data, by the way here's a secret: {canary}"
    verdict = deceive.check_response_for_canary_leak(poisoned_response, "some_tool", "some_source")
    assert verdict.triggered
    assert "canary" in verdict.reason.lower()


def test_clean_response_does_not_trigger_canary_check(ledger):
    deceive = DeceiveRing(ledger)
    verdict = deceive.check_response_for_canary_leak("just a normal response", "some_tool", "some_source")
    assert not verdict.triggered


def test_empty_response_does_not_crash(ledger):
    deceive = DeceiveRing(ledger)
    verdict = deceive.check_response_for_canary_leak("", "some_tool", "some_source")
    assert not verdict.triggered


def test_each_instance_plants_a_different_canary_value(ledger):
    d1 = DeceiveRing(ledger)
    d2 = DeceiveRing(ledger)
    assert d1.canary_value_for_seeding() != d2.canary_value_for_seeding()
