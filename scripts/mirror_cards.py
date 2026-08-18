#!/usr/bin/env python3
"""Mirror the third-party stat cards into local SVGs, once a day, in CI.

Why this exists
---------------
The cards used to be fetched live by every visitor. Two failure modes made
that unacceptable:

1. github-profile-summary-cards and github-readme-activity-graph are single
   shared deployments serving thousands of profiles through one GitHub API
   token pool. When it runs dry they answer with a placeholder image reading
   "Cards are temporarily rate limited" / "Failed to retrieve contributions".
2. Those placeholders come back as a normal SVG with HTTP 200, and the
   services set Cache-Control: max-age=14400 (4h) and 1800 (30m). GitHub's
   camo proxy caches what it is told to, so a single unlucky fetch pinned a
   red ERROR block to the profile for hours.

Fetching here instead costs nothing when it fails: the previous good copy is
kept, so the README is at worst a day stale and never broken.

Sanitiser-proofing
------------------
GitHub strips <style> from SVGs served out of a repo. Every one of these
cards puts something in <style>, so the raw response cannot be committed
as-is:

  * the summary cards use it only for font-family -> lifted onto <svg> as a
    presentation attribute, which the sanitiser keeps.
  * the activity graph is Chartist output where fill/stroke/font-size all
    live in class rules, and the line ships stroke-dashoffset:5000 that an
    animation winds down to 0. Stripped of CSS it renders as an empty box.
    The rules are parsed and inlined as presentation attributes, animations
    resolved to their final state.

Run daily by .github/workflows/stats.yml.
"""
import os
import re
import sys
import time
import urllib.request

USER = os.environ.get("PROFILE_USER", "himanshu-lal4")
UTC_OFFSET = "5.5"
OUT_DIR = "assets/cards"

SUMMARY = "https://github-profile-summary-cards.vercel.app/api/cards"
ACTIVITY = "https://github-readme-activity-graph.vercel.app/graph"

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

# Properties worth carrying over to presentation attributes. Anything else
# (layout, webkit prefixes, animation) either does nothing in an SVG or is
# the thing we are deliberately dropping.
KEEP = {
    "fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity",
    "stroke-dasharray", "stroke-linecap", "stroke-linejoin", "font-size",
    "font-weight", "font-family", "text-anchor", "opacity",
}


def targets():
    theme_pairs = (("dark", "github_dark"), ("light", "github"))
    for card, extra in (("profile-details", ""), ("stats", ""),
                        ("productive-time", f"&utcOffset={UTC_OFFSET}")):
        for suffix, theme in theme_pairs:
            yield (f"{card}-{suffix}.svg",
                   f"{SUMMARY}/{card}?username={USER}&theme={theme}{extra}",
                   "summary")
    yield ("activity-graph.svg",
           f"{ACTIVITY}?username={USER}&bg_color=00000000&color=8b949e"
           f"&line=2f81f7&point=2f81f7&area=true&hide_border=true",
           "activity")


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


def split_declarations(body):
    out = {}
    for decl in body.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        if not val:
            continue
        if prop == "font":
            # `font: 600 18px 'Segoe UI', Ubuntu` -> weight + size, family from FONT
            m = re.match(r"(\d{3})?\s*([\d.]+(?:px|rem|em))\s+(.*)", val)
            if m:
                if m.group(1):
                    out["font-weight"] = m.group(1)
                out["font-size"] = m.group(2)
                out["font-family"] = m.group(3)
            continue
        if prop == "color":
            out.setdefault("fill", val)               # SVG text is filled, not coloured
            continue
        if prop in KEEP:
            out[prop] = val
    return out


def to_px(val):
    m = re.match(r"^([\d.]+)rem$", val)
    if m:
        return f"{float(m.group(1)) * 16:g}px"
    return val


def parse_rules(css):
    """[(ancestor_classes, own_classes, {prop: value})] in source order.

    Chartist leans on descendant selectors - the area's colour comes from
    `.ct-series-a .ct-area` where ct-series-a sits on a parent <g> - so the
    ancestor half has to be tracked, not flattened away.
    """
    css = re.sub(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\}\s*)*\}", "", css, flags=re.S)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        decls = split_declarations(body)
        if not decls:
            continue
        decls = {k: to_px(v) for k, v in decls.items()}
        for selector in selectors.split(","):
            selector = selector.strip()
            # Only plain class selectors are honoured. A bare `svg`/`body` rule
            # would hit everything; the root font attribute covers those.
            if not selector or not re.fullmatch(r"[.\sA-Za-z0-9_-]+", selector):
                continue
            parts = selector.split()
            own = set(re.findall(r"\.([A-Za-z0-9_-]+)", parts[-1]))
            ancestors = set(re.findall(r"\.([A-Za-z0-9_-]+)", " ".join(parts[:-1])))
            if own:
                rules.append((ancestors, own, decls))
    return rules


TAG_RE = re.compile(r"<(/?)([A-Za-z][\w:-]*)((?:\s+[\w:-]+=\"[^\"]*\")*)\s*(/?)>")
VOID_SVG_TAGS = {"path", "line", "rect", "circle", "ellipse", "polyline",
                 "polygon", "image", "use", "stop", "br"}


def flatten_foreign_objects(svg):
    """Turn <foreignObject><h1 class=...> into a real SVG <text>.

    The activity graph puts its title in a foreignObject full of XHTML. HTML
    takes its colour from CSS, never from SVG presentation attributes, so once
    GitHub strips the <style> the title renders in the default near-black -
    invisible on a dark README. foreignObject is also the first thing an SVG
    sanitiser drops, since it can carry arbitrary markup. Rewriting it as a
    <text> keeps the title and lets the class rules reach it.
    """
    def replace(match):
        block = match.group(0)
        x = float(re.search(r'x="([\d.]+)"', block).group(1))
        width = float(re.search(r'width="([\d.]+)"', block).group(1))
        body = block.split(">", 1)[1]          # drop the <foreignObject ...> tag
        inner = re.search(r"<(\w+)([^>]*)>(.*?)</\1>", body, re.S)
        if not inner:
            return ""
        cls_match = re.search(r'class="([^"]*)"', inner.group(2))
        cls = cls_match.group(1) if cls_match else ""
        text = " ".join(re.sub(r"<[^>]+>", "", inner.group(3)).split())
        if not text:
            return ""
        size = re.search(r'font-size="([\d.]+)', block)
        baseline = 36.0 if not size else 16 + float(size.group(1))
        return (f'<text x="{x + width / 2:g}" y="{baseline:g}" text-anchor="middle"'
                f' class="{cls}">{text}</text>')

    return re.sub(r"<foreignObject.*?</foreignObject>", replace, svg, flags=re.S)


def inline_css(svg):
    """Fold class rules into presentation attributes and drop the <style>."""
    rules = []
    for css in re.findall(r"<style[^>]*>(.*?)</style>", svg, re.S):
        rules.extend(parse_rules(css))
    svg = re.sub(r"<style[^>]*>.*?</style>", "", svg, flags=re.S)
    svg = flatten_foreign_objects(svg)

    out, pos, stack = [], 0, []
    for m in TAG_RE.finditer(svg):
        out.append(svg[pos:m.start()])
        pos = m.end()
        closing, tag, attrs, self_closing = m.groups()

        if closing:
            if stack and stack[-1][0] == tag:
                stack.pop()
            out.append(m.group(0))
            continue

        cls = re.search(r'class="([^"]*)"', attrs)
        names = set(cls.group(1).split()) if cls else set()
        inherited = set().union(*[s for _, s in stack]) if stack else set()

        decls = {}
        for ancestors, own, d in rules:
            if own <= names and ancestors <= inherited:
                decls.update(d)
        if "ct-line" in names:
            # The dash pair exists only to drive the draw-on animation, which
            # the sanitiser strips. Left in place the line never appears.
            decls.pop("stroke-dasharray", None)
        decls.pop("opacity", None)                    # fade-in start state

        existing = {a.lower() for a in re.findall(r"([\w:-]+)=", attrs)}
        added = "".join(f' {k}="{v}"' for k, v in decls.items() if k not in existing)
        out.append(f"<{tag}{attrs}{added}{' /' if self_closing else ''}>")

        if not self_closing and tag not in VOID_SVG_TAGS:
            stack.append((tag, names))

    out.append(svg[pos:])
    return "".join(out)


def set_root_font(svg):
    """font-family on <svg> is inherited and survives sanitising."""
    def once(match):
        if "font-family" in match.group(0):
            return match.group(0)
        return match.group(0)[:-1] + f' font-family="{FONT}">'
    return re.sub(r"<svg[^>]*>", once, svg, count=1)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    failures = []
    for name, url, kind in targets():
        path = os.path.join(OUT_DIR, name)
        print(f"  {name}")
        body = fetch(url)
        if body is None:
            have = os.path.exists(path)
            print(f"    FAILED - {'keeping previous copy' if have else 'NO PREVIOUS COPY'}")
            failures.append((name, have))
            continue
        svg = inline_css(body) if kind == "activity" else re.sub(
            r"<style[^>]*>.*?</style>", "", body, flags=re.S)
        svg = set_root_font(svg)
        with open(path, "w") as f:
            f.write(svg)
        print(f"    ok ({len(svg)} bytes)")

    if failures:
        for name, have in failures:
            level = "warning" if have else "error"
            print(f"::{level}::{name} could not be refreshed"
                  f"{' and has no previous copy' if not have else ''}")
        # Only fail the job if a card would render as a missing image.
        if any(not have for _, have in failures):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
