from voice_consent_gateway.voice.fixtures import KNOWN_COMMANDS
from voice_consent_gateway.voice.intent import parse


def test_known_commands_all_parse_to_expected_tool():
    for key, cmd in KNOWN_COMMANDS.items():
        if cmd.intent_hint.startswith("speaker_enrollment"):
            continue
        parsed = parse(cmd.text)
        assert parsed.tool_name == cmd.intent_hint, f"{key}: {cmd.text!r} -> {parsed.tool_name!r}"


def test_habit_arguments_extracted():
    parsed = parse("log my morning run habit as done for today")
    assert parsed.arguments["habit_title"] == "morning run"


def test_expense_amount_word_parsing():
    parsed = parse("add an expense of two hundred rupees for lunch")
    assert parsed.arguments["amount"] == 200
    assert parsed.arguments["name"] == "lunch"


def test_workout_duration_and_type_extracted():
    parsed = parse("log a forty five minute cycling workout")
    assert parsed.arguments["duration_min"] == 45
    assert parsed.arguments["workout_type"] == "cycling"


def test_unlock_door_extracts_door_and_flags_sensitive_tool():
    parsed = parse("unlock the front door")
    assert parsed.tool_name == "sensitive_demo_unlock_door"
    assert parsed.arguments["door"] == "front"


def test_unrecognized_command_returns_no_tool():
    parsed = parse("what a lovely day it is today")
    assert parsed.tool_name is None
    assert parsed.arguments == {}


def test_explanation_always_quotes_the_original_transcript():
    transcript = "log my morning run habit as done for today"
    parsed = parse(transcript)
    assert transcript in parsed.explanation
