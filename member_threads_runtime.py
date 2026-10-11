"""
The member-report drain loop (#943).

⚠️ **Its own loop, slower than Chain Watch's.** A board is live data and wants a
short interval; member reports are a post-war event that happens a handful of
times a month. Polling them on the board's cadence would mean thousands of
pointless claims a day against five dashboards.

⚠️ **The same process, never a second one.** Two processes on one token open two
gateway connections and Discord routes each interaction to only one of them —
`bot.py` says this about Chain Watch and it is just as true here.

⚠️ **This loop must never die, and must never take the host down.** The OC
watcher has been running for months and is not what changed. Every tick is
wrapped; a faction whose dashboard is unreachable, whose forum is misconfigured,
or whose thread was deleted must not stop the other four.
"""

import asyncio
import logging
import os

import chain_tenants
import member_threads_poster as poster

log = logging.getLogger("member_threads_runtime")

#: ⚠️ Two minutes. The mailbox is filled by a war ending, so nothing is urgent
#: to the second — but a lead who issues a correction should see it land while
#: they are still looking. Tunable by env for the same reason Chain Watch's is.
DEFAULT_INTERVAL_SECONDS = 120

INTERVAL_ENV = "MEMBER_REPORT_POLL_SECONDS"


def interval() -> int:
    try:
        n = int(os.environ.get(INTERVAL_ENV, DEFAULT_INTERVAL_SECONDS))
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    # ⚠️ Floored. A one-second poll across five tenants is a self-inflicted
    # denial of service on the dashboards, and `claim` is a WRITE.
    return max(30, min(n, 3600))


def enabled() -> bool:
    """
    ⚠️ **Gated on at least one faction having a forum set, not on a token.** The
    control plane mints a member-threads token for every tenant, so gating on
    the token would turn this on everywhere the moment the registry answers.
    Somebody has to point it at a forum first.
    """
    return any(poster.forum_id(t.slug) for t in chain_tenants.all_tenants())


class MemberThreadsRuntime:
    def __init__(self, client):
        self.client = client
        self._task = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def tick_all(self) -> None:
        for tenant in chain_tenants.all_tenants():
            try:
                counts = await poster.drain_once(self.client, tenant.slug)
            except Exception:                              # noqa: BLE001
                # ⚠️ Per tenant, so one faction's problem is one faction's
                # problem. The alternative — one try around the whole sweep —
                # means the first failing faction silences every faction after
                # it in the list, which is both a bug and impossible to notice.
                log.exception("member-report drain failed for %s", tenant.slug)
                continue
            if counts["posted"] or counts["failed"]:
                log.info("member reports %s: %s posted, %s failed",
                         tenant.slug, counts["posted"], counts["failed"])

    async def _run(self) -> None:
        while not self.client.is_closed():
            try:
                await self.tick_all()
            except Exception:                              # noqa: BLE001
                log.exception("member-report tick failed")
            await asyncio.sleep(interval())
