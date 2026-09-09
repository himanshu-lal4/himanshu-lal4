#!/usr/bin/env python3
"""Mirror the third-party stat cards into local SVGs, once a day, in CI.

Why this exists
---------------
The cards used to be fetched live by every visitor. Two failure modes made
that unacceptable:

1. github-profile-summary-cards is a single shared deployment serving
   thousands of profiles through one GitHub API token pool. When it runs dry
   it answers with a placeholder image reading "Cards are temporarily rate
   limited" / "Failed to retrieve contributions".
2. Those placeholders come back as a normal SVG with HTTP 200, and the
   services set Cache-Control: max-age=14400 (4h) and 1800 (30m). GitHub's
   camo proxy caches what it is told to, so a single unlucky fetch pinned a
   red ERROR block to the profile for hours.

Fetching here instead costs nothing when it fails: the previous good copy is
kept, so the README is at worst a day stale and never broken.

That fallback has one failure mode of its own, and it bit: on 2026-08-24 the
activity graph's host started returning 402 Payment Required, the mirror
dutifully kept the previous copy, and the card sat 16 days stale behind a
green tick because a ::warning:: does not fail a job. STALE_DAYS below now
escalates a card that has not refreshed in that long to a hard failure, and
the activity graph itself is no longer mirrored at all -- it is drawn from
GitHub's own API by scripts/gen_activity_graph.py.

Sanitiser-proofing
------------------
GitHub strips <style> from SVGs served out of a repo. Every one of these
cards puts something in <style>, so the raw response cannot be committed
as-is:

  * the summary cards use it only for font-family -> lifted onto <svg> as a
    presentation attribute, which the sanitiser keeps.

Run daily by .github/workflows/stats.yml.
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.request

USER = os.environ.get("PROFILE_USER", "himanshu-lal4")
UTC_OFFSET = "5.5"
CARD_DIR = "assets/cards"

# Last-good dates per card, committed alongside them. actions/checkout is
# shallow by default so git history is not available to date the files, and a
# checkout's mtimes are all fresh -- the record has to be carried in-repo.
STATE = f"{CARD_DIR}/.mirror-state.json"
# A card that has not refreshed in this many days is broken, not unlucky.
STALE_DAYS = 5

SUMMARY = "https://github-profile-summary-cards.vercel.app/api/cards"
STREAK = "https://streak-stats.demolab.com/"

# Font stack the cards ask for, minus the Windows-only head so it degrades
# sensibly on the Linux and macOS machines that actually render the README.
FONT = "'Segoe UI',Ubuntu,'Helvetica Neue',Helvetica,Arial,sans-serif"

# An error payload is still an SVG with HTTP 200, so status codes prove
# nothing. These are the strings the two services put in their placeholders.
ERROR_MARKERS = (
    "rate limited",
    "ERROR!!!",
    "Failed to retrieve",
    "Something went wrong",
    "Maximum retries exceeded",
    "Could not fetch",
)

def targets():
    """(path, url) for every mirrored image."""
    theme_pairs = (("dark", "github_dark"), ("light", "github"))
    for card, extra in (("profile-details", ""), ("stats", ""),
                        ("productive-time", f"&utcOffset={UTC_OFFSET}")):
        for suffix, theme in theme_pairs:
            yield (f"{CARD_DIR}/{card}-{suffix}.svg",
                   f"{SUMMARY}/{card}?username={USER}&theme={theme}{extra}")
    # disable_animations is REQUIRED: the animated variant ships a <style>
    # block of opacity:0 rules that fade elements in. GitHub strips it from a
    # repo-served SVG, leaving every element at opacity 0 - an empty box.
    yield ("assets/streak-card.svg",
           f"{STREAK}?user={USER}&hide_border=true&background=00000000"
           f"&stroke=30363d&ring=F97316&fire=F97316&currStreakLabel=F97316"
           f"&sideLabels=8b949e&dates=8b949e&sideNums=8b949e"
           f"&currStreakNum=8b949e&excludeDaysLabel=8b949e"
           f"&disable_animations=true")


def fetch(url, attempts=3):
    """Return SVG text, or None if every attempt failed or looked like an error."""
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "profile-card-mirror"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8", "replace")
        except Exception as exc:                      # noqa: BLE001 - any failure retries
            print(f"    attempt {attempt}: {type(exc).__name__}: {exc}")
            body = None
        if body:
            if "<svg" not in body[:400]:
                print(f"    attempt {attempt}: response is not an SVG")
            elif any(m.lower() in body.lower() for m in ERROR_MARKERS):
                print(f"    attempt {attempt}: service returned its error placeholder")
            elif len(body) < 500:
                print(f"    attempt {attempt}: suspiciously small ({len(body)} bytes)")
            else:
                return body
        if attempt < attempts:
            time.sleep(20)
    return None


TAG_RE = re.compile(r"<(/?)([A-Za-z][\w:-]*)((?:\s+[\w:-]+=\"[^\"]*\")*)\s*(/?)>")
VOID_SVG_TAGS = {"path", "line", "rect", "circle", "ellipse", "polyline",
                 "polygon", "image", "use", "stop", "br"}


def set_root_font(svg):
    """font-family on <svg> is inherited and survives sanitising."""
    def once(match):
        if "font-family" in match.group(0):
            return match.group(0)
        return match.group(0)[:-1] + f' font-family="{FONT}">'
    return re.sub(r"<svg[^>]*>", once, svg, count=1)


def usable_copy(path):
    """True if the file on disk is a real card rather than a stored error page.

    Without this check a placeholder committed by an earlier run would satisfy
    "keep the previous copy" forever, and the profile would show the error
    indefinitely while CI reported nothing worse than a warning. That is
    exactly how the streak card sat broken.
    """
    if not os.path.exists(path):
        return False
    with open(path) as f:
        body = f.read()
    return not any(m.lower() in body.lower() for m in ERROR_MARKERS)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def main():
    today = datetime.date.today()
    state = load_state()
    failures = []
    for path, url in targets():
        name = os.path.basename(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print(f"  {name}")
        body = fetch(url)
        if body is None:
            have = usable_copy(path)
            print(f"    FAILED - {'keeping previous copy' if have else 'NO USABLE PREVIOUS COPY'}")
            failures.append((name, have))
            continue
        svg = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.S)
        svg = set_root_font(svg)
        with open(path, "w") as f:
            f.write(svg)
        state[name] = today.isoformat()
        print(f"    ok ({len(svg)} bytes)")

    stale = []
    for name, have in failures:
        # An unseen card is recorded now rather than treated as ancient, so a
        # newly added one gets its full grace period.
        last = state.setdefault(name, today.isoformat())
        age = (today - datetime.date.fromisoformat(last)).days
        if age >= STALE_DAYS:
            stale.append((name, age))

    with open(STATE, "w") as f:
        json.dump(dict(sorted(state.items())), f, indent=2)
        f.write("\n")

    for name, have in failures:
        level = "warning" if have else "error"
        print(f"::{level}::{name} could not be refreshed"
              f"{' and the stored copy is unusable - the README is showing a broken card' if not have else ''}")
    for name, age in stale:
        print(f"::error::{name} has not refreshed in {age} days - the README is "
              f"showing stale data and the upstream service is not coming back "
              f"on its own")

    # Fail on a card that would render broken, or one that has quietly stopped
    # updating. A single bad fetch still only warns.
    if stale or any(not have for _, have in failures):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
