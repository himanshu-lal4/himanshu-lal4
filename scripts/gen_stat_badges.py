#!/usr/bin/env python3
"""Render live stat badges (GitHub stars, npm installs) as flat local SVGs.

Local SVGs avoid GitHub's camo URL-length limit, which breaks shields.io
badges that carry a base64 icon in the query string.

Run daily by .github/workflows/stats.yml. Set GITHUB_STARS / NPM_INSTALLS to
skip the network calls (useful for local runs behind a proxy).
"""
import datetime
import json
import os
import urllib.parse
import urllib.request

USER = "himanshu-lal4"
PACKAGES = ["@wrack/react-native-tour-guide", "react-native-liquid-glassmorphism"]
START = "2024-01-01"
FONT = "Verdana,DejaVu Sans,Geneva,sans-serif"

STAR = "M12 2l2.9 6.3 6.9.8-5.1 4.7 1.4 6.8L12 17.3 5.9 20.6l1.4-6.8L2.2 9.1l6.9-.8z"
DOWNLOAD = "M12 15.6l-5-5 1.4-1.4 2.6 2.6V3h2v8.8l2.6-2.6L17 10.6l-5 5zM5 18h14v2H5z"

THEMES = {  # suffix: (label bg, label fg, value bg, value fg)
    "dark":  ("#21262D", "#FFFFFF", "#2F81F7", "#FFFFFF"),
    "light": ("#EAEEF2", "#1F2328", "#0969DA", "#FFFFFF"),
}


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "profile-badges"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_stars():
    total, page = 0, 1
    while True:
        repos = get_json(f"https://api.github.com/users/{USER}/repos?per_page=100&page={page}&type=owner")
        if not repos:
            break
        total += sum(r["stargazers_count"] for r in repos if not r["fork"])
        page += 1
    return total


def fetch_installs():
    end = datetime.date.today().isoformat()
    total = 0
    for pkg in PACKAGES:
        data = get_json(f"https://api.npmjs.org/downloads/range/{START}:{end}/{urllib.parse.quote(pkg, safe='')}")
        n = sum(d["downloads"] for d in data["downloads"])
        print(f"  {pkg}: {n:,}")
        total += n
    return total


def human(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def badge(icon, label, value, bg, label_fg, accent, accent_fg):
    """Label first, then icon, then the value block."""
    text_w = int(len(label) * 7.6)
    icon_x = 12 + text_w + 8
    lw = icon_x + 14 + 12
    vw = 24 + len(value) * 9
    w = lw + vw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="28" '
        f'viewBox="0 0 {w} 28" role="img" aria-label="{label}: {value}">\n'
        f'  <title>{label}: {value}</title>\n'
        f'  <rect width="{lw}" height="28" fill="{bg}"/>\n'
        f'  <rect x="{lw}" width="{vw}" height="28" fill="{accent}"/>\n'
        f'  <text x="12" y="18" fill="{label_fg}" font-family="{FONT}" font-size="10" '
        f'font-weight="bold" letter-spacing="1.1">{label}</text>\n'
        f'  <g transform="translate({icon_x},7) scale(0.583)" fill="{label_fg}">'
        f'<path d="{icon}"/></g>\n'
        f'  <text x="{lw + vw / 2}" y="18" fill="{accent_fg}" font-family="{FONT}" '
        f'font-size="11" font-weight="bold" text-anchor="middle">{value}</text>\n'
        f'</svg>\n'
    )


def main():
    stars = int(os.environ["GITHUB_STARS"]) if os.environ.get("GITHUB_STARS") else fetch_stars()
    installs = int(os.environ["NPM_INSTALLS"]) if os.environ.get("NPM_INSTALLS") else fetch_installs()
    print(f"stars={stars:,}  installs={installs:,}")

    os.makedirs("assets/badges", exist_ok=True)
    specs = [("stars", STAR, "GITHUB", human(stars)), ("installs", DOWNLOAD, "NPM", human(installs))]
    for name, icon, label, value in specs:
        for suffix, colors in THEMES.items():
            path = f"assets/badges/{name}-{suffix}.svg"
            with open(path, "w") as f:
                f.write(badge(icon, label, value, *colors))
            print("wrote", path)


if __name__ == "__main__":
    main()
