---
name: process-transcript
description: Use when Sam hands over one or more casting-call transcript paths (a .txt file or a directory) to turn a recorded call into one browsable HTML report — a summary, a roll-up of what he owes and what the far side owes, then the call cut into subject sections with every marker press (the single P4next pad, or the older seven Stream Deck keys) and every phrase he spoke into his muted mic ("action item", "notes notes notes") filed into the section it happened in, each with click-to-audio seek and the screen grab taken at each press. Also runs on /process-transcript. Replaces the old marker-digest skill.
---

## Process Transcript

One call in, one page out. The page has to answer "what do I have to do" before it
answers anything else, because that is why Sam opens it.

Two channels feed it and they are treated as one thing:

- **Button presses.** Clips that land on the marker track (Track 4 of the four-track
  rig, Track 3 before 2026-10-08) and become `[MARKER] <type>` lines. The most
  deliberate signal in the call, since he physically pressed a key.
  - **Single-marker rig (2026-11-14 on):** one pad (pad A on a Zoom P4next), one
    type, `marker`. The press says only "this moment matters", so what it was
    about has to come from the talk around it. These are the primary items on a
    new call, and each one has a full-resolution screen grab saved by `ripa`.
  - **Legacy rig:** seven Stream Deck keys (`topic`, `action-me`, `action-them`,
    `important`, `question`, `quote`, `video`) where the key itself said the kind.
    Old recordings still carry these and render as before.
- **Spoken asides.** Phrases he says into his muted mic ("action item", "notes notes
  notes") that land on his own `[You]` track.

Both are authoritative. When he did both for the same task, they arrive already
merged into one item, so do not re-split them.

A third source fills the gaps: **your own sweep of the transcript** for action
items nobody flagged. Sam would rather prune a long list than miss a task, so the
sweep leans hard toward inclusion. Its items render with an `inferred` badge so a
guess never looks like a press.

## When to Use

Manual invocation or `/process-transcript`. Sam hands over **paths**: a single
merged transcript `.txt`, several, or a directory to scan.

**Resolving a handed-off path.** Do not glob for this yourself. A day directory
holds far more `.txt` files than it holds calls, and the traps (the `parts/`
per-track transcripts, the same call sitting in two layouts, `-clean` and
`-stitched` variants of one conversation) are exactly what the discovery module
already handles:

```bash
cd ~/Developer/casting-call && python3 -c "
from casting_call.discover import find_transcripts, group_by_day
from pathlib import Path
t = Path('<target>')
for day, paths in group_by_day(find_transcripts(t), t):
    for p in paths: print(day, p)
"
```

`target` can be any of three things, and the output shape tells you which:

| Target | Produces |
|---|---|
| a single `.txt` | one `<name>-call.html` |
| one day directory | its call pages + `<day>/<day>-day.html` |
| a parent of day directories | every call page, every day page, and `index.html` at the target |

A transcript is **unprocessed** if no `<name>-call.html` sits next to it. Skip
processed ones unless Sam says to reprocess or force. Day and index pages are
always rewritten, since a new call changes them.

## Process

For each unprocessed transcript:

### 1. Build the structured data (deterministic, do not reimplement)

```bash
cd ~/Developer/casting-call && python3 -m casting_call.digest <absolute-transcript-path>
```

Everything mechanical already happened in there: parsing, the marker/aside split,
the phrase matching, topic-press sectioning, press-and-aside pairing, context
windows, ffmpeg screenshots, and matching each `marker` press to its frame in
`markers.json`. **Do not re-derive any of it by reading the transcript
yourself.** Read the transcript only for the language work in step 2.

Add `--window <seconds>` to widen the context, `--no-frames` to skip screenshots.

The JSON gives you:

| Key | What it is |
|---|---|
| `name`, `audio_path`, `video_path` | the call and its sibling media (paths may be null) |
| `sections` | the call cut at each `topic` press, in time order (one implicit section when there are none, which is every single-marker call) |
| `rollup` | `yours` / `theirs` / `questions` / `unowned`, across all sections |
| `groups`, `counts`, `total` | the old by-type view, still there if you want a count row |
| `aside_total`, `aside_paired` | how many spoken flags there were, and how many folded into a press |
| `markers_manifest` | path to `markers.json` next to the transcript, or null on a legacy call |

Each **section** has `start`/`end` (seconds), `tstr`/`endstr`, `implicit` (true for
the run-up before the first topic press, or for a whole call with no topic presses
at all), `topic_marker`, and `items`.

Each **item** has `type`, `t`, `tstr`, `source` (`marker` or `aside`), `text` (the
spoken words, or null for an unpaired press), `paired`, `context` (the ±30s of
speech around it), `screenshot` (a 960 px JPEG data URI, on `video` and `marker`
items), and `frame` (the full-resolution PNG path from `markers.json`, or null).

A `marker` item's `context` is wider, ±90s (`MARKER_WINDOW` in `digest.py`),
because a single button carries no meaning of its own and 30s cannot tell a topic
from a passing remark.

**If `total` and `aside_total` are both 0, print a one-line notice and write
nothing.** Transcripts from before the rig have neither. That is expected, not an
error.

### 2. Write the language (this is your only job)

- **Call summary.** 2-4 sentences: who was on it, what it was about, what came out
  of it. Readable cold. Follow `sam-style`.
- **Subject split.** Cut the call into sections by what was discussed, not by
  button presses. You do this inline from the full transcript: the split is
  language work like everything else in this step, so a separate model call buys
  nothing but another handoff. (In batch mode, the per-call subagents do it, so
  dispatch them with `model: sonnet` or better, never haiku.)
  - **Single-marker calls, and any call with no `topic` presses:** write
    `SUBJECTS`, one entry per subject in time order, each starting at the second
    the conversation turned to it. Aim for a section per real change of subject,
    typically 3 to 8 on an hour call; a short call on one subject gets one entry.
    The renderer re-cuts the items into these sections and the first one is
    snapped back to the call start.
  - **Legacy calls with `topic` presses:** keep Sam's presses as the cut. Name
    each non-implicit section in `TOPICS`. Write `SUBJECTS` instead only if the
    presses are clearly wrong, and say so in `LEGACY_NOTE`.
  - Names are short noun phrases drawn from what is said ("Pricing", "Migration
    timeline"). Never "Topic 2". Add a one-line lede only when the name is thin.
- **Marker summaries.** For each `marker` item, 1 to 2 sentences in `ONE` on what
  was being discussed around the press, from its `context`. If anyone asked for
  something or committed to something in that window, name it in the summary
  ("Corey asked for the eval numbers by Friday") and also add it to `INFERRED`
  with badge `"at marker"`, so it reaches the roll-up with an owner. A marker with
  no ask or commitment is still summarized; it was pressed for a reason, and the
  likeliest reason is what is on screen, so say what the frame shows when the
  talk is thin.
- **Item one-liners.** One plain sentence per item, from its `context`.
  - When `text` is present, that is Sam saying it himself. Lead with his words,
    lightly cleaned, and strip the trigger phrase: "action item, send the SOW
    friday" becomes "Send the SOW by Friday." Never quote the trigger back at him.
  - When `text` is null, write the moment from its `context` window.
  - If the context is thin, say so. Do not invent detail.
- **Decisions.** Read the full transcript for agreements and direction changes
  nobody flagged. Bold one-liner plus a sentence of context. This is the one place
  you infer, and it is a backstop, never a re-listing of items above.

- **Action-item sweep.** Read the whole transcript, including caption-patched
  `[Caller]` lines, and list every task either side took on or handed over that
  the flagged items do not already cover. **Over-include.** Sam prunes; a missed
  task costs more than an extra line. Count as a task:
  - any commitment: "I'll", "let me", "I'm gonna", "I can", "I owe you"
  - any ask: "can you", "could you", "drop a note", "send me", "loop in", "ping"
  - anything to send, share, post, review, fix, approve, schedule, or follow up on
  - a suggestion the other side clearly expects acted on ("I'd tell you to drop
    a note in the labs channel")
  - something done live on the call, marked `(done on the call)` so Sam can drop it
  - an open question left hanging, as `question`

  One entry per distinct task, at the second it was asked or promised. Owner is
  `me` (Sam), `them`, `unclear`, or `question`. Write the one-liner as the task,
  with who and where when the transcript says ("Post in #devrel tagging Greg and
  Corey: ..."). Skip only an exact duplicate of a flagged item: the same task, not
  just the same topic. A flagged press is never dropped in favor of a guess.
- **Decisions** stay separate: direction and agreements, never a re-listing of
  tasks above.

### 3. Render

**Do not hand-write the HTML.** `render.py` (next to this skill) fills the
templates. You write a small Python file of strings and it does the layout, so a
fan-out over many days produces one consistent set of pages instead of drifting
per author.

```bash
SK=~/.claude/skills/process-transcript
python3 $SK/render.py call --digest d.json --lang lang.py \
    --transcript <name>.txt --out <name>-call.html
```

`--transcript` gives each sweep item its context lines. The renderer writes the
sweep back into `d.json`, so day pages built from that digest count it too.

A call's `--lang` file holds exactly these names:

```python
TITLE   = "Corey Sync"          # short, human. Not the filename.
DATE    = "August 25, 2026"
SUMMARY = "2-4 sentences."
TOPICS  = {304: ("Pricing", "optional one-line lede")}   # keyed by section start
SUBJECTS = [(0, "Intros", ""), (412, "Eval results", "optional lede")]
            # (start_t, name[, lede]); replaces the topic-press cut and TOPICS.
            # Required on single-marker calls longer than one subject.
ONE     = {304: "One sentence per item, keyed by the item's t."}
DECISIONS = [("Bold one-liner.", "A sentence of context.")]
INFERRED = [(1973, "me", "Ask Labs about hosting the podcast on their page."),
            (558, "them", "Fix the Ramp approval today."),
            (2109, "me", "Thread in #devrel tagging Greg.", "added by Sam")]
            # (t, owner, one-liner[, badge]); owner: me | them | unclear | question.
            # Badge defaults to "inferred". Use "added by Sam" for tasks he names
            # after the fact, "at marker" for an ask or commitment found in a
            # marker's context window. No ONE entry needed; the one-liner is the third field.
LEGACY_NOTE = ""                # optional line under the summary
REMAP   = {}                    # optional, e.g. {"action": "action-me"}
RETYPE  = {}                    # optional, one press Sam corrects: {187: "action-me"}
```

Every `t` in `ONE` must match an item's `t` exactly, or that item renders "No
summary for this moment." Check the rendered page for that string before you call
a call done.

The rules the renderer already enforces, for reference when reviewing output:

The page is meant to read like a one-pager. A reader should get the whole call
from the roll-up and the one-liners without opening anything; everything else
folds away. Judge every layout choice against that.

- **Roll-up first**, before any section. Use the item's one-liner as its text.
  Omit `Theirs`, `Open questions`, and `Owner unclear` when empty. Keep `Yours`
  always, showing its "Nothing landed on you" line when it is empty, because an
  absent heading reads as "not checked" and an explicit one reads as "checked, none".
- **Marked moments** follow Decisions in the roll-up card: every `marker` item
  with its summary and seek button, so the presses read as the call's outline.
- **Decisions go inside the roll-up card, directly after `Theirs`**, not at the
  bottom of the page. They are context for what you owe, so they belong next to it.
  Each decision renders as a two-column grid row (fixed 13rem label, then context)
  so the labels and the context lines align down the card. Keep the bold
  one-liner short enough to sit in that column in two or three lines.
- **Evidence folds.** Each item's context quotes and its screenshot go inside a
  closed `<details class="ev">`. The `<summary>` says what is in it: `6 lines`, or
  `6 lines + screen` for a video item. Omit the `<details>` when an item has no
  context and no screenshot. Never render an item's quotes open.
- **Side nav.** One `<a>` per anchor, in page order: `#rollup` as "Summary", then
  one per section using its topic or subject name (`Before the first topic` for
  an implicit one). Every section needs a matching `id`. The scrollspy in the template reads
  the nav's own hrefs, so the two cannot drift apart.
- **Sections in order.** For `implicit: true`, drop the `<h2>` and render its items
  bare. A call with no topic presses is one implicit section and looks like an
  ordinary page, which is what every pre-topic-key transcript will do.
- **Kind labels** on each item: `action-me` → `Yours` (class `mine`), `action-them`
  → `Theirs` (class `theirs`), `question` → `Question` (class `ask`), `action` →
  `Owner unclear`, `marker` → `Marked` (class `mark`), and `important` / `quote` /
  `video` / `note` under their own names with no color class.
- **"said out loud"** badge when `source` is `aside` or `paired` is true.
- **`inferred`** badge (or the entry's own badge) on sweep items, in the roll-up
  and in the section, so Sam can see at a glance which lines to prune.
- **Screen grab** only on `video` and `marker` items, inside the fold. A marker's
  `frame` (full resolution) is linked by a relative path, with the embedded
  `screenshot` as the preview image; when `frame` is null the `screenshot` alone
  shows, and with neither, the "no frame available" note. Frames are not inlined:
  a 4K PNG per press would make the page tens of megabytes.
- Omit the `<audio>` element and disable the seek buttons when `audio_path` is null.

Everything stays inline. No external scripts, styles, or fonts.

**Strip every comment from the template before writing the page**, not just the
first one. The template's own authoring note is a comment, and a single
non-greedy strip can end at a nested close marker and spill the rest of the note
onto the top of the rendered page as visible text.

### 4. Day and index pages

Only when the target was a directory. The aggregation is deterministic; use it
rather than tallying by hand:

```bash
cd ~/Developer/casting-call && python3 -c "
from casting_call.rollupday import day_summary, merge_rollups, report_name
"
```

`day_summary(day, digests)` takes the day's name and its per-call digest dicts and
returns `label` (a readable date), `calls`, `markers`, `counts` per bucket, a
merged `rollup` where every item carries the `call` it came from, and `calls_list`.

```bash
python3 $SK/render.py day --day 26-08-25 --digests a.json b.json \
    --lang daylang.py --out 26-08-25-day.html
python3 $SK/render.py index --days days.json --lang indexlang.py --out index.html
```

A day's `--lang` file:

```python
DAY_LABEL   = "August 25, 2026"
DAY_SUMMARY = "1-2 sentences on the shape of the day."
MERGED      = {...}   # rollupday.merge_rollups output, each item given a
                      # "one_liner" key copied from that call's ONE
CALLS       = {"2026-08-25-corey-sync": {"title": "Corey Sync",
                                         "summary": "one line for the card"}}
```

An index `--lang` file: `RANGE` (e.g. "July 20 to August 28, 2026"), `OVERALL`
(2-3 sentences), and `DAYS = {"26-08-25": {"blurb": "one line"}}`. Its `--days`
is a JSON array of `rollupday.day_summary` results, newest first.

- **Day page** → `<day>/<day>-day.html` from `day-template.html`. The merged
  roll-up goes first, each item linking into its call page's anchor
  (`<name>-call.html`), then one card per call carrying that call's summary.
  A day page is worth writing even for a single call, so the index always has
  something to point at.
- **Index page** → `index.html` in the target directory, from
  `index-template.html`. One card per day, **newest first**, linking to the day
  page. This is the page Sam opens in the morning, so it leads with what is
  outstanding, not with how many calls happened.
- Links between pages are **relative**, so the whole tree can be moved or copied
  without breaking. A day page links to a call as `<name>/<name>-call.html` and
  back to the index as `../index.html`.

### 5. Write and open

Write `<same-dir>/<name>-call.html` per call. Open in **Chrome**, skipping
anything already in a tab (helper below). When the run produced day and index
pages, **open only the index** and print the paths of the rest: it is the entry
point and everything else is one click away.

## Batch mode (subagents)

Sam runs this at the end of a work day so a report is waiting the next morning, so
a normal run is a day directory with two to four calls. Do those inline. Only a
backlog of a whole month needs the fan-out below.

For 1-2 transcripts, inline. For 3+ (a backlog, an overnight run):

- Dispatch one subagent per transcript, all in the same message so they run in
  parallel. Resolve the unprocessed list first; subagents are independent because
  each writes a different file.
- Each subagent does steps 1-3 and writes only its own `<name>-call.html`. It does
  **not** open Chrome, which is shared state and would race, and it does **not**
  write day or index pages, which need every call to be finished first.
- You do steps 4 and 5 once, after they all finish, and report a roll-up:
  N processed, total flagged items, any failures.

## Open in Chrome

Open the index when there is one. Otherwise auto-open only when the run produced
**5 or fewer** pages; for a larger batch print the paths and let Sam read them in
the morning.

```bash
open_report() {  # $1 = absolute path to the -call.html
  local url="file://$1" hit
  hit=$(osascript - "$url" <<'OSA'
on run argv
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        if (URL of t) is (item 1 of argv) then return "true"
      end repeat
    end repeat
  end tell
  return "false"
end run
OSA
)
  if [ "$hit" = "true" ]; then
    echo "  already open, skipping: $(basename "$1")"
  else
    open -a "Google Chrome" "$1"
  fi
}
```

## Error handling

- **No `audio_path`:** no `<audio>`, timestamps render as disabled buttons.
- **No video, or a null screenshot on a `video` item:** the "no frame available"
  note. Never block the page on a missing frame.
- **No `markers.json`, or a `marker` item with a null `frame`:** fall back to
  `screenshot`. `ripo` deletes the `.mkv` overnight, so a call reprocessed after
  that has frames but no screenshots, and one processed before `ripa` saved
  frames has neither. Both still render.
- **Legacy marker types** (`flag`, `follow` from before the button rename) arrive in
  `groups` after the canonical ones. They still belong to a section and still
  render; label them by their raw type. Nothing about an old call silently vanishes.
- **Calls recorded before 2026-08-28** had one generic Action key that meant Sam's
  own to-do, so their bare `action` items are his. Render those as **Yours**, not
  as "Owner unclear", and say so in a line under the summary. The alternative
  leaves a page reading "Nothing landed on you" on a call where he flagged nine of
  his own tasks, which is the one thing this page must never do.
- **Collapsed markers on an old transcript.** If a legacy call (anything but
  `marker` presses) shows exactly one marker, or a handful all of the same type,
  its marker track was transcribed whole by the old
  pipeline and every timestamp after the first is wrong. Check for
  `parts/markers.mp3`; if it is there, strip the `[MARKER]` lines and re-derive
  with `casting_call.marker_track` before building the digest.
- **Zero markers on a four-track call.** If `total` is 0 and `parts/markers.mp3`
  peaks at digital silence (`ffmpeg -i parts/markers.mp3 -af volumedetect -f null -`
  shows `max_volume` around -91 dB), the clips were routed to the wrong track in
  OBS, not left unpressed. Say so in one line and point Sam at rerunning `ripa`
  with `MARKER_TRACK=<n>`. Still build the page from the asides.
- **Few or no presses on a long call.** Before blaming Sam, check the OBS log
  for that morning (`~/Library/Application Support/obs-studio/logs/`). A line
  `Skipping module 'obs-websocket', not on safe list` means OBS was in Safe Mode
  after a crash and the Stream Deck could not reach it. Say so in one line under
  the summary. The sweep is the whole action list on that call, so be thorough.
- **Caption-patched far side.** If `parts/pre-caption-splice.txt` exists, the
  system-audio capture dropped and `ripa` spliced Meet captions in as `[Caller]`
  lines; `parts/captions.txt` holds them with the names Meet showed. Use those
  names when writing one-liners, but treat them as approximate where people
  overlapped, and mention the patched stretch in one line under the summary.
- **Digest module errors** (bad path, unreadable file): surface it, skip that
  transcript, keep going.

## History

Supersedes `marker-digest`, which rendered the button channel to its own
`<name>-digest.html` while this skill rendered the spoken channel to a separate
`<name>-summary.md`. Two files per call from two passes over the same transcript,
with no way to see a task that came through both. Retired 2026-08-28. Old
`-summary.md` and `-digest.html` files on disk are left alone; nothing rewrites them.
