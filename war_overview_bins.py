"""
The bin-size argument for `/rw-overview` (#811 follow-up).

⚠️ **The same widths the page's Bin size control offers** — Auto, 1m, 5m, 1h,
6h, 1d. A chart in Discord drawn at a width the page cannot produce, or
labelled differently, is how the two stop being comparable; somebody holding a
screenshot of one against the other has to be looking at the same arithmetic.

⚠️ **Parsing lives here, validation lives on the dashboard.** This turns what
somebody typed into whole seconds; whether that many bars is drawable is the
endpoint's call, because only it knows how long the war ran. Duplicating the
bar-count rule here would put the two copies one refactor apart.
"""

from typing import List, Optional, Tuple

#: What the autocomplete offers, in the page's order. `None` is the auto pick.
#:
#: ⚠️ Auto is FIRST and is the default. The automatic width is right for nearly
#: every war, and the presets exist for the case where somebody is comparing two
#: wars of different lengths and needs the bars to mean the same thing.
PRESETS: List[Tuple[str, Optional[int]]] = [
    ("Auto — pick a width to suit the war", None),
    ("1 minute", 60),
    ("5 minutes", 300),
    ("1 hour", 3600),
    ("6 hours", 21600),
    ("1 day", 86400),
]

#: Suffixes accepted on a typed width. Bare digits are seconds.
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def label(seconds: Optional[int]) -> str:
    """`3600` → `1h`, `90` → `90s`. The compact form, for a footer."""
    if seconds is None:
        return "auto"
    for suffix, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{suffix}"
    return f"{seconds}s"


def parse(text: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    """
    What somebody typed → `(seconds, error)`. `seconds is None` means auto.

    Accepts `auto`, a bare number of seconds, or a number with a `s`/`m`/`h`/`d`
    suffix — so `5m`, `300` and `300s` are the same width.

    ⚠️ An unparseable value is an ERROR, never a silent fall back to auto.
    Falling back would draw a chart at a width nobody asked for and say nothing,
    which is the failure this whole argument exists to let people avoid.
    """
    raw = (text or "").strip().lower()
    if raw in ("", "auto"):
        return None, None

    unit = 1
    if raw[-1] in UNITS:
        unit = UNITS[raw[-1]]
        raw = raw[:-1].strip()

    # ⚠️ `isdigit` rather than `int(...)` in a try: `int` accepts a leading `+`,
    # a `_` separator and assorted unicode digits, none of which anybody meant
    # to type and all of which would reach the endpoint as something else.
    if not raw.isdigit() or not raw.isascii():
        return None, (f"`{text}` is not a bin size. Pick one from the list, or type "
                      f"a width like `90s`, `15m`, `2h` or `1d`.")
    seconds = int(raw) * unit
    if seconds <= 0:
        return None, "A bin size has to be more than zero."
    return seconds, None


def choices(current: str) -> List[Tuple[str, str]]:
    """
    `(name, value)` pairs for the autocomplete, filtered by what is typed.

    ⚠️ A typed value that parses is offered back as its own choice, showing the
    width it resolves to. Discord lets somebody submit free text when a
    parameter has autocomplete, so without this they would be typing `2h` into a
    box that shows them nothing and hoping. Seeing `2h → 2h (custom)` before
    pressing enter is the difference between a guess and a choice.
    """
    cur = (current or "").strip().lower()
    out = [(name, "auto" if secs is None else str(secs))
           for name, secs in PRESETS
           if not cur or cur in name.lower() or cur == label(secs)]
    if cur:
        seconds, err = parse(cur)
        if err is None and seconds is not None and str(seconds) not in [v for _, v in out]:
            out.insert(0, (f"{label(seconds)} (custom)", str(seconds)))
    return out[:25]
