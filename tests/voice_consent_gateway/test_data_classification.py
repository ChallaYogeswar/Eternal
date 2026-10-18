import pytest

from voice_consent_gateway.models import MemoryCategory
from voice_consent_gateway.voice.data_classification import DataClass, classify


@pytest.mark.parametrize("text", [
    "I spent 500 rupees on groceries today",
    "please check my portfolio balance",
    "I want to buy shares of that company",
    "add an expense of two hundred rupees for lunch",
])
def test_finance_text_is_local_only(text):
    assert classify(text).data_class == DataClass.LOCAL_ONLY


@pytest.mark.parametrize("text", [
    "my blood pressure was a bit high this morning",
    "remind me to take my medication",
    "I should see a doctor about this",
])
def test_health_text_is_local_only(text):
    assert classify(text).data_class == DataClass.LOCAL_ONLY


@pytest.mark.parametrize("text", [
    "I had the strangest dream last night",
    "add this to my dream journal",
    "do you remember any of my old memories",
])
def test_dream_and_memory_text_is_local_only(text):
    assert classify(text).data_class == DataClass.LOCAL_ONLY


@pytest.mark.parametrize("text", [
    "what's the weather like today",
    "tell me a fun fact about space",
    "log a forty five minute cycling workout",
])
def test_casual_text_is_cloud_ok(text):
    assert classify(text).data_class == DataClass.CLOUD_OK


@pytest.mark.parametrize("category", [MemoryCategory.FINANCE, MemoryCategory.HEALTH, MemoryCategory.DREAM_JOURNAL])
def test_domain_hint_forces_local_even_for_bland_text(category):
    result = classify("nothing unusual here", domain_hint=category)
    assert result.data_class == DataClass.LOCAL_ONLY
    assert "domain_hint" in result.reasons[0]


def test_general_domain_hint_falls_back_to_text_scan():
    result = classify("what's the weather like today", domain_hint=MemoryCategory.GENERAL)
    assert result.data_class == DataClass.CLOUD_OK


def test_result_always_carries_reasons():
    assert len(classify("anything at all").reasons) >= 1
