# casting-call: Meet Speaker Segmentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `casting-call` repo as the home of the full call-recording pipeline, and add speaker attribution that relabels the Caller audio channel using Meet's on-screen caption speaker-labels.

**Architecture:** casting-call owns the migrated media scripts (`bin/`) plus a new Python package (`casting_call/`) with isolated stages — sample (ffmpeg frames), locate (caption band), read (OCR + parse + roster match), timeline (debounce + grace-bridge), review (unknowns + bail-out), transcript (relabel + coverage). The migrated `extract_audio_stereo.sh` calls the package to relabel `[Caller]` transcript lines from `speakers.json` when present. Pure logic is unit-tested; I/O glue is validated against a real recording.

**Tech Stack:** Python 3 (stdlib `difflib`, `dataclasses`), Pillow + numpy (image crop/band detection), pytesseract + tesseract binary (OCR), ffmpeg + whisper-cpp (existing), pytest. Offline, no API cost.

**Repo:** `~/Developer/casting-call` (branch `main`, already initialized; owns spec + plan under `docs/superpowers/`).

**Dependencies to install (flag before running):**
- `brew install tesseract` (ffmpeg + whisper-cpp already installed for the existing pipeline)
- `pip install --break-system-packages pillow numpy pytesseract pytest`
- `opencv` is intentionally NOT a v1 dependency — it belongs to the deferred BorderProvider.

**Spec:** `docs/superpowers/specs/2026-06-04-meet-speaker-segmentation-design.md`

**Sample recording:** `/path/to/recording.mov` (2-person: Sam + Dana Park). Plumbing validation only; group-call tuning is a follow-up per the spec.

---

## File Structure

```
casting-call/
  README.md
  bin/
    extract_audio_stereo.sh      # migrated (ripa); gains native speaker relabel
    transcribe_batch_stereo.sh   # migrated (ript)
    convert_video.sh             # migrated (ripv)
    convert_recordings.sh        # migrated (riprec)
    extract_audio.sh             # migrated (called by convert_recordings)
    transcribe_batch.sh          # migrated
    extract_speakers.sh          # NEW; alias `rips`
  casting_call/
    __init__.py
    config.py        # Config dataclass + defaults
    roster.py        # load_roster, match_name (difflib)
    read.py          # read_active_speaker (parse) + ocr_strip (tesseract I/O)
    timeline.py      # Span dataclass, build_timeline (debounce + grace-bridge)
    review.py        # collect_unknowns, reconcile (+ bail-out)
    transcript.py    # parse_transcript, relabel_transcript, render_transcript
    coverage.py      # coverage_report (Caller-line attribution)
    sample.py        # extract_frames (ffmpeg I/O)
    locate.py        # crop_region, band_present (Pillow/numpy I/O)
    cli.py           # orchestrates stages end-to-end
    tests/
      __init__.py
      test_roster.py
      test_read.py
      test_timeline.py
      test_review.py
      test_transcript.py
      test_coverage.py
  speakers_roster.json
  docs/superpowers/specs/2026-06-04-meet-speaker-segmentation-design.md
  docs/superpowers/plans/2026-06-04-meet-speaker-segmentation.md
```

Run tests from repo root: `python3 -m pytest casting_call/tests/ -v`

---

## Task 1: Scaffold repo + migrate the pipeline scripts

Move the existing media scripts out of `gutils/scripts/` into `casting-call/bin/`, repoint the aliases, and confirm a migrated script still runs. The scripts reference each other by `$SCRIPT_DIR` and only depend on `$HOME/whisper-models`, so this is a relocate-and-repoint.

**Files:**
- Create: `casting-call/README.md`, `casting-call/.gitignore`
- Move: 6 scripts from `~/Developer/gutils/scripts/*.sh` → `~/Developer/casting-call/bin/`
- Modify: `~/Developer/gutils/oh-my-zsh-setup/gutils_aliases.zsh` (repoint alias paths)
- Modify: `~/Developer/gutils/CLAUDE.md` (update the `scripts/` media-processing note)

- [ ] **Step 1: Create README and .gitignore**

Create `casting-call/README.md`:

```markdown
# casting-call

Turn Google Meet call recordings into speaker-attributed transcripts.

Records a stereo `.mov` (left = your mic, right = everyone else) plus the on-screen Meet
window. Whisper transcribes the two audio channels; the speaker layer reads Meet's on-screen
caption labels to attribute the otherwise-undifferentiated "Caller" channel to named speakers.

## Pipeline (bin/)
- `extract_audio_stereo.sh` (`ripa`) — extract stereo audio + transcribe + relabel speakers
- `transcribe_batch_stereo.sh` (`ript`) — batch transcribe across date dirs
- `convert_video.sh` (`ripv`) — compress .mov to mp4
- `convert_recordings.sh` (`riprec`) — extract audio + convert video
- `extract_speakers.sh` (`rips`) — build the speaker timeline (speakers.json) from captions

## Speaker layer (casting_call/)
Python package: sample frames → locate caption band → OCR + roster-match → timeline →
review unknowns → relabel transcript. See `docs/superpowers/specs/`.

## Setup
    brew install tesseract ffmpeg whisper-cpp
    pip install --break-system-packages pillow numpy pytesseract pytest
```

Create `casting-call/.gitignore`:

```
__pycache__/
*.pyc
.pytest_cache/
frames/
review/
*.mov
*.mp3
*.wav
```

- [ ] **Step 2: Move the scripts into bin/**

Run:
```bash
SRC=~/Developer/gutils
DEST=~/Developer/casting-call
mkdir -p "$DEST/bin"
cd "$SRC"
for f in extract_audio_stereo.sh transcribe_batch_stereo.sh convert_video.sh \
         convert_recordings.sh extract_audio.sh transcribe_batch.sh; do
  cp "scripts/$f" "$DEST/bin/$f"
  git rm -q "scripts/$f"
done
chmod +x "$DEST"/bin/*.sh
ls "$DEST/bin"
```
Expected: six `.sh` files now in `casting-call/bin/`, removed from `gutils/scripts/`.

- [ ] **Step 3: Repoint the aliases**

In `~/Developer/gutils/oh-my-zsh-setup/gutils_aliases.zsh`, change the four alias targets from
`~/Developer/gutils/scripts/` to `~/Developer/casting-call/bin/`:

```bash
alias ripa='bash ~/Developer/casting-call/bin/extract_audio_stereo.sh'
alias ript='bash ~/Developer/casting-call/bin/transcribe_batch_stereo.sh'
alias ripv='bash ~/Developer/casting-call/bin/convert_video.sh'
alias riprec='bash ~/Developer/casting-call/bin/convert_recordings.sh'
```

- [ ] **Step 4: Update the gutils CLAUDE.md note**

In `~/Developer/gutils/CLAUDE.md`, the repo-structure block lists:
```
scripts/                 Media processing (audio extraction, transcription, video conversion)
```
Change that line to:
```
scripts/                 (media processing moved to ~/Developer/casting-call)
```

- [ ] **Step 5: Verify a migrated script still runs**

Run (empty-dir smoke test — `convert_video.sh` exits cleanly with no .mov files):
```bash
mkdir -p /tmp/cc_smoke && bash ~/Developer/casting-call/bin/convert_video.sh /tmp/cc_smoke
```
Expected: "Found 0 .mov files for video conversion" and clean exit (no path errors).

- [ ] **Step 6: Commit both repos**

```bash
cd ~/Developer/casting-call
git add README.md .gitignore bin/
git commit -m "feat: scaffold casting-call, migrate call-recording pipeline from gutils"

cd ~/Developer/gutils
git add scripts/ oh-my-zsh-setup/gutils_aliases.zsh CLAUDE.md
git commit -m "chore: move media pipeline to casting-call, repoint aliases"
```

---

## Task 2: Config + roster matching

**Files:**
- Create: `casting_call/__init__.py` (empty), `casting_call/tests/__init__.py` (empty)
- Create: `casting_call/config.py`, `casting_call/roster.py`, `speakers_roster.json`
- Test: `casting_call/tests/test_roster.py`

- [ ] **Step 1: Create package init files + config**

Create `casting_call/__init__.py` and `casting_call/tests/__init__.py` (both empty).

Create `casting_call/config.py`:

```python
from dataclasses import dataclass


@dataclass
class Config:
    fps: float = 2.0
    match_threshold: float = 0.72        # difflib ratio cutoff for roster matching
    debounce_seconds: float = 1.0        # drop speaker spans shorter than this
    grace_bridge_seconds: float = 3.0    # bridge brief no-signal gaps within one speaker
    caption_lag_seconds: float = 1.5     # captions trail audio; shift lookups back by this
    caller_label: str = 'Caller'
    self_label: str = 'You'              # how the existing transcript tags Sam's channel
    # (x, y, w, h) caption-strip crop hint, full-frame px. None = must pass --region in v1.
    caption_region: tuple | None = None
```

- [ ] **Step 2: Create the roster file**

Create `speakers_roster.json`:

```json
{
  "self": "Sam Gutentag",
  "members": [
    { "canonical": "Dana Park", "aliases": ["D Park", "Dana"] }
  ]
}
```

- [ ] **Step 3: Write the failing test**

Create `casting_call/tests/test_roster.py`:

```python
from casting_call.roster import Roster, match_name

ROSTER = Roster(
    self_name='Sam Gutentag',
    members=[{'canonical': 'Dana Park', 'aliases': ['D Park', 'Dana']}],
)


def test_you_maps_to_self():
    assert match_name('You', ROSTER, 0.72) == 'Sam Gutentag'
    assert match_name('you', ROSTER, 0.72) == 'Sam Gutentag'


def test_exact_canonical_match():
    assert match_name('Dana Park', ROSTER, 0.72) == 'Dana Park'


def test_garbled_ocr_snaps_to_roster():
    assert match_name('D4na Park', ROSTER, 0.72) == 'Dana Park'


def test_alias_match():
    assert match_name('Dana', ROSTER, 0.72) == 'Dana Park'


def test_unknown_returns_none():
    assert match_name('Jordan Mendez', ROSTER, 0.72) is None
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_roster.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'casting_call.roster'`

- [ ] **Step 5: Implement `roster.py`**

Create `casting_call/roster.py`:

```python
import json
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class Roster:
    self_name: str
    members: list  # list of {'canonical': str, 'aliases': list[str]}


def load_roster(path) -> Roster:
    with open(path) as f:
        data = json.load(f)
    return Roster(self_name=data['self'], members=data.get('members', []))


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def match_name(raw: str, roster: Roster, threshold: float):
    """Return the canonical roster name for an OCR'd label, or None.

    'You' (any case) maps to the roster self name. Otherwise fuzzy-match against
    each member's canonical name and aliases; best canonical above threshold wins.
    """
    cleaned = raw.strip()
    if cleaned.lower() == 'you':
        return roster.self_name

    best_name = None
    best_score = 0.0
    for member in roster.members:
        candidates = [member['canonical'], *member.get('aliases', [])]
        score = max(_ratio(cleaned, c) for c in candidates)
        if score > best_score:
            best_score = score
            best_name = member['canonical']

    return best_name if best_score >= threshold else None
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_roster.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Commit**

```bash
git add casting_call/__init__.py casting_call/tests/__init__.py casting_call/config.py \
        casting_call/roster.py speakers_roster.json casting_call/tests/test_roster.py
git commit -m "feat: config + roster fuzzy matching"
```

---

## Task 3: Caption parsing + validation gate

**Files:**
- Create: `casting_call/read.py`
- Test: `casting_call/tests/test_read.py`

Parses OCR text of the caption strip, returns the bottom-most (most recent) speaker. Distinguishes a short name *label* from a spoken *sentence*. Returns `(None, None)` when nothing looks like a caption — the occlusion signal.

- [ ] **Step 1: Write the failing test**

Create `casting_call/tests/test_read.py`:

```python
from casting_call.roster import Roster
from casting_call.read import read_active_speaker

ROSTER = Roster(
    self_name='Sam Gutentag',
    members=[{'canonical': 'Dana Park', 'aliases': ['D Park', 'Dana']}],
)

CAPTION_LINES = [
    'grids',
    'You',
    'Yeah.',
    'Dana Park',
    'so what about the sidebar layout here?',
    'You',
    "Yeah, that part makes sense. Yeah.",
]


def test_returns_bottom_most_speaker():
    name, raw = read_active_speaker(CAPTION_LINES, ROSTER, 0.72)
    assert name == 'Sam Gutentag'
    assert raw == 'You'


def test_picks_other_speaker_when_they_are_last():
    lines = ['You', 'Yeah.', 'Dana Park', 'so what about the sidebar layout?']
    name, raw = read_active_speaker(lines, ROSTER, 0.72)
    assert name == 'Dana Park'


def test_unknown_name_is_unresolved_not_occlusion():
    lines = ['You', 'hello', 'Jrdn Mendez', 'whats up everyone']
    name, raw = read_active_speaker(lines, ROSTER, 0.72)
    assert name is None
    assert raw == 'Jrdn Mendez'


def test_non_caption_text_is_occlusion():
    lines = [
        'def relabel_transcript(lines, spans, lag_seconds):',
        '    for entry in lines:',
        '        t = entry["t"] - lag_seconds',
    ]
    name, raw = read_active_speaker(lines, ROSTER, 0.72)
    assert name is None
    assert raw is None


def test_empty_is_occlusion():
    assert read_active_speaker([], ROSTER, 0.72) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_read.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'casting_call.read'`

- [ ] **Step 3: Implement `read.py` (parse only; OCR I/O added in Task 9)**

Create `casting_call/read.py`:

```python
from .roster import match_name


def _is_name_like(line: str) -> bool:
    """A Meet name label is short with no sentence-ending punctuation or code chars."""
    words = line.split()
    if not (1 <= len(words) <= 4):
        return False
    if line.rstrip()[-1:] in '.?!,:;':
        return False
    if any(ch in line for ch in '(){}[]=<>/'):
        return False
    return any(w[:1].isupper() for w in words)


def read_active_speaker(ocr_lines, roster, threshold):
    """Scan caption lines bottom-up for the most recent speaker.

    Returns (canonical_name, raw_label):
      - (name, raw)  a name-like line matched the roster at/above threshold
      - (None, raw)  a name-like line exists but below threshold (unknown -> review)
      - (None, None) no name-like line at all (occlusion / tab switch / silence)
    """
    for line in reversed(ocr_lines):
        stripped = line.strip()
        if not stripped or not _is_name_like(stripped):
            continue
        return (match_name(stripped, roster, threshold), stripped)
    return (None, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_read.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add casting_call/read.py casting_call/tests/test_read.py
git commit -m "feat: caption parse + occlusion/unknown validation gate"
```

---

## Task 4: Timeline builder (debounce + grace-bridge)

**Files:**
- Create: `casting_call/timeline.py`
- Test: `casting_call/tests/test_timeline.py`

- [ ] **Step 1: Write the failing test**

Create `casting_call/tests/test_timeline.py`:

```python
from casting_call.timeline import Span, build_timeline

FPS = 2.0  # 0.5s per frame


def obs(*triples):
    return [(i, n, r) for i, n, r in triples]


def test_collapses_consecutive_same_speaker():
    observations = obs(
        (0, 'Sam Gutentag', 'You'),
        (1, 'Sam Gutentag', 'You'),
        (2, 'Sam Gutentag', 'You'),
        (3, 'Dana Park', 'Dana Park'),
        (4, 'Dana Park', 'Dana Park'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    assert len(spans) == 2
    assert spans[0].name == 'Sam Gutentag'
    assert spans[0].start == 0.0
    assert spans[0].end == 1.5
    assert spans[1].name == 'Dana Park'
    assert spans[1].start == 1.5


def test_drops_subsecond_flicker():
    observations = obs(
        (0, 'Sam Gutentag', 'You'),
        (1, 'Sam Gutentag', 'You'),
        (2, 'Dana Park', 'Dana Park'),  # single-frame flicker
        (3, 'Sam Gutentag', 'You'),
        (4, 'Sam Gutentag', 'You'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    assert [s.name for s in spans] == ['Sam Gutentag']


def test_grace_bridges_brief_gap_same_speaker():
    observations = obs(
        (0, 'Dana Park', 'Dana Park'),
        (1, 'Dana Park', 'Dana Park'),
        (2, None, None),
        (3, None, None),  # 1.0s gap < 3.0s grace
        (4, 'Dana Park', 'Dana Park'),
        (5, 'Dana Park', 'Dana Park'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    real = [s for s in spans if s.name == 'Dana Park']
    assert len(real) == 1
    assert real[0].start == 0.0
    assert real[0].end == 3.0


def test_long_gap_not_bridged():
    observations = obs(
        (0, 'Dana Park', 'Dana Park'),
        (1, 'Dana Park', 'Dana Park'),
        (2, None, None), (3, None, None), (4, None, None),
        (5, None, None), (6, None, None), (7, None, None),  # 3.0s gap >= grace
        (8, 'Dana Park', 'Dana Park'),
        (9, 'Dana Park', 'Dana Park'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=1.0, grace_bridge_seconds=3.0)
    real = [s for s in spans if s.name == 'Dana Park']
    assert len(real) == 2


def test_unresolved_preserved():
    observations = obs(
        (0, None, 'Jrdn Mendez'),
        (1, None, 'Jrdn Mendez'),
    )
    spans = build_timeline(observations, fps=FPS, debounce_seconds=0.5, grace_bridge_seconds=3.0)
    assert len(spans) == 1
    assert spans[0].name is None
    assert spans[0].unresolved is True
    assert spans[0].raw_ocr == 'Jrdn Mendez'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_timeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'casting_call.timeline'`

- [ ] **Step 3: Implement `timeline.py`**

Create `casting_call/timeline.py`:

```python
from dataclasses import dataclass


@dataclass
class Span:
    start: float
    end: float
    name: str | None
    raw_ocr: str | None = None
    confidence: float = 1.0
    unresolved: bool = False
    review_id: str | None = None


def _key(name, raw):
    if name is not None:
        return ('name', name)
    if raw is not None:
        return ('unknown', raw)
    return ('gap', None)


def _raw_runs(observations, fps):
    runs = []
    for frame_index, name, raw in observations:
        t = frame_index / fps
        k = _key(name, raw)
        if runs and runs[-1][3] == k:
            runs[-1][1] = t + (1.0 / fps)
        else:
            runs.append([t, t + (1.0 / fps), (name, raw), k])
    return runs


def build_timeline(observations, fps, debounce_seconds, grace_bridge_seconds):
    runs = _raw_runs(observations, fps)

    # Drop sub-debounce non-gap flickers by merging into the previous run.
    cleaned = []
    for run in runs:
        start, end, payload, k = run
        if k[0] != 'gap' and (end - start) < debounce_seconds and cleaned:
            cleaned[-1][1] = end
            continue
        cleaned.append(run)

    # Grace-bridge: a short gap flanked by the same named speaker is absorbed.
    bridged = []
    i = 0
    while i < len(cleaned):
        run = cleaned[i]
        if (
            run[3][0] == 'gap'
            and bridged
            and i + 1 < len(cleaned)
            and (run[1] - run[0]) < grace_bridge_seconds
            and bridged[-1][3] == cleaned[i + 1][3]
            and bridged[-1][3][0] == 'name'
        ):
            bridged[-1][1] = cleaned[i + 1][1]
            i += 2
            continue
        bridged.append(run)
        i += 1

    spans = []
    for start, end, (name, raw), k in bridged:
        if k[0] == 'gap':
            continue  # gaps are not emitted; relabel falls back to Caller
        spans.append(Span(
            start=round(start, 3), end=round(end, 3),
            name=name, raw_ocr=raw, unresolved=(name is None),
        ))
    return spans
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_timeline.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add casting_call/timeline.py casting_call/tests/test_timeline.py
git commit -m "feat: timeline builder with debounce + grace-bridge"
```

---

## Task 5: Review — collect unknowns + reconcile with bail-out

**Files:**
- Create: `casting_call/review.py`
- Test: `casting_call/tests/test_review.py`

- [ ] **Step 1: Write the failing test**

Create `casting_call/tests/test_review.py`:

```python
from casting_call.timeline import Span
from casting_call.review import collect_unknowns, reconcile


def make_spans():
    return [
        Span(0.0, 5.0, 'Sam Gutentag', 'You'),
        Span(5.0, 9.0, None, 'Jrdn Mendez', unresolved=True),
        Span(9.0, 12.0, None, 'Cassie R', unresolved=True),
        Span(12.0, 15.0, None, 'Jrdn Mendez', unresolved=True),
    ]


def test_collect_distinct_unknowns_assigns_review_ids():
    spans = make_spans()
    unknowns = collect_unknowns(spans)
    assert len(unknowns) == 2
    assert unknowns[0] == {'review_id': 'unknown_01', 'raw_ocr': 'Jrdn Mendez'}
    assert unknowns[1]['review_id'] == 'unknown_02'
    ids = {s.review_id for s in spans if s.raw_ocr == 'Jrdn Mendez'}
    assert ids == {'unknown_01'}


def test_reconcile_applies_mapping():
    spans = make_spans()
    collect_unknowns(spans)
    out = reconcile(spans, {'unknown_01': 'Jordan Mendez', 'unknown_02': 'Cassie Reyes'})
    by_time = {s.start: s.name for s in out}
    assert by_time[5.0] == 'Jordan Mendez'
    assert by_time[9.0] == 'Cassie Reyes'
    assert by_time[12.0] == 'Jordan Mendez'
    assert all(not s.unresolved for s in out)


def test_reconcile_bail_collapses_rest_to_caller():
    spans = make_spans()
    collect_unknowns(spans)
    out = reconcile(spans, {'unknown_01': 'Jordan Mendez'}, bail=True)
    by_time = {s.start: s for s in out}
    assert by_time[5.0].name == 'Jordan Mendez'
    assert by_time[9.0].name is None
    assert by_time[9.0].unresolved is False
    assert by_time[12.0].name == 'Jordan Mendez'


def test_reconcile_partial_no_bail_leaves_unmapped_pending():
    spans = make_spans()
    collect_unknowns(spans)
    out = reconcile(spans, {'unknown_01': 'Jordan Mendez'}, bail=False)
    by_time = {s.start: s for s in out}
    assert by_time[9.0].unresolved is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_review.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'casting_call.review'`

- [ ] **Step 3: Implement `review.py`**

Create `casting_call/review.py`:

```python
def collect_unknowns(spans):
    """Assign a stable review_id to each DISTINCT unresolved raw label.

    Mutates spans in place (sets review_id) and returns the review list:
    [{'review_id': 'unknown_01', 'raw_ocr': '...'}].
    """
    order = []
    seen = {}
    for span in spans:
        if not span.unresolved:
            continue
        raw = span.raw_ocr
        if raw not in seen:
            rid = f'unknown_{len(order) + 1:02d}'
            seen[raw] = rid
            order.append({'review_id': rid, 'raw_ocr': raw})
        span.review_id = seen[raw]
    return order


def reconcile(spans, mapping, bail=False):
    """Apply a review_id -> name mapping.

    - mapped review_ids become that name (resolved).
    - if bail: every still-unresolved span collapses to Caller (name stays None,
      unresolved cleared).
    - if not bail: unmapped unknowns remain pending.
    """
    for span in spans:
        if span.review_id and span.review_id in mapping:
            span.name = mapping[span.review_id]
            span.unresolved = False
        elif span.unresolved and bail:
            span.unresolved = False
    return spans
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_review.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add casting_call/review.py casting_call/tests/test_review.py
git commit -m "feat: unknown review with bail-out to Caller"
```

---

## Task 6: Transcript relabel

**Files:**
- Create: `casting_call/transcript.py`
- Test: `casting_call/tests/test_transcript.py`

Parses the existing transcript format (`[h:mm:ss] [Label] text`), relabels `[Caller]` lines using the timeline (line timestamp minus caption lag → covering span), renders back. Self-name spans are bleed → stay Caller. Only Caller lines are touched.

- [ ] **Step 1: Write the failing test**

Create `casting_call/tests/test_transcript.py`:

```python
from casting_call.timeline import Span
from casting_call.transcript import parse_transcript, relabel_transcript, render_transcript

SPANS = [
    Span(0.0, 10.0, 'Sam Gutentag', 'You'),
    Span(10.0, 20.0, 'Dana Park', 'Dana Park'),
    Span(20.0, 30.0, None, 'Jrdn', unresolved=True),
]

TRANSCRIPT = (
    "[0:00:03] [You] hey\n"
    "[0:00:12] [Caller] some more?\n"        # 12s -1.5 lag = 10.5 -> Dana
    "[0:00:05] [Caller] uh huh\n"            # 5s span is Sam -> bleed -> Caller
    "[0:00:25] [Caller] hello\n"             # unresolved span -> Caller
)


def test_parse_transcript():
    entries = parse_transcript(TRANSCRIPT)
    assert entries[0] == {'t': 3, 'label': 'You', 'text': 'hey'}
    assert entries[1] == {'t': 12, 'label': 'Caller', 'text': 'some more?'}


def test_relabel_caller_by_timeline():
    entries = parse_transcript(TRANSCRIPT)
    out = relabel_transcript(entries, SPANS, lag_seconds=1.5,
                             self_name='Sam Gutentag', caller_label='Caller')
    assert [e['label'] for e in out] == ['You', 'Dana Park', 'Caller', 'Caller']


def test_you_lines_untouched():
    entries = parse_transcript("[0:00:03] [You] hey\n")
    out = relabel_transcript(entries, SPANS, lag_seconds=1.5,
                             self_name='Sam Gutentag', caller_label='Caller')
    assert out[0]['label'] == 'You'


def test_off_timeline_stays_caller():
    entries = parse_transcript("[0:01:05] [Caller] hello there\n")  # 65s, off the end
    out = relabel_transcript(entries, SPANS, lag_seconds=0.0,
                             self_name='Sam Gutentag', caller_label='Caller')
    assert render_transcript(out) == "[0:01:05] [Caller] hello there\n"


def test_render_uses_resolved_name():
    entries = parse_transcript("[0:00:12] [Caller] some more?\n")
    out = relabel_transcript(entries, SPANS, lag_seconds=1.5,
                             self_name='Sam Gutentag', caller_label='Caller')
    assert render_transcript(out) == "[0:00:12] [Dana Park] some more?\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_transcript.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'casting_call.transcript'`

- [ ] **Step 3: Implement `transcript.py`**

Create `casting_call/transcript.py`:

```python
import re

LINE_RE = re.compile(r'^\[(\d+):(\d+):(\d+)\] \[([^\]]+)\] (.*)$')


def parse_transcript(text):
    """Parse '[h:mm:ss] [Label] text' lines into dicts with seconds 't'."""
    entries = []
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        h, mn, sec = int(m[1]), int(m[2]), int(m[3])
        entries.append({'t': h * 3600 + mn * 60 + sec, 'label': m[4], 'text': m[5]})
    return entries


def _name_at(spans, t):
    for span in spans:
        if span.start <= t < span.end:
            return span.name  # None for unresolved/gap
    return None


def relabel_transcript(entries, spans, lag_seconds, self_name, caller_label='Caller'):
    """Relabel only caller_label lines using the timeline at (t - lag).

    Self-name spans are bleed -> stay caller_label. Unresolved/gap/off-timeline
    -> stay caller_label. Other labels (e.g. You) are untouched. Each result
    carries 'original_label' (pre-relabel) for coverage.
    """
    out = []
    for entry in entries:
        result = dict(entry)
        result['original_label'] = entry['label']
        if entry['label'] == caller_label:
            who = _name_at(spans, entry['t'] - lag_seconds)
            if who and who != self_name:
                result['label'] = who
        out.append(result)
    return out


def render_transcript(entries):
    """Render entries back to '[h:mm:ss] [Label] text\\n' lines."""
    lines = []
    for entry in entries:
        h, rem = divmod(entry['t'], 3600)
        mn, sec = divmod(rem, 60)
        lines.append(f"[{h:01d}:{mn:02d}:{sec:02d}] [{entry['label']}] {entry['text']}\n")
    return ''.join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_transcript.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add casting_call/transcript.py casting_call/tests/test_transcript.py
git commit -m "feat: relabel Caller transcript lines by timeline"
```

---

## Task 7: Coverage report

**Files:**
- Create: `casting_call/coverage.py`
- Test: `casting_call/tests/test_coverage.py`

Computes attribution coverage from relabeled transcript entries: how many originally-Caller lines got a name versus stayed Caller (lost attribution). Reads `original_label` stamped by `relabel_transcript` (Task 6).

- [ ] **Step 1: Write the failing test**

Create `casting_call/tests/test_coverage.py`:

```python
from casting_call.coverage import coverage_report


def entry(t, original_label, label):
    return {'t': t, 'original_label': original_label, 'label': label, 'text': 'x'}


def test_attributed_vs_fallback_counts():
    entries = [
        entry(10, 'Caller', 'Dana Park'),
        entry(20, 'Caller', 'Caller'),
        entry(30, 'You', 'You'),  # not a caller line; ignored
    ]
    report = coverage_report(entries, caller_label='Caller')
    assert report['caller_lines'] == 2
    assert report['attributed_lines'] == 1
    assert report['attributed_pct'] == 50.0
    assert report['lost_lines'] == [{'t': 20, 'text': 'x'}]


def test_all_attributed():
    entries = [entry(5, 'Caller', 'Dana Park')]
    report = coverage_report(entries, caller_label='Caller')
    assert report['attributed_pct'] == 100.0
    assert report['lost_lines'] == []


def test_no_caller_lines_is_zero_safe():
    entries = [entry(5, 'You', 'You')]
    report = coverage_report(entries, caller_label='Caller')
    assert report['caller_lines'] == 0
    assert report['attributed_pct'] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_coverage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'casting_call.coverage'`

- [ ] **Step 3: Implement `coverage.py`**

Create `casting_call/coverage.py`:

```python
def coverage_report(entries, caller_label='Caller'):
    """Coverage over originally-Caller lines.

    Each entry carries 'original_label' (before relabel) and 'label' (after). A
    still-caller_label line is a lost attribution.
    """
    caller_entries = [e for e in entries if e.get('original_label') == caller_label]
    caller_lines = len(caller_entries)
    lost = [{'t': e['t'], 'text': e['text']} for e in caller_entries if e['label'] == caller_label]
    attributed_lines = caller_lines - len(lost)

    pct = round(attributed_lines / caller_lines * 100, 1) if caller_lines else 0.0
    return {
        'caller_lines': caller_lines,
        'attributed_lines': attributed_lines,
        'attributed_pct': pct,
        'lost_lines': lost,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/test_coverage.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add casting_call/coverage.py casting_call/tests/test_coverage.py
git commit -m "feat: attribution coverage report over Caller lines"
```

---

## Task 8: Frame sampling (ffmpeg I/O)

**Files:**
- Create: `casting_call/sample.py`

I/O glue — validated against the sample recording.

- [ ] **Step 1: Implement `sample.py`**

Create `casting_call/sample.py`:

```python
import subprocess
from pathlib import Path


def extract_frames(mov_path, out_dir, fps):
    """Extract frames at `fps` as frame_000001.png ... Frame N is at (N-1)/fps s."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for existing in out.glob('frame_*.png'):
        existing.unlink()
    subprocess.run(
        ['ffmpeg', '-i', str(mov_path), '-vf', f'fps={fps}',
         str(out / 'frame_%06d.png'), '-y', '-loglevel', 'error'],
        check=True,
    )
    return sorted(out.glob('frame_*.png'))


def frame_time(frame_path, fps):
    index = int(Path(frame_path).stem.split('_')[1])  # 1-based
    return (index - 1) / fps
```

- [ ] **Step 2: Validate against the sample**

Run:
```bash
cd ~/Developer/casting-call && python3 -c "
from casting_call.sample import extract_frames, frame_time
mov = '/path/to/recording.mov'
frames = extract_frames(mov, '/tmp/cc_frames', fps=2.0)
print('frames:', len(frames))
print('first:', frames[0].name, frame_time(frames[0], 2.0))
print('last :', frames[-1].name, frame_time(frames[-1], 2.0))
"
```
Expected: ~2600 frames, first at 0.0s, last near 1300s.

- [ ] **Step 3: Commit**

```bash
git add casting_call/sample.py
git commit -m "feat: ffmpeg frame sampling"
```

---

## Task 9: Caption locate + OCR (Pillow/numpy/tesseract I/O)

**Files:**
- Create: `casting_call/locate.py`
- Modify: `casting_call/read.py` (add `ocr_strip`)

I/O glue. v1 uses a `--region` hint; auto-locate is a follow-up. `band_present` is the per-frame occlusion signal.

- [ ] **Step 1: Implement `locate.py`**

Create `casting_call/locate.py`:

```python
import numpy as np
from PIL import Image


def crop_region(frame_path, region):
    """region = (x, y, w, h). Returns a PIL Image cropped to it."""
    x, y, w, h = region
    return Image.open(frame_path).crop((x, y, x + w, y + h))


def band_present(image, dark_fraction_min=0.35, dark_threshold=90):
    """Occlusion check: Meet's caption band is a large dark translucent strip.
    Returns True if enough of the crop is dark. Drops when Meet is covered or the
    tab switches to a light window.
    """
    gray = np.asarray(image.convert('L'))
    return float((gray < dark_threshold).mean()) >= dark_fraction_min
```

- [ ] **Step 2: Add `ocr_strip` to `read.py`**

Append to `casting_call/read.py`:

```python
import pytesseract


def ocr_strip(image):
    """OCR a caption-strip PIL image into a list of non-empty text lines."""
    text = pytesseract.image_to_string(image)
    return [line for line in text.splitlines() if line.strip()]
```

- [ ] **Step 3: Validate locate + OCR against the sample**

Run:
```bash
cd ~/Developer/casting-call && python3 -c "
from casting_call.locate import crop_region, band_present
from casting_call.read import ocr_strip, read_active_speaker
from casting_call.roster import load_roster
import subprocess
mov = '/path/to/recording.mov'
subprocess.run(['ffmpeg','-ss','650','-i',mov,'-frames:v','1','/tmp/probe.png','-y','-loglevel','error'], check=True)
region = (2240, 2080, 1856, 220)   # measured from the spike; lower band of the Meet pane
img = crop_region('/tmp/probe.png', region)
print('band_present:', band_present(img))
lines = ocr_strip(img)
print('ocr lines:', lines)
print('active:', read_active_speaker(lines, load_roster('speakers_roster.json'), 0.72))
"
```
Expected: `band_present: True`; OCR lines containing `You` / `Dana Park`; active speaker resolves to a roster name. If the region is off, open `/tmp/probe.png`, adjust the `region` tuple, re-run, and record the working region as the documented default for this recording style.

- [ ] **Step 4: Commit**

```bash
git add casting_call/locate.py casting_call/read.py
git commit -m "feat: caption-band locate + tesseract OCR"
```

---

## Task 10: CLI orchestration

**Files:**
- Create: `casting_call/cli.py`

Wires the stages: sample → per-frame (band check + OCR + read) → timeline → review → write `speakers.json`. Optional `--transcript` relabels an existing transcript in place and prints coverage.

- [ ] **Step 1: Implement `cli.py`**

Create `casting_call/cli.py`:

```python
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .config import Config
from .roster import load_roster
from .sample import extract_frames
from .locate import crop_region, band_present
from .read import ocr_strip, read_active_speaker
from .timeline import build_timeline
from .review import collect_unknowns, reconcile
from .transcript import parse_transcript, relabel_transcript, render_transcript
from .coverage import coverage_report


def observe_frames(frames, region, roster, cfg):
    observations = []
    for frame in frames:
        idx = int(frame.stem.split('_')[1])
        img = crop_region(frame, region)
        if not band_present(img):
            observations.append((idx, None, None))
            continue
        name, raw = read_active_speaker(ocr_strip(img), roster, cfg.match_threshold)
        observations.append((idx, name, raw))
    return observations


def run_review(spans, frames, region, out_dir, fps):
    unknowns = collect_unknowns(spans)
    if not unknowns:
        return reconcile(spans, {})
    print(f'\n{len(unknowns)} distinct unknown speaker(s) to review.')
    print('Enter a name for each, or "skip"/Enter to make the rest Caller.\n')
    review_dir = Path(out_dir) / 'review'
    review_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}
    bail = False
    for entry in unknowns:
        rid = entry['review_id']
        span = next(s for s in spans if s.review_id == rid)
        frame = min(frames, key=lambda f: abs((int(f.stem.split('_')[1]) - 1) / fps - span.start))
        crop_region(frame, region).save(review_dir / f'{rid}.png')
        answer = input(f'{rid} (raw OCR "{entry["raw_ocr"]}", crop {rid}.png) -> ').strip()
        if answer.lower() in ('skip', ''):
            bail = True
            break
        mapping[rid] = answer
    return reconcile(spans, mapping, bail=bail)


def main(argv=None):
    parser = argparse.ArgumentParser(description='casting-call speaker segmentation')
    parser.add_argument('mov')
    parser.add_argument('--roster', default=str(Path(__file__).parent.parent / 'speakers_roster.json'))
    parser.add_argument('--region', help='x,y,w,h caption strip crop (full-frame px)')
    parser.add_argument('--out', help='output dir (default: alongside the mov)')
    parser.add_argument('--transcript', help='existing transcript .txt to relabel in place')
    args = parser.parse_args(argv)

    cfg = Config()
    if args.region:
        cfg.caption_region = tuple(int(v) for v in args.region.split(','))
    if cfg.caption_region is None:
        print('error: --region x,y,w,h required in v1 (auto-locate is a follow-up)', file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else Path(args.mov).parent
    roster = load_roster(args.roster)

    frames = extract_frames(args.mov, Path(out_dir) / 'frames', cfg.fps)
    observations = observe_frames(frames, cfg.caption_region, roster, cfg)
    spans = build_timeline(observations, cfg.fps, cfg.debounce_seconds, cfg.grace_bridge_seconds)
    spans = run_review(spans, frames, cfg.caption_region, out_dir, cfg.fps)

    speakers_path = Path(out_dir) / 'speakers.json'
    with open(speakers_path, 'w') as f:
        json.dump([asdict(s) for s in spans], f, indent=2)
    print(f'\nwrote {speakers_path} ({len(spans)} spans)')

    if args.transcript:
        entries = parse_transcript(Path(args.transcript).read_text())
        relabeled = relabel_transcript(
            entries, spans, cfg.caption_lag_seconds, roster.self_name, cfg.caller_label)
        Path(args.transcript).write_text(render_transcript(relabeled))
        report = coverage_report(relabeled, cfg.caller_label)
        print(f"coverage: {report['attributed_lines']}/{report['caller_lines']} "
              f"Caller lines attributed ({report['attributed_pct']}%)")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
```

- [ ] **Step 2: Validate end-to-end on a short slice**

Run (uses the Task 9 region; trims to 2 min):
```bash
cd ~/Developer/casting-call
ffmpeg -ss 600 -t 120 -i /path/to/recording.mov /tmp/slice.mov -y -loglevel error
python3 -m casting_call.cli /tmp/slice.mov --region 2240,2080,1856,220 --out /tmp/cc_out </dev/null
cat /tmp/cc_out/speakers.json
```
Expected: `speakers.json` with alternating `Sam Gutentag` / `Dana Park` spans. Eyeball against the video. (`</dev/null` auto-bails review; run without it to label unknowns interactively.)

- [ ] **Step 3: Commit**

```bash
git add casting_call/cli.py
git commit -m "feat: CLI orchestration end-to-end"
```

---

## Task 11: rips wrapper + alias + native pipeline integration

**Files:**
- Create: `casting-call/bin/extract_speakers.sh`
- Modify: `casting-call/bin/extract_audio_stereo.sh` (call the relabel after transcript write)
- Modify: `~/Developer/gutils/oh-my-zsh-setup/gutils_aliases.zsh` (add `rips`)

- [ ] **Step 1: Create the wrapper**

Create `casting-call/bin/extract_speakers.sh`:

```bash
#!/bin/bash
# Build a caption-derived speaker timeline (speakers.json) for a Meet recording.
#
# Usage: extract_speakers.sh <recording.mov> --region x,y,w,h [--out dir] [--transcript t.txt]
#
# Dependencies: brew install tesseract ffmpeg
#               pip install --break-system-packages pillow numpy pytesseract

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v tesseract &>/dev/null; then
    echo "Error: tesseract not found. Run: brew install tesseract"; exit 1
fi

cd "$REPO_DIR"
exec python3 -m casting_call.cli "$@"
```

Make executable: `chmod +x ~/Developer/casting-call/bin/extract_speakers.sh`

- [ ] **Step 2: Add the rips alias**

In `~/Developer/gutils/oh-my-zsh-setup/gutils_aliases.zsh`, add alongside the repointed rip* aliases:

```bash
alias rips='bash ~/Developer/casting-call/bin/extract_speakers.sh'
```

- [ ] **Step 3: Make extract_audio_stereo.sh relabel from speakers.json**

In `casting-call/bin/extract_audio_stereo.sh`, find the merge success branch (after
`echo "  ✓ Done ($result)"`, around line 277). Immediately after that line, add a relabel step
that runs only when `speakers.json` exists next to the recording and calls the package:

```bash
    # Relabel Caller lines from a caption-derived speaker timeline, if present.
    speakers_json="$INPUT_DIR/speakers.json"
    if [ -f "$speakers_json" ]; then
        echo "  → Relabeling Caller lines from speakers.json..."
        REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
        ( cd "$REPO_DIR" && python3 -c "
import sys, json
from casting_call.timeline import Span
from casting_call.roster import load_roster
from casting_call.transcript import parse_transcript, relabel_transcript, render_transcript
from casting_call.coverage import coverage_report
txt, spath = sys.argv[1], sys.argv[2]
roster = load_roster('speakers_roster.json')
spans = [Span(**s) for s in json.load(open(spath))]
entries = parse_transcript(open(txt).read())
out = relabel_transcript(entries, spans, 1.5, roster.self_name, 'Caller')
open(txt, 'w').write(render_transcript(out))
r = coverage_report(out, 'Caller')
print(f\"  ✓ {r['attributed_lines']}/{r['caller_lines']} Caller lines attributed ({r['attributed_pct']}%)\")
" "$txt_out" "$speakers_json" )
    fi
```

This is a no-op when `speakers.json` is absent, so `ripa` is unchanged for recordings without a
timeline. The self name is read from the roster, so it stays correct if the roster `self` changes.

- [ ] **Step 4: Run the full unit-test suite**

Run: `cd ~/Developer/casting-call && python3 -m pytest casting_call/tests/ -v`
Expected: PASS — all tests from Tasks 2–7 green (roster, read, timeline, review, transcript, coverage).

- [ ] **Step 5: Integration check — relabel a real transcript line**

Using the 2-min slice from Task 10 and a hand-made Caller line that falls in an Dana span:
```bash
cd ~/Developer/casting-call
mkdir -p /tmp/cc_out
printf '[0:00:12] [You] hi\n[0:00:16] [Caller] some more?\n' > /tmp/cc_out/t.txt
python3 -m casting_call.cli /tmp/slice.mov --region 2240,2080,1856,220 --out /tmp/cc_out --transcript /tmp/cc_out/t.txt </dev/null
cat /tmp/cc_out/t.txt
```
Expected: the `[Caller]` line is relabeled to the speaker the timeline shows at ~14.5s into the slice, and a coverage line prints. Eyeball against the video.

- [ ] **Step 6: Commit**

```bash
cd ~/Developer/casting-call
git add bin/extract_speakers.sh bin/extract_audio_stereo.sh
git commit -m "feat: rips wrapper + native speaker relabel in stereo pipeline"

cd ~/Developer/gutils
git add oh-my-zsh-setup/gutils_aliases.zsh
git commit -m "chore: add rips alias for casting-call"
```

---

## Self-Review Notes

**Spec coverage check:**
- Full-pipeline ownership / migration + alias repoint → Task 1.
- Caption-primary signal → Tasks 3, 9, 10. Border provider deferred → not built (spec-aligned).
- Roster fuzzy-match + `You`→self → Task 2.
- Validation gate (occlusion vs unknown vs caption) → Task 3 (`read_active_speaker`) + Task 9 (`band_present`).
- Timeline debounce + grace-bridge → Task 4.
- Review with up-front count + bail-out → Task 5 + Task 10 (`run_review`).
- Relabel by timestamp − lag, self-as-bleed, You untouched → Task 6.
- Coverage incl. lost lines → Task 7.
- `sample`/`locate` stages → Tasks 8, 9. `locate` v1 = `--region` hint; auto-locate deferred (spec-flagged).
- Native integration into migrated `extract_audio_stereo.sh` + `rips` → Task 11.
- Group-call tuning → noted as follow-up; sample is 2-person plumbing validation per spec.

**Type/name consistency:** `Span` fields are shared across timeline/review/transcript; `speakers.json` is dumped via `asdict(Span)` and reloaded via `Span(**s)` (Tasks 10, 11) — field names match. `relabel_transcript` stamps `original_label`, which `coverage_report` reads (Tasks 6, 7).

**Deferred / follow-up (not v1):**
- Auto-locate of the caption band (uses `--region` hint in v1).
- BorderProvider fallback (interface seam exists via the observation model; not implemented).
- Group-call constant tuning (lag, debounce, threshold) against real group footage.
- Appending resolved unknowns back into `speakers_roster.json` (manual in v1).
- The non-stereo legacy scripts move as-is; their behavior is unchanged and out of scope.
```