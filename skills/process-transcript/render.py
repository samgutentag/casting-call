"""
render — fill the templates. Layout is deterministic; the model supplies language.

Three modes, one per page kind:

    python3 render.py call  --digest d.json --lang lang.py --out page.html \
                            [--transcript call.txt]
    python3 render.py day   --day 26-08-25 --dir <daydir> --lang daylang.py --out day.html
    python3 render.py index --root <target> --lang indexlang.py --out index.html

A `--lang` file is plain Python holding only strings. What each mode expects is
documented in SKILL.md. This exists so a fan-out over many days produces one
consistent layout: hand-written HTML drifts, a shared renderer cannot.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).parent
e = html.escape

KIND = {
    "action-me": ("Yours", "mine"), "action-them": ("Theirs", "theirs"),
    "question": ("Question", "ask"), "action": ("Owner unclear", ""),
    "important": ("Important", ""), "quote": ("Quote", ""),
    "video": ("On screen", ""), "note": ("Note", ""), "topic": ("Topic", ""),
    "flag": ("Flag", ""), "follow": ("Follow up", ""),
    "marker": ("Marked", "mark"),
}
# Types whose evidence fold carries a screen grab.
SCREEN_TYPES = ("video", "marker")
# INFERRED owner -> item type. The sweep files into the same buckets as presses.
OWNER = {"me": "action-me", "them": "action-them", "unclear": "action",
         "question": "question"}
BUCKET_OF = {"action-me": "yours", "action-them": "theirs", "question": "questions",
             "action": "unowned"}
LINE_RE = re.compile(r"^\[(\d+):(\d\d):(\d\d)\]\s+\[([^\]]+)\]\s*(.*)$")
CONTEXT_SECONDS = 30
BUCKET_HEADS = [("Yours", "yours", "bar-mine"), ("Theirs", "theirs", "bar-theirs"),
                ("Open questions", "questions", "bar-ask"), ("Owner unclear", "unowned", "")]


def load_lang(path):
    spec = importlib.util.spec_from_file_location("lang", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def shell(template_name, title):
    """The template's head with every comment stripped and the title filled.

    Strips ALL comments, not the first: the authoring note used to nest a comment,
    and a single non-greedy strip ends at that inner marker and spills the rest
    onto the page as visible text.
    """
    tpl = (HERE / template_name).read_text()
    head = tpl[:tpl.index("<body>")]
    head = re.sub(r"<!--.*?-->", "", head, flags=re.S).lstrip("\n")
    return head.replace("{{TITLE}}", e(title)), tpl


def hms(sec):
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def read_lines(path):
    out = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        m = LINE_RE.match(raw)
        if m and m.group(4) != "MARKER":
            h, mi, se, label, text = m.groups()
            t = int(h) * 3600 + int(mi) * 60 + int(se)
            out.append({"t": t, "label": label, "text": text, "tstr": hms(t)})
    return out


def merge_inferred(digest, L, transcript):
    """Fold the model's action-item sweep (L.INFERRED) into the digest.

    Each entry is (t, owner, one_liner) or (t, owner, one_liner, badge). Items keep
    their own source and badge, so a flagged press and a guess never look alike.
    Any sweep already in the digest is dropped first and rebuilt from L, so a
    re-render neither doubles the list nor keeps a one-liner Sam has since edited.
    """
    entries = getattr(L, "INFERRED", []) or []
    had = digest.get("inferred_total", 0)
    for sec in digest["sections"]:
        sec["items"] = [i for i in sec["items"] if i.get("source") != "inferred"]
    for key, items in digest["rollup"].items():
        digest["rollup"][key] = [i for i in items if i.get("source") != "inferred"]
    digest["inferred_total"] = 0
    if not entries:
        return had > 0
    lines = read_lines(transcript) if transcript else []
    taken = {i["t"] for s in digest["sections"] for i in s["items"]}
    added = 0
    for entry in entries:
        t, owner, text = entry[:3]
        badge = entry[3] if len(entry) > 3 else "inferred"
        if owner not in OWNER:
            raise SystemExit(f"INFERRED at {t}: owner {owner!r} is not one of {sorted(OWNER)}")
        while t in taken:   # ONE is keyed by t, so a guess may not share a press's second
            t += 1
        taken.add(t)
        item = {"type": OWNER[owner], "t": t, "tstr": hms(t), "source": "inferred",
                "badge": badge, "text": None, "paired": False, "one_liner": text,
                "context": [c for c in lines if abs(c["t"] - t) <= CONTEXT_SECONDS],
                "screenshot": None}
        L.ONE[t] = text
        sec = next((s for s in reversed(digest["sections"]) if s["start"] <= t),
                   digest["sections"][0])
        sec["items"].append(item)
        digest["rollup"].setdefault(BUCKET_OF[item["type"]], []).append(item)
        added += 1
    for sec in digest["sections"]:
        sec["items"].sort(key=lambda i: i["t"])
    for items in digest["rollup"].values():
        items.sort(key=lambda i: i["t"])
    digest["inferred_total"] = added
    return True


def badge(i):
    if i.get("source") == "inferred":
        return f'<span class="spoken">{e(i.get("badge", "inferred"))}</span>'
    if i.get("source") == "aside" or i.get("paired"):
        return '<span class="spoken">said out loud</span>'
    return ""


def ts_button(item, audio):
    if not audio:
        return f'<button class="ts" disabled>{item["tstr"]}</button>'
    return f'<button class="ts" onclick="seek({item["t"]})">{item["tstr"]}</button>'


def resection(digest, subjects):
    """Re-cut the call at the model's subject boundaries (L.SUBJECTS).

    Single-marker calls have no `topic` presses, so the digest arrives as one
    implicit section. SUBJECTS is [(start_t, name, lede), ...]; the first entry is
    snapped back to the call start so nothing before it falls off the page.
    Returns the TOPICS mapping the section headings are read from.
    """
    subjects = sorted(subjects, key=lambda s: s[0])
    old = digest["sections"]
    items = sorted((i for s in old for i in s["items"]), key=lambda i: i["t"])
    call_start, last_end = old[0]["start"], old[-1]["endstr"]
    starts = [call_start] + [s[0] for s in subjects[1:]]
    sections, topics = [], {}
    for n, (start, subj) in enumerate(zip(starts, subjects)):
        nxt = starts[n + 1] if n + 1 < len(starts) else None
        topics[start] = (subj[1], subj[2] if len(subj) > 2 else "")
        sections.append({"start": start, "end": nxt, "implicit": False,
                         "topic_marker": None, "tstr": hms(start),
                         "endstr": hms(nxt) if nxt is not None else last_end,
                         "items": [i for i in items
                                   if i["t"] >= start and (nxt is None or i["t"] < nxt)]})
    if items and items[0]["t"] < call_start:   # cannot happen, but never drop an item
        sections[0]["items"] = [i for i in items if i["t"] < call_start] + sections[0]["items"]
    digest["sections"] = sections
    digest["subject_split"] = True
    return topics


def screen_html(i, out_dir):
    """The screen grab for a marker or video item, or the no-frame note.

    A marker's `frame` is the full-resolution PNG ripa saved; it stays a file on
    disk (too large to inline) and is linked relatively so the tree can move. The
    embedded 960 px `screenshot` is the preview shown on the page. With only one of
    the two, that one is shown.
    """
    frame, shot, alt = i.get("frame"), i.get("screenshot"), f'screen at {i["tstr"]}'
    href = None
    if frame:
        try:
            href = quote(os.path.relpath(frame, out_dir))
        except ValueError:
            href = "file://" + quote(frame)
    if not frame and not shot:
        return '\n        <p class="noshot">no frame available</p>'
    img = f'<img class="shot" src="{shot or href}" alt="{alt}">'
    if href:
        return (f'\n        <a href="{href}" target="_blank">{img}</a>'
                f'\n        <p class="noshot"><a href="{href}" target="_blank">'
                f'full-resolution frame</a></p>')
    return "\n        " + img


def rebuild_rollup(digest):
    buckets = {}
    for sec in digest["sections"]:
        for i in sec["items"]:
            buckets.setdefault(i["type"], []).append(i)
    digest["rollup"] = {"yours": buckets.get("action-me", []),
                        "theirs": buckets.get("action-them", []),
                        "questions": buckets.get("question", []),
                        "unowned": buckets.get("action", [])}


def render_call(digest, L, out, transcript=None, digest_path=None):
    # RETYPE fixes one press Sam says was the wrong key ({187: "action-me"});
    # REMAP below re-labels a whole type and only for display.
    retype = getattr(L, "RETYPE", {}) or {}
    changed = False
    for sec in digest["sections"]:
        for i in sec["items"]:
            if i["t"] in retype and i["type"] != retype[i["t"]]:
                i["type"], changed = retype[i["t"]], True
    if changed:
        rebuild_rollup(digest)
    # SUBJECTS re-cuts the call by what was discussed. Done before the sweep merge
    # so inferred items land in the subject they were said in.
    subjects = getattr(L, "SUBJECTS", None)
    if subjects:
        L.TOPICS = resection(digest, subjects)
        changed = True
    # All three are written back into the digest so day and index pages, which
    # aggregate from digests, count them the same way the call page does.
    if (merge_inferred(digest, L, transcript) or changed) and digest_path:
        Path(digest_path).write_text(json.dumps(digest, indent=1), encoding="utf-8")
    remap = getattr(L, "REMAP", {})
    for sec in digest["sections"]:
        for i in sec["items"]:
            i["type"] = remap.get(i["type"], i["type"])
    if remap:
        rebuild_rollup(digest)

    audio = digest.get("audio_path")
    one = L.ONE
    out_dir = str(Path(out).resolve().parent)

    def line(i, cls):
        return (f'<li class="{cls}">{ts_button(i, audio)} '
                f'{e(one.get(i["t"], "No summary."))} {badge(i)}</li>')

    parts, r = [], digest["rollup"]
    for head, key, cls in BUCKET_HEADS:
        got = r.get(key, [])
        # An empty bucket is skipped, except Yours, which keeps its heading so the
        # absence reads as "checked, none" rather than "not checked".
        if got or key == "yours":
            parts.append(f"    <h2>{head}</h2>")
            parts.append("    <ul>\n" + "\n".join("      " + line(i, cls) for i in got)
                         + "\n    </ul>"
                         if got else '    <p class="none">Nothing landed on you.</p>')
        # Decisions sit after the Theirs position whether or not Theirs had any
        # items. This used to live inside the block above, so a call with nothing
        # owed by the other side silently dropped its Decisions entirely.
        if key == "theirs" and getattr(L, "DECISIONS", None):
            parts.append('    <div class="decisions">\n      <h2>Decisions</h2>\n      <ul>\n'
                         + "\n".join(f"        <li><strong>{e(a)}</strong> {e(b)}</li>"
                                     for a, b in L.DECISIONS)
                         + "\n      </ul>\n    </div>")
        # Marker presses come right after Decisions: on the single-marker rig they
        # are the main thing Sam flagged, and the one-liner says what each was.
        if key == "theirs":
            marked = [i for s in digest["sections"] for i in s["items"]
                      if i["type"] == "marker"]
            if marked:
                parts.append("    <h2>Marked moments</h2>")
                parts.append("    <ul>\n" + "\n".join("      " + line(i, "mark")
                                                    for i in marked) + "\n    </ul>")
    rollup_html = "\n".join(parts)

    secs, nav = [], [("rollup", "Summary")]
    for n, s in enumerate(digest["sections"], 1):
        sid, body = f"s{n}", []
        if not s["implicit"]:
            name, lede = L.TOPICS.get(s["start"], (f"Topic at {s['tstr']}", ""))
            nav.append((sid, name))
            body.append(f'    <h2>{e(name)} <span class="span">{s["tstr"]} &ndash; '
                        f'{s["endstr"]}</span></h2>')
            if lede:
                body.append(f'    <p class="lede">{e(lede)}</p>')
        else:
            nav.append((sid, "The call" if len(digest["sections"]) == 1
                        else "Before the first topic"))
        for i in s["items"]:
            label, cls = KIND.get(i["type"], (i["type"], ""))
            spoken = badge(i)
            quotes = "".join(f'<div><span class="who">[{e(c["label"])}]</span> '
                             f'{e(c["text"])}</div>' for c in i["context"])
            screen = i["type"] in SCREEN_TYPES
            shot = screen_html(i, out_dir) if screen else ""
            bits = ([f'{len(i["context"])} line{"s" if len(i["context"]) != 1 else ""}']
                    if i["context"] else [])
            if screen:
                bits.append("screen")
            ev = ("" if not bits else
                  f'\n      <details class="ev">\n        <summary>{" + ".join(bits)}</summary>\n'
                  f'        <div class="quotes">{quotes}</div>{shot}\n      </details>')
            body.append(f'    <div class="item">\n      <div class="head">'
                        f'<span class="kind {cls}">{label}</span>{spoken}'
                        f'{ts_button(i, audio)}</div>\n'
                        f'      <p class="oneliner">'
                        f'{e(one.get(i["t"], "No summary for this moment."))}</p>{ev}\n'
                        f'    </div>')
        secs.append(f'  <div class="section" id="{sid}">\n' + "\n".join(body) + "\n  </div>")

    head, tpl = shell("template.html", L.TITLE)
    nav_html = ('<nav class="toc">\n  <h2>' + e(L.TITLE) + "</h2>\n"
                + "\n".join(f'  <a href="#{i}">{e(t)}</a>' for i, t in nav) + "\n</nav>")
    legacy = getattr(L, "LEGACY_NOTE", "")
    player = (f'  <audio id="player" controls preload="none" src="file://{audio}"></audio>\n\n'
              if audio else "")
    page = (head + "<body>\n\n" + nav_html + "\n\n<main>\n"
            f'  <h1>{e(L.TITLE)}</h1>\n  <p class="date">{e(L.DATE)}</p>\n'
            f'  <p class="summary">{e(L.SUMMARY)}</p>\n'
            + (f'  <p class="lede">{e(legacy)}</p>\n' if legacy else "") + "\n"
            + player
            + f'  <section class="rollup" id="rollup">\n{rollup_html}\n  </section>\n\n'
            + "\n\n".join(secs) + "\n</main>\n\n" + tpl.split("</main>")[1])
    Path(out).write_text(page, encoding="utf-8")
    return page


def render_day(day, digests, L, out):
    head, tpl = shell("day-template.html", L.DAY_LABEL)
    parts = []
    # All four buckets, not the first three. Dropping "unowned" here is how a
    # legacy day page ends up reading "Nothing landed on you" with a dozen of
    # Sam's own action items sitting in the digest.
    for headline, key, cls in BUCKET_HEADS:
        got = L.MERGED.get(key, [])
        if not got and key != "yours":
            continue
        parts.append(f"    <h2>{headline}</h2>")
        if got:
            rows = []
            for i in got:
                href = f'{i["call"]}/{i["call"]}-call.html'
                rows.append(f'      <li class="{cls}"><a class="ts" href="{e(href)}">{i["tstr"]}</a> '
                            f'{e(i.get("one_liner", "No summary."))} {badge(i)} '
                            f'<span class="from">{e(i["call"])}</span></li>')
            parts.append("    <ul>\n" + "\n".join(rows) + "\n    </ul>")
        else:
            parts.append('    <p class="none">Nothing landed on you.</p>')

    # Card tallies are read per call. They must be remapped the same way the
    # roll-up above them was, or a legacy day prints "0 yours" on every card
    # while the roll-up lists sixteen.
    remap = getattr(L, "REMAP", {}) or {}
    bucket_for = {"action-me": "yours", "action-them": "theirs",
                  "question": "questions", "action": "unowned"}

    def tally_of(d):
        counts = {"yours": 0, "theirs": 0, "questions": 0}
        for bucket, items in d.get("rollup", {}).items():
            for it in items:
                t = remap.get(it.get("type"), it.get("type"))
                dest = bucket_for.get(t, bucket)
                if dest in counts:
                    counts[dest] += 1
        return counts

    cards = []
    for d in digests:
        meta = L.CALLS.get(d["name"], {})
        c = tally_of(d)
        tally = "".join(f'<li class="{cl}">{c[k]} {lbl}</li>'
                        for k, cl, lbl in [("yours", "mine", "yours"),
                                           ("theirs", "theirs", "theirs"),
                                           ("questions", "ask", "questions")] if c[k])
        cards.append(
            f'  <a class="card" href="{e(d["name"])}/{e(d["name"])}-call.html">\n'
            f'    <div class="top"><span class="name">{e(meta.get("title", d["name"]))}</span>'
            f'<span class="when">{d["total"]} marked</span></div>\n'
            f'    <p class="blurb">{e(meta.get("summary", ""))}</p>\n'
            + (f'    <ul class="tally">{tally}</ul>\n' if tally else "") + "  </a>")

    page = (head + "<body>\n<main>\n"
            f'  <p class="date"><a href="../index.html">&larr; All days</a></p>\n'
            f'  <h1>{e(L.DAY_LABEL)}</h1>\n'
            f'  <p class="summary">{e(L.DAY_SUMMARY)}</p>\n\n'
            f'  <section class="rollup" id="rollup">\n' + "\n".join(parts) + "\n  </section>\n\n"
            f'  <h2>Calls</h2>\n' + "\n".join(cards) + "\n</main>\n</body>\n</html>\n")
    Path(out).write_text(page, encoding="utf-8")
    return page


def render_index(days, L, out):
    head, _ = shell("index-template.html", "Call Reports")
    cards = []
    for d in days:
        meta = L.DAYS.get(d["day"], {})
        tally = "".join(f'<li class="{cl}">{d["counts"][k]} {lbl}</li>'
                        for k, cl, lbl in [("yours", "mine", "yours"),
                                           ("theirs", "theirs", "theirs"),
                                           ("questions", "ask", "questions")]
                        if d["counts"].get(k))
        cards.append(
            f'  <a class="card" href="{e(d["day"])}/{e(d["day"])}-day.html">\n'
            f'    <div class="top"><span class="name">{e(d["label"])}</span>'
            f'<span class="when">{d["calls"]} call{"s" if d["calls"] != 1 else ""}</span></div>\n'
            f'    <p class="blurb">{e(meta.get("blurb", ""))}</p>\n'
            + (f'    <ul class="tally">{tally}</ul>\n' if tally else "") + "  </a>")
    page = (head + "<body>\n<main>\n"
            f'  <h1>Call Reports</h1>\n  <p class="date">{e(L.RANGE)}</p>\n'
            f'  <p class="summary">{e(L.OVERALL)}</p>\n\n'
            + "\n".join(cards) + "\n</main>\n</body>\n</html>\n")
    Path(out).write_text(page, encoding="utf-8")
    return page


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("call"); c.add_argument("--digest", required=True)
    c.add_argument("--lang", required=True); c.add_argument("--out", required=True)
    c.add_argument("--transcript", help="the call .txt, for INFERRED items' context")
    d = sub.add_parser("day"); d.add_argument("--digests", nargs="+", required=True)
    d.add_argument("--day", required=True)
    d.add_argument("--lang", required=True); d.add_argument("--out", required=True)
    x = sub.add_parser("index"); x.add_argument("--days", required=True)
    x.add_argument("--lang", required=True); x.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    if a.mode == "call":
        render_call(json.load(open(a.digest)), load_lang(a.lang), a.out,
                    transcript=a.transcript, digest_path=a.digest)
    elif a.mode == "day":
        render_day(a.day, [json.load(open(p)) for p in a.digests], load_lang(a.lang), a.out)
    else:
        render_index(json.load(open(a.days)), load_lang(a.lang), a.out)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
