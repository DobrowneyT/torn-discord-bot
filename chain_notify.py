"""
The dashboard telling us a board is out of date (#785 follow-up).

Polling every `board_refresh_seconds` (default 300) is right for the chain
counter and travel state, and far too slow for a sign-up — somebody claims a
slot and then stares at an unchanged board wondering whether it worked. The
dashboard nudges this listener instead, and the board redraws within seconds.

⚠️ **A unix socket, not a TCP port.** A container cannot reach the host's
127.0.0.1 (its traffic arrives via the bridge gateway), so "listen on localhost"
does not work — and the obvious fix for that is binding 0.0.0.0, which on a VPS
puts a control channel for a process holding a Discord token and five tenant
bearers on the public internet. A bind-mounted socket has no port, needs no
firewall rule, and carries no credential that could leak.

⚠️ **This is an optimisation, never the source of truth.** The poll loop keeps
running. If the socket is missing, the dashboard is old, or a nudge is dropped,
the board is at worst one poll stale — exactly where it was before this existed.
Nothing here may become load-bearing.
"""

import asyncio
import contextlib
import logging
import os
from typing import Awaitable, Callable, Dict, Optional, Set

from aiohttp import web

log = logging.getLogger("chain_notify")

#: Absent disables the listener, and the dashboard side independently.
SOCKET_ENV = "CHAIN_NOTIFY_SOCKET"

#: How long to wait for more nudges before redrawing.
#:
#: ⚠️ This is the debounce, and it is the point. Leadership filling a rota
#: assigns eight slots in twenty seconds; without coalescing that is eight
#: redraws, eight Discord edits, and a board that flickers while somebody works.
#: One redraw a beat after they stop is both cheaper and nicer to watch.
DEFAULT_DEBOUNCE_SECONDS = 5


def socket_path() -> Optional[str]:
    return os.environ.get(SOCKET_ENV) or None


class NotifyServer:
    """
    Listens on a unix socket; calls `on_change(slug)` once per quiet period.

    `on_change` is whatever redraws a tenant — injected rather than imported so
    the debounce can be tested against a fake clock, which is the only way to
    ask "did eight nudges become one redraw".
    """

    def __init__(self, on_change: Callable[[str], Awaitable[None]], *,
                 path: Optional[str] = None,
                 debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS):
        self.on_change = on_change
        self.path = path if path is not None else socket_path()
        self.debounce_seconds = debounce_seconds
        self._pending: Set[str] = set()
        self._timers: Dict[str, asyncio.Task] = {}
        self._runner: Optional[web.AppRunner] = None

    # ── receiving ────────────────────────────────────────────────────────────

    async def _handle(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:                                  # noqa: BLE001
            # ⚠️ 200 even for junk. This endpoint exists to be ignored safely;
            # an error status would give the dashboard something to retry, and
            # retrying a redraw nudge is never worth a member's request time.
            return web.json_response({"ok": False, "reason": "bad json"})
        slug = str((body or {}).get("slug") or "")
        if not slug:
            return web.json_response({"ok": False, "reason": "no slug"})
        self.nudge(slug)
        return web.json_response({"ok": True})

    def nudge(self, slug: str) -> None:
        """Record a change and (re)start that tenant's quiet period."""
        self._pending.add(slug)
        existing = self._timers.get(slug)
        if existing and not existing.done():
            # ⚠️ Cancel and restart, so a steady trickle of sign-ups does not
            # redraw every `debounce_seconds` forever — it redraws once, after
            # they stop.
            existing.cancel()
        self._timers[slug] = asyncio.ensure_future(self._fire_after_quiet(slug))

    async def _fire_after_quiet(self, slug: str) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
        except asyncio.CancelledError:
            return
        self._pending.discard(slug)
        try:
            await self.on_change(slug)
        except Exception:                                  # noqa: BLE001
            # ⚠️ Never propagate. This runs in a bare task; an escaping
            # exception is an unretrievable warning on stderr and a redraw that
            # silently stopped happening.
            log.exception("redraw after nudge failed for %s", slug)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> bool:
        """Returns True when listening. False means disabled or unavailable."""
        if not self.path:
            log.info("%s not set — the board updates on its poll interval only",
                     SOCKET_ENV)
            return False
        directory = os.path.dirname(self.path)
        if directory:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as e:
                # ⚠️ A path we cannot create is a misconfiguration, not a
                # reason to fail startup. Push is an optimisation; falling back
                # to the poll interval is the old behaviour, not a failure.
                log.warning("cannot create %s (%s) — poll interval only", directory, e)
                return False
        # ⚠️ A leftover socket file from an unclean shutdown makes bind() fail
        # with EADDRINUSE, and the bot would then start with push silently off
        # forever. Removing it is safe: if another process really is listening,
        # the bind still fails and we log that.
        with contextlib.suppress(FileNotFoundError):
            if os.path.exists(self.path) and not _is_live_socket(self.path):
                os.unlink(self.path)
        app = web.Application()
        app.router.add_post("/changed", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        try:
            await web.UnixSite(self._runner, self.path).start()
        except OSError as e:
            log.warning("could not listen on %s (%s) — poll interval only", self.path, e)
            await self.stop()
            return False
        # ⚠️ World-writable on purpose: the dashboard container may run as a
        # different user. The socket accepts only "redraw this slug", which is
        # not worth a credential — and anything able to write here is already
        # inside the box.
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o666)
        log.info("listening for board nudges on %s (debounce %ss)",
                 self.path, self.debounce_seconds)
        return True

    async def stop(self) -> None:
        for task in self._timers.values():
            task.cancel()
        self._timers.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


def _is_live_socket(path: str) -> bool:
    """Is something actually accepting on this socket right now?"""
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()
