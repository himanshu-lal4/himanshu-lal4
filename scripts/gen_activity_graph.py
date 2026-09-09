#!/usr/bin/env python3
"""Render the contribution activity graph as local SVGs, from GitHub's own API.

Why this is generated here rather than mirrored
-----------------------------------------------
This card used to come from github-readme-activity-graph.vercel.app, a single
shared deployment serving thousands of profiles. On 2026-08-24 it started
answering HTTP 402 Payment Required -- its Vercel billing had run out -- and
every profile embedding it silently stopped updating. mirror_cards.py failed
safe and kept the previous copy, which is correct, but it meant the graph sat
16 days stale with the workflow still green.

The data behind that card is public and free: contributionsCollection in
GitHub's GraphQL API, the same source the profile page uses. Fetching it and
drawing the chart here removes the third party altogether, which is the same
call this repo already made for the resume's fonts and for the stat cards.

Sanitiser rules (learned the hard way, see mirror_cards.py)
-----------------------------------------------------------
GitHub strips <style> from SVGs served out of a repo, so every fill, stroke,
font and size below is a presentation attribute. No CSS, no classes, no
animation -- what you see in the file is what renders.

Run by .github/workflows/stats.yml. Needs GITHUB_TOKEN in the environment.
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

USER = os.environ.get("PROFILE_USER", "himanshu-lal4")
DAYS = 31
OUT_DIR = "assets/cards"
FONT = "'Segoe UI',Ubuntu,'Helvetica Neue',Helvetica,Arial,sans-serif"

W, H = 1200, 420
ML, MR, MT, MB = 78, 44, 78, 62          # plot margins
PW, PH = W - ML - MR, H - MT - MB        # plot area

# suffix: (line, area top, grid, axis text, title text)
THEMES = {
    "dark":  ("#2F81F7", "#2F81F7", "#21262D", "#8B949E", "#E6EDF3"),
    "light": ("#0969DA", "#0969DA", "#D0D7DE", "#57606A", "#1F2328"),
}

# Two aliased collections: passing from/to also scopes totalContributions to
# that window, so the year figure has to come from an unscoped second field.
QUERY = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    window: contributionsCollection(from:$from, to:$to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
    year: contributionsCollection {
      contributionCalendar { totalContributions }
    }
  }
}
"""


def fetch_days():
    """The last DAYS days of contribution counts, oldest first."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set -- cannot read the contribution calendar.")
    to = datetime.datetime.now(datetime.timezone.utc)
    frm = to - datetime.timedelta(days=DAYS - 1)
    payload = json.dumps({
        "query": QUERY,
        "variables": {
            "login": USER,
            "from": frm.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "to": to.isoformat(),
        },
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-activity-graph",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    # A GraphQL error is a 200 with an "errors" key -- the same shape of trap
    # that let the old rate-limit placeholders through, so check it explicitly.
    if "errors" in body:
        sys.exit(f"GitHub GraphQL error: {body['errors']}")
    user = body["data"]["user"]
    cal = user["window"]["contributionCalendar"]
    days = [d for w in cal["weeks"] for d in w["contributionDays"]]
    days.sort(key=lambda d: d["date"])
    year_total = user["year"]["contributionCalendar"]["totalContributions"]
    return days[-DAYS:], year_total


def nice_max(n):
    """A round ceiling for the y axis, so gridlines land on whole numbers."""
    if n <= 4:
        return 4
    for step in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if n <= step * 4:
            return step * 4
    return ((n + 999) // 1000) * 1000


def smooth_path(pts):
    """Monotone cubic (Fritsch-Carlson) through the points, as beziers.

    Deliberately not Catmull-Rom: that overshoots around a spike, and on a
    series whose floor is zero the curve dips below the axis and draws days
    with negative contributions. Monotone interpolation cannot overshoot, so
    a run of zero days renders as a flat line on the baseline.
    """
    n = len(pts)
    if n < 2:
        return ""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    dx = [xs[i + 1] - xs[i] for i in range(n - 1)]
    delta = [(ys[i + 1] - ys[i]) / dx[i] for i in range(n - 1)]

    m = [0.0] * n
    m[0], m[-1] = delta[0], delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0:
            m[i] = 0.0          # a local extremum: flatten so it cannot bulge
        else:
            m[i] = (delta[i - 1] + delta[i]) / 2
    for i in range(n - 1):
        if delta[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / delta[i], m[i + 1] / delta[i]
        h = a * a + b * b
        if h > 9:
            t = 3 / (h ** 0.5)
            m[i], m[i + 1] = t * a * delta[i], t * b * delta[i]

    d = [f"M {xs[0]:.2f} {ys[0]:.2f}"]
    for i in range(n - 1):
        c1x, c1y = xs[i] + dx[i] / 3, ys[i] + m[i] * dx[i] / 3
        c2x, c2y = xs[i + 1] - dx[i] / 3, ys[i + 1] - m[i + 1] * dx[i] / 3
        d.append(
            f"C {c1x:.2f} {c1y:.2f} {c2x:.2f} {c2y:.2f} "
            f"{xs[i + 1]:.2f} {ys[i + 1]:.2f}"
        )
    return " ".join(d)


def render(days, total, suffix):
    line, area, grid, axis, title = THEMES[suffix]
    counts = [d["contributionCount"] for d in days]
    top = nice_max(max(counts) if counts else 0)
    n = len(days)

    x = lambda i: ML + (PW * i / (n - 1) if n > 1 else PW / 2)
    y = lambda v: MT + PH - (PH * v / top)
    pts = [(x(i), y(c)) for i, c in enumerate(counts)]

    o = [
        f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" fill="none" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{FONT}">',
        f'<defs><linearGradient id="fill-{suffix}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{area}" stop-opacity="0.36"/>'
        f'<stop offset="100%" stop-color="{area}" stop-opacity="0"/>'
        f"</linearGradient></defs>",
        f'<text x="{W/2:.0f}" y="40" text-anchor="middle" font-size="21" '
        f'font-weight="600" fill="{title}">Contribution Graph</text>',
        f'<text x="{W/2:.0f}" y="62" text-anchor="middle" font-size="13" '
        f'fill="{axis}">{total:,} contributions in the last year '
        f"&#183; last {n} days shown</text>",
    ]

    # Horizontal gridlines and y labels.
    for k in range(5):
        v = top * k / 4
        gy = y(v)
        o.append(
            f'<line x1="{ML}" y1="{gy:.1f}" x2="{ML + PW}" y2="{gy:.1f}" '
            f'stroke="{grid}" stroke-width="1"/>'
        )
        o.append(
            f'<text x="{ML - 14}" y="{gy + 4:.1f}" text-anchor="end" '
            f'font-size="13" fill="{axis}">{int(v)}</text>'
        )

    # Area, then line.
    o.append(
        f'<path d="{smooth_path(pts)} L {pts[-1][0]:.2f} {MT + PH} '
        f'L {pts[0][0]:.2f} {MT + PH} Z" fill="url(#fill-{suffix})"/>'
    )
    o.append(
        f'<path d="{smooth_path(pts)}" fill="none" stroke="{line}" '
        f'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
    )

    # Date labels, thinned so they never collide, and a dot on each real point.
    every = max(1, n // 8)
    for i, d in enumerate(days):
        px, py = pts[i]
        if counts[i] > 0:
            o.append(
                f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3.2" fill="{line}"/>'
            )
        if i % every == 0 or i == n - 1:
            label = datetime.date.fromisoformat(d["date"]).strftime("%d %b")
            o.append(
                f'<text x="{px:.2f}" y="{MT + PH + 28}" text-anchor="middle" '
                f'font-size="13" fill="{axis}">{label}</text>'
            )

    o.append("</svg>")
    return "\n".join(o) + "\n"


def main():
    days, total = fetch_days()
    os.makedirs(OUT_DIR, exist_ok=True)
    peak = max(d["contributionCount"] for d in days) if days else 0
    print(f"  {len(days)} days, {sum(d['contributionCount'] for d in days)} "
          f"contributions in window, peak {peak}, {total:,} in the last year")
    for suffix in THEMES:
        path = f"{OUT_DIR}/activity-graph-{suffix}.svg"
        with open(path, "w", encoding="utf-8") as f:
            f.write(render(days, total, suffix))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
