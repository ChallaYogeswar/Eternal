from mcp_servers.integration_status import integration_modes


def test_defaults_are_labelled_as_mirrors_stubs_and_simulations():
    m = integration_modes({})
    assert "MIRROR" in m["aether"] and "MIRROR" in m["cinemavault"] and "STUB" in m["web_search"]
    assert "SIMULATED" in m["home_assistant"] and "SIMULATED" in m["laptop_agent"] and "PLACEHOLDER" in m["remote_control"]


def test_aether_is_live_only_when_fully_configured():
    assert "MIRROR" in integration_modes({"AETHER_BACKEND": "http"})["aether"]
    assert integration_modes({"AETHER_BACKEND": "http", "AETHER_API_URL": "https://x", "AETHER_API_TOKEN": "t"})["aether"] == "live_http_api"


def test_home_assistant_needs_url_and_token():
    assert "SIMULATED" in integration_modes({"HOME_ASSISTANT_BASE_URL": "http://ha"})["home_assistant"]
    assert "configured" in integration_modes({"HOME_ASSISTANT_BASE_URL": "http://ha", "HOME_ASSISTANT_TOKEN": "t"})["home_assistant"]
