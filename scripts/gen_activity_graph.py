#!/usr/bin/env python3
"""Render an animated contribution graph as a self-contained SVG.

Weekly contribution totals become a line + area chart whose stroke draws in on
a loop. Animation is plain CSS keyframes inside the SVG, which browsers run
even when the file is loaded through <img> — the same mechanism Platane/snk uses.

Needs GITHUB_TOKEN. Set CONTRIB_JSON to a saved GraphQL response to run offline.
"""
import json
import os
import urllib.request

USER = "himanshu-lal4"
W, H = 880, 210
PAD_L, PAD_R, PAD_T, PAD_B = 14, 14, 34, 26
CYCLE = 8  # seconds

QUERY = """{ user(login: "%s") { contributionsCollection { contributionCalendar {
  totalContributions weeks { contributionDays { date contributionCount } } } } } }""" % USER

THEMES = {
    "light": dict(line="#0969DA", area="#0969DA", grid="#D0D7DE", label="#59636E", value="#1F2328"),
    "dark":  dict(line="#2F81F7", area="#2F81F7", grid="#30363D", label="#8B949E", value="#E6EDF3"),
}


def fetch():
    if os.environ.get("CONTRIB_JSON"):
        return json.load(open(os.environ["CONTRIB_JSON"]))
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY}).encode(),
        headers={"Authorization": f"bearer {os.environ['GITHUB_TOKEN']}",
                 "Content-Type": "application/json",
                 "User-Agent": "profile-graph"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def weekly(payload):
    cal = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    weeks = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in cal["weeks"]]
    return cal["totalContributions"], weeks


def build(total, weeks, line, area, grid, label, value):
    peak = max(weeks) or 1
    iw, ih = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    step = iw / max(len(weeks) - 1, 1)
    pts = [(PAD_L + i * step, PAD_T + ih - (v / peak) * ih) for i, v in enumerate(weeks)]

    # smooth with midpoint quadratics
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        d += f" Q{x0:.1f},{y0:.1f} {(x0+x1)/2:.1f},{(y0+y1)/2:.1f}"
    d += f" L{pts[-1][0]:.1f},{pts[-1][1]:.1f}"
    fill = d + f" L{pts[-1][0]:.1f},{PAD_T+ih:.1f} L{pts[0][0]:.1f},{PAD_T+ih:.1f} Z"

    baseline = PAD_T + ih
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{total} contributions in the last year">
  <title>{total} contributions in the last year</title>
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{area}" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="{area}" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <style>
    @keyframes draw {{
      0%   {{ stroke-dashoffset: 4000; }}
      45%  {{ stroke-dashoffset: 0; }}
      88%  {{ stroke-dashoffset: 0; }}
      100% {{ stroke-dashoffset: 4000; }}
    }}
    @keyframes fade {{
      0%,8% {{ opacity: 0; }}
      50%,88% {{ opacity: 1; }}
      100% {{ opacity: 0; }}
    }}
    #line {{ stroke-dasharray: 4000; animation: draw {CYCLE}s ease-in-out infinite; }}
    #area {{ animation: fade {CYCLE}s ease-in-out infinite; }}
  </style>
  <line x1="{PAD_L}" y1="{baseline}" x2="{W-PAD_R}" y2="{baseline}" stroke="{grid}" stroke-width="1"/>
  <text x="{PAD_L}" y="20" fill="{value}" font-family="'Segoe UI',Helvetica,Arial,sans-serif" font-size="15" font-weight="700">{total:,}<tspan fill="{label}" font-size="11" font-weight="400" letter-spacing="1.4"> CONTRIBUTIONS · LAST 12 MONTHS</tspan></text>
  <path id="area" d="{fill}" fill="url(#g)"/>
  <path id="line" d="{d}" fill="none" stroke="{line}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
  <text x="{W-PAD_R}" y="{H-8}" fill="{label}" font-family="'Segoe UI',Helvetica,Arial,sans-serif" font-size="10" text-anchor="end" letter-spacing="1.2">PEAK {peak} / WEEK</text>
</svg>
'''


def main():
    total, weeks = weekly(fetch())
    print(f"{total:,} contributions across {len(weeks)} weeks (peak {max(weeks)}/wk)")
    os.makedirs("assets", exist_ok=True)
    for name, colors in THEMES.items():
        path = f"assets/activity-{name}.svg"
        with open(path, "w") as f:
            f.write(build(total, weeks, **colors))
        print("wrote", path)


if __name__ == "__main__":
    main()
