from rcap_p.attribute import AttributeTracker
from rcap_p.models import InspectVerdict


def test_fresh_source_is_not_a_repeat_offender(config, ledger):
    tracker = AttributeTracker(config, ledger)
    assert not tracker.is_repeat_offender("some_source")
    assert not tracker.is_hard_blocked("some_source")


def test_flags_accumulate_per_source(config, ledger):
    tracker = AttributeTracker(config, ledger)
    tracker.record_flag("bad.example", InspectVerdict.SUSPICIOUS, ["reason 1"])
    tracker.record_flag("bad.example", InspectVerdict.SUSPICIOUS, ["reason 2"])
    rec = tracker._get("bad.example")
    assert rec.flag_count == 2
    # a different source's count is untouched
    assert tracker._get("other.example").flag_count == 0


def test_crosses_threshold_and_becomes_repeat_offender(config, ledger):
    config.attribute_repeat_offender_threshold = 3
    tracker = AttributeTracker(config, ledger)
    for i in range(3):
        tracker.record_flag("bad.example", InspectVerdict.SUSPICIOUS, [f"reason {i}"])
    assert tracker.is_repeat_offender("bad.example")
    assert tracker.is_hard_blocked("bad.example")


def test_stays_below_threshold_is_not_repeat_offender(config, ledger):
    config.attribute_repeat_offender_threshold = 3
    tracker = AttributeTracker(config, ledger)
    tracker.record_flag("bad.example", InspectVerdict.SUSPICIOUS, ["reason"])
    tracker.record_flag("bad.example", InspectVerdict.SUSPICIOUS, ["reason"])
    assert not tracker.is_repeat_offender("bad.example")
    assert not tracker.is_hard_blocked("bad.example")


def test_record_call_tracks_total_calls_independent_of_flags(config, ledger):
    tracker = AttributeTracker(config, ledger)
    tracker.record_call("some_source")
    tracker.record_call("some_source")
    tracker.record_call("some_source")
    rec = tracker._get("some_source")
    assert rec.total_calls == 3
    assert rec.flag_count == 0


def test_report_is_sorted_by_flag_count_descending(config, ledger):
    tracker = AttributeTracker(config, ledger)
    tracker.record_flag("low.example", InspectVerdict.SUSPICIOUS, ["r"])
    tracker.record_flag("high.example", InspectVerdict.MALICIOUS, ["r"])
    tracker.record_flag("high.example", InspectVerdict.MALICIOUS, ["r"])
    report = tracker.report()
    assert report[0].source_label == "high.example"
    assert report[0].flag_count == 2
