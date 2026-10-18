"""
Study tracker data layer.

Unlike CinemaVault (see cinemavault/data.py's honest-scoping note), the
study tracker's real interface already exists and was reviewed directly:
`study_progress_tracker.html`, a self-contained static HTML artifact
listing five curriculum phases with a per-topic done/next/todo status.
This module transcribes that same real data as a small, queryable Python
structure rather than inventing placeholder curriculum content — the
phase names, week ranges, and topic lists below are copied verbatim from
the reviewed HTML, not simulated.

Read-only by construction, same as cinemavault/data.py: no mutation
function exists here at all.
"""
from typing import Dict, List, Optional, TypedDict


class PhaseRow(TypedDict):
    phase_number: int
    name: str
    weeks: str
    topics_done: List[str]
    topics_next: List[str]
    topics_todo: List[str]


_CURRICULUM: List[PhaseRow] = [
    {
        "phase_number": 1,
        "name": "Python & Java OOP",
        "weeks": "Weeks 1-4",
        "topics_done": [
            "Python loops & functions", "File handling", "Exception handling",
            "APIs with requests", "OOP in Python", "Java OOP concepts",
            "Inheritance & polymorphism", "ArrayList & HashMap",
        ],
        "topics_next": [],
        "topics_todo": [],
    },
    {
        "phase_number": 2,
        "name": "Data Structures & Algorithms",
        "weeks": "Weeks 5-10",
        "topics_done": [
            "Arrays & strings", "Linked list", "Stack & queue", "Hashing",
            "Recursion", "Sorting algorithms", "Binary search",
            "Problem set (5 problems)", "Big-O cheatsheet",
        ],
        "topics_next": [],
        "topics_todo": [],
    },
    {
        "phase_number": 3,
        "name": "CS Fundamentals — DBMS & OS",
        "weeks": "Weeks 11-14",
        "topics_done": [],
        "topics_next": [
            "What is a database / RDBMS", "SQL: SELECT, WHERE, JOIN",
            "GROUP BY, HAVING", "Normalization 1NF-3NF", "Process vs thread",
            "CPU scheduling", "Deadlock & memory", "Semaphore & mutex",
        ],
        "topics_todo": [],
    },
    {
        "phase_number": 4,
        "name": "ML & Computer Vision (your strength)",
        "weeks": "Weeks 15-20",
        "topics_done": [],
        "topics_next": [],
        "topics_todo": [
            "ML fundamentals", "Train/val/test split", "Accuracy, precision, recall",
            "CNN architecture", "OpenCV image processing", "Model training with Keras",
            "Dataset handling",
        ],
    },
    {
        "phase_number": 5,
        "name": "Hardware Track (parallel from week 11)",
        "weeks": "Weeks 11-20",
        "topics_done": [],
        "topics_next": [],
        "topics_todo": [
            "Digital electronics", "Analog circuits", "Embedded systems & C",
            "Arduino hands-on", "Raspberry Pi + Python GPIO",
            "Communication systems", "Troubleshooting skills",
        ],
    },
]

_BY_PHASE: Dict[int, PhaseRow] = {p["phase_number"]: p for p in _CURRICULUM}


def get_curriculum_status() -> List[PhaseRow]:
    return list(_CURRICULUM)


def get_phase(phase_number: int) -> Optional[PhaseRow]:
    return _BY_PHASE.get(phase_number)
