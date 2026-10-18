"""
Minimal voice-command -> tool-call parser.

Scope note, stated plainly rather than left implicit: this is NOT the
production design for turning speech into tool calls. That job belongs to
Eternal's actual reasoning loop — eternal_core's LangGraph graph (Phase 2),
extended with a tool-calling node once one exists (see
graph_integration.py for a worked example of that extension). This
module exists only so Phase 4's exit criterion ("a spoken command changes
tracker data") has a genuine, testable, end-to-end path to demonstrate
against, without Phase 4 having to first build a general LLM-based
tool-router as a side quest. It recognizes a small, fixed set of command
shapes with regexes; it does not understand paraphrases, and it is not
meant to.

What Phase 4 actually needs this module to demonstrate correctly is
everything AROUND the tool call — consent, the voice-gate rule, the
ledger explanation, INSPECT/CONTAIN — and those all operate on whatever
ParsedIntent comes out of here, real LLM-routed or this regex stand-in
alike. Swapping this module out for a real one later changes nothing
downstream of it.
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ParsedIntent:
    tool_name: Optional[str]
    arguments: Dict[str, Any]
    matched_pattern: Optional[str]
    explanation: str


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}


def _words_to_number(phrase: str) -> Optional[int]:
    """Tiny word-number parser, only needs to handle 'two hundred' /
    'forty five' style phrases from the fixed KNOWN_COMMANDS cassette —
    not a general solution, see module docstring."""
    tokens = phrase.lower().split()
    total, current = 0, 0
    for t in tokens:
        if t not in _NUMBER_WORDS:
            continue
        val = _NUMBER_WORDS[t]
        if val == 100:
            current = (current or 1) * 100
        else:
            current += val
    total += current
    return total or None


def parse(transcript: str) -> ParsedIntent:
    text = transcript.lower().strip()

    m = re.search(r"log (?:my )?(.+?) habit as done(?: for today)?", text)
    if m:
        habit_name = m.group(1).strip()
        return ParsedIntent(
            tool_name="aether_log_habit",
            arguments={"habit_title": habit_name, "date_key": "today"},
            matched_pattern="log_habit",
            explanation=f"You said: \"{transcript}\" — logging habit '{habit_name}' as done for today.",
        )

    m = re.search(r"add (?:an )?expense of (.+?) rupees? for (.+)", text)
    if m:
        amount = _words_to_number(m.group(1))
        item = m.group(2).strip()
        return ParsedIntent(
            tool_name="aether_add_expense",
            arguments={"name": item, "amount": amount, "category": "other"},
            matched_pattern="add_expense",
            explanation=f"You said: \"{transcript}\" — adding an expense of ₹{amount} for {item}.",
        )

    m = re.search(r"log a (.+?) minute (.+?) workout", text)
    if m:
        duration = _words_to_number(m.group(1))
        workout_type = m.group(2).strip()
        return ParsedIntent(
            tool_name="aether_log_workout",
            arguments={"workout_type": workout_type, "duration_min": duration},
            matched_pattern="log_workout",
            explanation=f"You said: \"{transcript}\" — logging a {duration}-minute {workout_type} workout.",
        )

    m = re.search(r"unlock the (.+?) door", text)
    if m:
        door = m.group(1).strip()
        return ParsedIntent(
            tool_name="sensitive_demo_unlock_door",
            arguments={"door": door},
            matched_pattern="unlock_door",
            explanation=(
                f"You said: \"{transcript}\" — this would unlock the {door} door, "
                "a SENSITIVE-tier physical action. Standing in here for Phase 5/6's "
                "real trade-execution/full-laptop-control/lock tools (build plan "
                "Section 6.3, point 2)."
            ),
        )

    return ParsedIntent(
        tool_name=None, arguments={}, matched_pattern=None,
        explanation=f"You said: \"{transcript}\" — no recognized tracker command; treat as conversational.",
    )
