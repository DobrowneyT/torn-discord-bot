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


# ── #1008: sign-up POSTs ─────────────────────────────────────────────────────

def test_post_relays_the_dashboards_answer(monkeypatch):
    t = tenant(monkeypatch)
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return Res(409, {"ok": False, "code": "hour_full", "error": "full"})

    monkeypatch.setattr(chain_api.requests, "post", fake_post)
    out = chain_api.post_sync(t, "/signups", {"member_id": "1", "hour_start": 5})
    assert out["code"] == "hour_full"
    assert seen["url"] == "https://forge.monchoon.me/api/internal/chain-watch/signups"
    assert seen["headers"]["Authorization"] == "Bearer abc"


def test_post_failures_are_unreachable_never_a_refusal(monkeypatch):
    # ⚠️ A timeout may have landed. "Unreachable" makes the bot say it cannot
    # confirm, rather than "failed" — which invites a duplicate sign-up.
    t = tenant(monkeypatch)
    cases = [
        requests.Timeout("slow"),
        Res(502, bad_json=True),
        Res(404, {"message": "Route POST:/x not found", "statusCode": 404}),
        Res(401, {"error": "Authentication required"}),
    ]
    for case in cases:
        def fake_post(*a, _c=case, **k):
            if isinstance(_c, Exception):
                raise _c
            return _c
        monkeypatch.setattr(chain_api.requests, "post", fake_post)
        assert chain_api.post_sync(t, "/signups", {}) == {"ok": False, "code": chain_api.UNREACHABLE}
