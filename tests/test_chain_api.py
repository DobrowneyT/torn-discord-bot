"""
The dashboard client (#784, #785).

⚠️ Every test here is about a FAILURE staying local. Five factions share this
bot; one dashboard being down, slow or holding a stale token must not raise into
the poll loop and take the other four offline with it.
"""

import requests

import chain_api
import chain_tenants


def tenant(monkeypatch, token="abc"):
    chain_tenants.add("forge", "https://forge.monchoon.me", 1, 10)
    if token:
        monkeypatch.setenv("CHAIN_WATCH_TOKEN_FORGE", token)
    return chain_tenants.get("forge")


class Res:
    def __init__(self, status, payload=None, bad_json=False):
        self.status_code = status
        self._payload = payload
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


def test_sends_the_bearer_to_the_right_url(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        return Res(200, {"event": None})

    monkeypatch.setattr(requests, "get", fake_get)
    assert chain_api.fetch_sync(tenant(monkeypatch)) == {"event": None}
    assert seen["url"] == "https://forge.monchoon.me/api/internal/chain-watch"
    assert seen["headers"]["Authorization"] == "Bearer abc"
    # ⚠️ Shorter than any sensible poll interval, so a dead dashboard shows as a
    # stale board within one cycle instead of piling requests up behind it.
    assert seen["timeout"] == chain_api.TIMEOUT_SECONDS


def test_a_missing_token_returns_none_rather_than_calling(monkeypatch):
    called = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: called.append(1))
    assert chain_api.fetch_sync(tenant(monkeypatch, token=None)) is None
    assert not called


def test_every_failure_shape_returns_none_instead_of_raising(monkeypatch):
    t = tenant(monkeypatch)

    def boom(*a, **k):
        raise requests.ConnectionError("down")

    for fake in (boom,
                 lambda *a, **k: Res(401),
                 lambda *a, **k: Res(500),
                 lambda *a, **k: Res(200, bad_json=True)):
        monkeypatch.setattr(requests, "get", fake)
        assert chain_api.fetch_sync(t) is None


def test_a_timeout_is_caught_too(monkeypatch):
    # ⚠️ requests.Timeout is a RequestException, but asserting it explicitly is
    # worth it: a slow dashboard is the likeliest failure, not a down one.
    def slow(*a, **k):
        raise requests.Timeout("too slow")

    monkeypatch.setattr(requests, "get", slow)
    assert chain_api.fetch_sync(tenant(monkeypatch)) is None


def test_fetch_slug_on_an_unknown_faction(monkeypatch):
    import asyncio
    assert asyncio.run(chain_api.fetch_slug("nope")) is None
