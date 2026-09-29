"""
Drawing a war's chart (#811 slice 4).

⚠️ This module draws bars and decides nothing about what goes in them — the
dashboard bins, ranks and colours, next to the summary the chart must agree
with. So these tests are about RENDERING: that a PNG comes out, that a bad
payload degrades instead of raising, and that failure leaves the numbers
postable.
"""

import war_overview_chart as chart

CHART = {
    "bin_seconds": 3600,
    "x_ms": [1790449200000 + i * 3600000 for i in range(6)],
    "panels": [
        {"key": "outgoing", "title": "Outgoing", "series": [
            {"label": "Damage Inc [20747]", "colour": "#1f77b4", "values": [3, 5, 0, 2, 1, 0]},
            {"label": "(no faction)", "colour": "#aec7e8", "values": [0, 1, 0, 0, 0, 0]},
        ]},
        {"key": "incoming", "title": "Incoming", "series": [
            {"label": "Damage Inc [20747]", "colour": "#1f77b4", "values": [2, 2, 1, 0, 0, 1]},
        ]},
        {"key": "revives", "title": "Revives", "series": [
            {"label": "Revived", "colour": "#3b82f6", "values": [1, 0, 2, 0, 0, 0]},
            {"label": "Failed revive", "colour": "#f97316", "values": [0, 1, 0, 0, 0, 0]},
        ]},
    ],
}


def _is_png(data):
    # PNG magic. Asserting on the bytes beats asserting "not None" — a stub that
    # returned b"" would pass the weaker check.
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def test_renders_a_png():
    data = chart.render(CHART, title="vs Damage Inc [20747]")
    assert data and _is_png(data)


def test_the_png_is_small_enough_to_attach():
    # ⚠️ An oversized attachment fails the WHOLE message, taking the summary
    # with it — so the size is checked rather than assumed.
    data = chart.render(CHART)
    assert len(data) < chart.MAX_BYTES


def test_renders_with_a_single_panel():
    one = {**CHART, "panels": CHART["panels"][:1]}
    assert _is_png(chart.render(one))


def test_an_empty_chart_is_none_not_a_blank_image():
    # ⚠️ Not an error — a war with no events in the window, or every panel empty
    # after filtering. The caller posts the summary alone, and a blank PNG would
    # look like a broken chart rather than an honest absence.
    assert chart.render({}) is None
    assert chart.render({"panels": []}) is None
    assert chart.render({"panels": [{"key": "x", "title": "X", "series": []}]}) is None
    assert chart.render({"bin_seconds": 3600, "x_ms": [], "panels": CHART["panels"]}) is None


def test_panels_with_no_series_are_dropped_not_drawn_empty():
    mixed = {**CHART, "panels": [CHART["panels"][0], {"key": "e", "title": "E", "series": []}]}
    assert _is_png(chart.render(mixed))


def test_a_short_series_is_padded_rather_than_raising():
    # ⚠️ A truncated payload should still render what it has. Zipping a short
    # list against the axis would raise and lose the summary with it.
    short = {**CHART, "panels": [{"key": "o", "title": "O", "series": [
        {"label": "Partial", "colour": "#1f77b4", "values": [1, 2]},
    ]}]}
    assert _is_png(chart.render(short))


def test_a_series_missing_its_colour_still_draws():
    odd = {**CHART, "panels": [{"key": "o", "title": "O", "series": [
        {"label": "No colour", "values": [1, 0, 1, 0, 0, 0]},
    ]}]}
    assert _is_png(chart.render(odd))


def test_a_malformed_payload_returns_none_instead_of_raising():
    # ⚠️ The whole contract: a chart that cannot be drawn must leave the
    # SUMMARY postable. Raising here would lose both.
    assert chart.render({"bin_seconds": "nonsense", "x_ms": ["not a number"],
                         "panels": CHART["panels"]}) is None


def test_uses_a_headless_backend():
    # ⚠️ The VPS has no display. Without Agg, importing pyplot tries to find one
    # and fails at import time — which would take the command down, not the
    # chart.
    chart.render(CHART)
    import matplotlib
    assert matplotlib.get_backend().lower() == "agg"


def test_does_not_leak_figures():
    # ⚠️ This runs per command on a long-lived bot; a figure left open leaks
    # until restart.
    import matplotlib.pyplot as plt
    for _ in range(4):
        chart.render(CHART)
    assert plt.get_fignums() == []


def test_the_y_axis_ticks_are_whole_numbers():
    # ⚠️ These are COUNTS. matplotlib's default ticker offers 2.5 and 7.5 on a
    # small range, and "2.5 revives" reads as a unit error in the data rather
    # than a tick-placement choice.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    small = {**CHART, "panels": [{"key": "r", "title": "Revives", "series": [
        {"label": "Revived", "colour": "#3b82f6", "values": [1, 2, 3, 2, 1, 0]},
    ]}]}
    assert chart.render(small)

    fig, ax = plt.subplots()
    chart._fmt_axis(ax, [
        __import__("datetime").datetime.fromtimestamp(ms / 1000,
            tz=__import__("datetime").timezone.utc) for ms in CHART["x_ms"]
    ], 3600)
    ax.bar(range(6), [1, 2, 3, 2, 1, 0])
    fig.canvas.draw()
    ticks = ax.get_yticks()
    plt.close(fig)
    assert all(float(t).is_integer() for t in ticks), ticks
