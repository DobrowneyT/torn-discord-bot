"""
The fleet tenant registry client.

⚠️ The distinction this file exists to hold: `None` (could not ask) and `[]`
(asked, fleet is empty) are different answers. Reporting "no factions exist"
during an outage is how somebody re-provisions one that is already there.
"""

import pytest
import requests

import choon_registry as reg


class FakeResponse:
    def __init__(self, status=200, payload=None, text_body=None):
        self.status_code = status
        self._payload = payload
        self._text = text_body

    def json(self):
        if self._text is not None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv(reg.TOKEN_ENV, "t" * 64)
    yield


def serve(monkeypatch, response):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers or {}
        seen["timeout"] = timeout
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    return seen


class TestFetch:
    def test_it_returns_the_fleet(self, monkeypatch):
        serve(monkeypatch, FakeResponse(payload={
            "tenants": [{"slug": "forge", "base_url": "https://forge.monchoon.me"}],
            "base_url_available": True}))
        assert reg.fetch() == [{"slug": "forge", "base_url": "https://forge.monchoon.me"}]

    def test_it_sends_the_bearer_and_a_short_timeout(self, monkeypatch):
        seen = serve(monkeypatch, FakeResponse(payload={"tenants": [], "base_url_available": True}))
        reg.fetch()
        assert seen["headers"]["Authorization"].startswith("Bearer ")
        # ⚠️ Called from an autocomplete, which fires per keystroke. A picker
        # that hangs is worse than one that is briefly empty.
        assert seen["timeout"] == reg.TIMEOUT_SECONDS

    def test_an_empty_fleet_is_a_fact_not_a_failure(self, monkeypatch):
        serve(monkeypatch, FakeResponse(payload={"tenants": [], "base_url_available": True}))
        assert reg.fetch() == []
        assert reg.fetch() is not None

    @pytest.mark.parametrize("response", [
        requests.RequestException("boom"),
        FakeResponse(status=401),
        FakeResponse(status=503),
        FakeResponse(status=500),
        FakeResponse(text_body="<html>"),
        FakeResponse(payload={"nope": 1}),
    ])
    def test_every_failure_is_none_and_never_an_empty_fleet(self, monkeypatch, response):
        # ⚠️ THE distinction. `[]` would read as "the fleet has no factions".
        serve(monkeypatch, response)
        assert reg.fetch() is None

    def test_no_token_is_a_failure_not_an_empty_fleet(self, monkeypatch):
        monkeypatch.delenv(reg.TOKEN_ENV, raising=False)
        assert reg.fetch() is None
        assert reg.configured() is False

    def test_it_warns_but_still_answers_when_there_are_no_base_urls(self, monkeypatch, caplog):
        # ⚠️ Surfaced rather than swallowed: a caller that did not notice would
        # write a tenant with no URL and see it fail later as an unreachable
        # dashboard.
        serve(monkeypatch, FakeResponse(payload={
            "tenants": [{"slug": "forge", "base_url": None}],
            "base_url_available": False, "note": "TENANT_BASE_DOMAIN is not set"}))
        with caplog.at_level("WARNING"):
            out = reg.fetch()
        assert out == [{"slug": "forge", "base_url": None}]
        assert "TENANT_BASE_DOMAIN" in caplog.text


class TestLookupAndSlugs:
    def _fleet(self, monkeypatch):
        serve(monkeypatch, FakeResponse(payload={
            "tenants": [{"slug": "forge", "base_url": "https://forge.monchoon.me"},
                        {"slug": "tnl", "base_url": "https://tnl.monchoon.me"}],
            "base_url_available": True}))

    def test_lookup_finds_a_faction_case_insensitively(self, monkeypatch):
        self._fleet(monkeypatch)
        assert reg.lookup("  FORGE ")["base_url"] == "https://forge.monchoon.me"

    def test_lookup_is_none_for_an_unknown_slug(self, monkeypatch):
        self._fleet(monkeypatch)
        assert reg.lookup("ghost") is None

    def test_slugs_lists_the_fleet(self, monkeypatch):
        self._fleet(monkeypatch)
        assert reg.slugs() == ["forge", "tnl"]

    def test_slugs_is_empty_when_it_could_not_ask(self, monkeypatch):
        # ⚠️ Empty, never stale. A picker completing from a list that might be
        # wrong is how somebody points a faction at a dashboard that has moved.
        serve(monkeypatch, requests.RequestException("down"))
        assert reg.slugs() == []


def test_the_default_url_is_loopback():
    # ⚠️ The control plane's local surface is published to host loopback only.
    # A default pointing anywhere else would be a routable internal endpoint by
    # accident.
    assert reg.DEFAULT_URL.startswith("http://127.0.0.1:")
