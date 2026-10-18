import time

from rcap_p.contain import ContainQueue
from rcap_p.models import ContainmentState, InspectVerdict


def test_decision_table_clean(config, ledger):
    q = ContainQueue(config, ledger)
    assert q.decide(InspectVerdict.CLEAN, is_repeat_offender=False, deceive_triggered=False) == ContainmentState.PASSTHROUGH


def test_decision_table_suspicious(config, ledger):
    q = ContainQueue(config, ledger)
    assert q.decide(InspectVerdict.SUSPICIOUS, is_repeat_offender=False, deceive_triggered=False) == ContainmentState.CONTAINED


def test_decision_table_malicious(config, ledger):
    q = ContainQueue(config, ledger)
    assert q.decide(InspectVerdict.MALICIOUS, is_repeat_offender=False, deceive_triggered=False) == ContainmentState.HARD_BLOCKED


def test_decision_table_repeat_offender_forces_hard_block_even_if_suspicious_only(config, ledger):
    q = ContainQueue(config, ledger)
    assert q.decide(InspectVerdict.SUSPICIOUS, is_repeat_offender=True, deceive_triggered=False) == ContainmentState.HARD_BLOCKED


def test_decision_table_deceive_trigger_forces_hard_block_even_if_clean(config, ledger):
    q = ContainQueue(config, ledger)
    assert q.decide(InspectVerdict.CLEAN, is_repeat_offender=False, deceive_triggered=True) == ContainmentState.HARD_BLOCKED


def test_open_approval_request_is_queryable_and_pending(config, ledger):
    q = ContainQueue(config, ledger)
    req = q.open_approval_request("web_search", "example.invalid", ["matched instruction_override"], "raw content")
    assert req in q.pending()
    fetched = q.get(req.request_id)
    assert fetched.request_id == req.request_id
    assert not fetched.resolved


def test_approve_resolves_the_request(config, ledger):
    q = ContainQueue(config, ledger)
    req = q.open_approval_request("web_search", "example.invalid", ["reason"], "raw")
    approved = q.approve(req.request_id)
    assert approved.resolved
    assert approved.approved is True
    assert req not in q.pending()


def test_reject_resolves_the_request_as_not_approved(config, ledger):
    q = ContainQueue(config, ledger)
    req = q.open_approval_request("web_search", "example.invalid", ["reason"], "raw")
    rejected = q.reject(req.request_id)
    assert rejected.resolved
    assert rejected.approved is False


def test_approving_an_already_resolved_request_raises_cleanly(config, ledger):
    q = ContainQueue(config, ledger)
    req = q.open_approval_request("web_search", "example.invalid", ["reason"], "raw")
    q.approve(req.request_id)
    try:
        q.approve(req.request_id)
        assert False, "expected a ValueError for double-approval"
    except ValueError:
        pass


def test_request_expires_to_hard_block_after_max_duration(config, ledger):
    config.contain_max_duration_seconds = 0.05
    q = ContainQueue(config, ledger)
    req = q.open_approval_request("web_search", "example.invalid", ["reason"], "raw")
    time.sleep(0.1)
    assert req not in q.pending()
    fetched = q.get(req.request_id)
    assert fetched.resolved
    assert fetched.approved is False


def test_unknown_request_id_raises_keyerror_not_crash(config, ledger):
    q = ContainQueue(config, ledger)
    try:
        q.approve("does-not-exist")
        assert False, "expected KeyError"
    except KeyError:
        pass
