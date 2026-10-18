"""
Fast, in-process tests against each server's data.py — no MCP transport
involved. The real stdio/MCP-protocol round trip is proven separately in
scripts/test_mcp_e2e.py, same split Phase 1 (pytest vs. test_http_e2e.py)
and Phase 2 used.
"""
import os
import tempfile

import pytest

from mcp_servers.cinemavault import data as cv_data
from mcp_servers.study_tracker import data as study_data


@pytest.fixture(autouse=True)
def _isolated_cinemavault_db(monkeypatch):
    """Every test gets its own throwaway SQLite file so tests can't pollute
    each other's seed data via cv_data's module-level DB_PATH."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let init_and_seed create it fresh
    monkeypatch.setattr(cv_data, "DB_PATH", path)
    cv_data.init_and_seed()
    yield
    if os.path.exists(path):
        os.remove(path)


def test_cinemavault_seed_data_is_present():
    movies = cv_data.list_movies(limit=100)
    assert len(movies) == 8
    assert all(m["title"] for m in movies)


def test_cinemavault_list_respects_limit_and_offset():
    page1 = cv_data.list_movies(limit=3, offset=0)
    page2 = cv_data.list_movies(limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 3
    assert {m["id"] for m in page1}.isdisjoint({m["id"] for m in page2})


def test_cinemavault_get_movie_by_id():
    movie = cv_data.get_movie("m1")
    assert movie is not None
    assert movie["title"] == "Interstellar"


def test_cinemavault_get_missing_movie_returns_none_not_exception():
    assert cv_data.get_movie("does-not-exist") is None


def test_cinemavault_search_is_case_insensitive_substring():
    results = cv_data.search_movies("dark knight")
    assert len(results) == 1
    assert results[0]["id"] == "m4"


def test_cinemavault_search_with_no_matches_returns_empty_list():
    assert cv_data.search_movies("Definitely Not A Real Movie Title XYZ") == []


def test_cinemavault_insert_row_for_test_helper_is_read_only_surface_only():
    """Confirms the test-insertion helper exists ONLY in data.py (used to
    seed a poisoned record for INSPECT tests) and is never imported by
    server.py — server.py's tool functions have no write path at all."""
    import inspect
    from mcp_servers.cinemavault import server as cv_server
    source = inspect.getsource(cv_server)
    assert "_insert_row_for_test" not in source


def test_study_tracker_has_five_phases():
    phases = study_data.get_curriculum_status()
    assert len(phases) == 5
    assert [p["phase_number"] for p in phases] == [1, 2, 3, 4, 5]


def test_study_tracker_phase_1_is_fully_done():
    phase = study_data.get_phase(1)
    assert phase is not None
    assert len(phase["topics_done"]) == 8
    assert phase["topics_next"] == []
    assert phase["topics_todo"] == []


def test_study_tracker_phase_3_is_next():
    phase = study_data.get_phase(3)
    assert phase["topics_done"] == []
    assert len(phase["topics_next"]) == 8


def test_study_tracker_unknown_phase_returns_none_not_exception():
    assert study_data.get_phase(99) is None
