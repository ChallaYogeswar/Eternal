from mcp_servers.aether.data import HttpAetherStore


class FakeAetherHttpStore(HttpAetherStore):
    def __init__(self):
        super().__init__("https://tracker.test", "token")
        self.requests = []
        self.habits = [{
            "id": "h1", "title": "Morning Run", "category": "Exercise",
            "currentStreak": 2, "longestStreak": 4, "doneToday": False, "isActive": True,
        }]

    def _request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        if method == "GET" and path == "/api/habits":
            return self.habits
        if method == "POST" and path == "/api/habits/h1/log":
            self.habits[0]["doneToday"] = True
            return {"done": True, "streak": 3}
        if method == "POST" and path == "/api/habits":
            return {"id": "h2", "title": payload["title"], "category": payload["category"],
                    "currentStreak": 0, "longestStreak": 0}
        raise AssertionError(f"unexpected request: {method} {path}")


def test_http_backend_translates_real_aether_shapes_and_preserves_idempotence():
    store = FakeAetherHttpStore()

    result = store.log_habit("h1", "today")
    assert result["already_logged_today"] is False
    assert result["habit"]["current_streak"] == 2

    result = store.log_habit("h1", "today")
    assert result["already_logged_today"] is True
    assert not any(path == "/api/habits/h1/log" for _, path, _ in store.requests[3:])