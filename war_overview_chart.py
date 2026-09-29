"""
Drawing a war's chart for Discord (#811 slice 4).

⚠️ **This module draws bars. It decides nothing about what goes in them.**
Binning, faction ranking, colour assignment and the event classifier all happen
on the dashboard, next to the summary the chart has to agree with — see
`server/src/warOverview/series.ts`. Porting any of that here would be a THIRD
implementation of logic that has drifted three times in a week.

The shape it is handed:

    { bin_seconds, x_ms: [...],
      panels: [ { key, title, series: [ { label, colour, values: [...] } ] } ] }

⚠️ Lineage worth knowing: this is the surviving half of `scripts/analyze_war.py`
in the dashboard repo — deleted in `cc3bc28`, recoverable from `cc3bc28^`. That
script rendered exactly this chart with matplotlib and its own Supabase reads;
the reads are gone, the drawing is here, and the `tab20` palette the dashboard
page still cites in its own comments comes from it.
"""

import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("war_overview_chart")

#: Roughly the dashboard's 14×11 figure, at a dpi Discord will not downscale.
FIG_WIDTH_IN = 13.0
PANEL_HEIGHT_IN = 3.2
DPI = 110

#: Discord rejects an attachment over 25 MB on a free guild. A PNG of this
#: shape is ~150 KB, so the cap is a guard against a pathological war rather
#: than a real limit — but an oversized upload fails the whole message, so it is
#: checked rather than assumed.
MAX_BYTES = 8 * 1024 * 1024


def _fmt_axis(ax, x_dates, bin_seconds: int) -> None:
    import matplotlib.dates as mdates
    from matplotlib.ticker import MaxNLocator

    # ⚠️ Whole numbers only. These are COUNTS, and matplotlib's default ticker
    # happily offers 2.5 and 7.5 on a small range — "2.5 revives" reads as a
    # unit error in the data rather than a tick-placement choice.
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    span = (x_dates[-1] - x_dates[0]).total_seconds() if len(x_dates) > 1 else bin_seconds
    # ⚠️ Date format chosen from the SPAN, not the bin width. A twelve-day event
    # binned hourly needs day labels; an hour binned by the minute needs times.
    fmt = "%H:%M" if span <= 36 * 3600 else "%d %b %H:%M"
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt))
    ax.grid(axis="y", alpha=0.3)


def render(chart: Dict[str, Any], *, title: str = "") -> Optional[bytes]:
    """
    Draw the panels and return PNG bytes, or None with the reason logged.

    ⚠️ Returning None rather than raising: a chart that cannot be drawn must
    still leave the SUMMARY postable. Losing the numbers because the picture
    failed would be the worse trade.
    """
    panels: List[Dict] = [p for p in (chart or {}).get("panels") or [] if p.get("series")]
    if not panels:
        # Not an error — a war with no events in the window, or every panel
        # empty after filtering. The caller posts the summary alone.
        return None

    try:
        # ⚠️ Imported HERE, not at module scope. matplotlib costs ~1s to import
        # and pulls in a lot; the bot's startup is a gateway handshake with a
        # timeout, and every other command would pay for a feature it does not
        # use.
        import matplotlib
        # ⚠️ Agg before pyplot. The VPS has no display, and the default backend
        # would try to find one and fail at import time.
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:                                        # noqa: BLE001
        log.warning("matplotlib unavailable — posting the summary alone: %s", e)
        return None

    x_ms = chart.get("x_ms") or []
    if not x_ms:
        return None
    try:
        # ⚠️ Epoch MILLISECONDS on the wire. Never parse a timestamp string
        # here: Postgres's bare two-digit offset is rejected outright by
        # `datetime.fromisoformat` on older runtimes.
        #
        # ⚠️ Guarded, because this used to sit outside the try and a malformed
        # payload raised straight through `render` — breaking the one promise
        # this function makes, which is that a chart it cannot draw still leaves
        # the SUMMARY postable. A test caught it.
        x_dates = [datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc) for ms in x_ms]
        bin_seconds = int(chart.get("bin_seconds") or 3600)
    except (TypeError, ValueError, OSError, OverflowError) as e:
        log.warning("war chart has unusable timestamps: %s", e)
        return None
    # matplotlib bar widths on a date axis are in DAYS.
    width = bin_seconds / 86400.0

    fig = None
    try:
        fig, axes = plt.subplots(
            len(panels), 1, sharex=True,
            figsize=(FIG_WIDTH_IN, PANEL_HEIGHT_IN * len(panels)))
        if len(panels) == 1:
            axes = [axes]

        for ax, panel in zip(axes, panels):
            bottom = [0.0] * len(x_dates)
            for s in panel["series"]:
                values = s.get("values") or []
                # ⚠️ A series shorter than the axis would raise; pad rather than
                # drop it, so a truncated payload still renders what it has.
                values = (values + [0] * len(x_dates))[:len(x_dates)]
                ax.bar(x_dates, values, width=width, bottom=bottom,
                       label=s.get("label") or "?", color=s.get("colour") or "#888888")
                bottom = [b + v for b, v in zip(bottom, values)]
            ax.set_ylabel(panel.get("title") or panel.get("key") or "")
            _fmt_axis(ax, x_dates, bin_seconds)
            # ⚠️ Legend outside the axes. Inside, a busy war hides its own bars
            # behind the key.
            ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0),
                      fontsize=8, frameon=False)

        if title:
            fig.suptitle(title, fontsize=12)
        fig.tight_layout(rect=(0, 0, 1, 0.97 if title else 1))

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight")
        data = buf.getvalue()
    except Exception as e:                                        # noqa: BLE001
        log.warning("could not render the war chart: %s", e)
        return None
    finally:
        # ⚠️ Always closed. A figure left open leaks until the process restarts,
        # and this runs per command on a long-lived bot.
        if fig is not None:
            try:
                plt.close(fig)
            except Exception:                                     # noqa: BLE001
                pass

    if len(data) > MAX_BYTES:
        log.warning("war chart is %d bytes — too large to attach", len(data))
        return None
    return data
