import importlib.util
import json
import types
from pathlib import Path

from casting_call.digest import build_digest_from_entries
from casting_call.transcript import parse_transcript

RENDER = Path(__file__).resolve().parents[2] / "skills" / "process-transcript" / "render.py"
_spec = importlib.util.spec_from_file_location("pt_render", RENDER)
render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render)

TRANSCRIPT = """\
[0:00:05] [You] hey Corey
[0:00:08] [Corey Weathers] hey, can you send me the eval numbers
[0:00:20] [MARKER] marker
[0:00:30] [Corey Weathers] I'll write up the launch plan
[0:00:40] [You] I'll fix the nav labels
[0:01:00] [Corey Weathers] okay bye
"""


def _digest():
    entries = parse_transcript(TRANSCRIPT)
    d = build_digest_from_entries(entries)
    d.update(name="call", audio_path="/a/call.mp3", video_path=None)
    return d


def _lang(**over):
    L = types.SimpleNamespace(
        TITLE="Corey Sync", DATE="November 10, 2026",
        SUMMARY="Sam and Corey talked evals.",
        TOPICS={}, ONE={20: "Corey walked through the eval dashboard."},
        DECISIONS=[("Ship it.", "No waiting on brand.")],
        INFERRED=[
            (8, "me", "Send the eval numbers.", "inferred", "Corey Weathers"),
            {"t": 40, "owner": "me", "text": "Fix the nav labels."},
            (30, "them", "Write up the launch plan."),
            (50, "question", "Who owns the blog?"),
        ])
    for k, v in over.items():
        setattr(L, k, v)
    return L


def test_digest_counts_any_far_side_label():
    d = _digest()
    assert d["far_side"] == ["Corey Weathers"]
    assert d["duration"] == 55


def test_task_phrasing_names_who_asked_and_who_owes(tmp_path):
    out = tmp_path / "call-call.html"
    page = render.render_call(_digest(), _lang(), out)
    assert "Corey Weathers asked you to send the eval numbers." in page
    assert "You said you&#x27;d fix the nav labels." in page
    # Two-party call: an owed item with no person is the one far-side voice.
    assert "Corey Weathers will write up the launch plan." in page
    assert page.index('id="asks"') < page.index('id="owed"') < page.index('id="summary"') \
        < page.index('id="marked"') < page.index('<details class="more"')
    assert "Who owns the blog?" in page.split('<details class="more"')[1]
    assert "No summary for this moment" not in page
    assert "with Sam, Corey Weathers" in page


def test_one_tuple_names_a_person_and_unnamed_caller_reads_they(tmp_path):
    entries = parse_transcript(TRANSCRIPT.replace("Corey Weathers", "Caller")
                               + "[0:01:05] [MARKER] action-me\n")
    d = build_digest_from_entries(entries)
    d.update(name="call", audio_path=None, video_path=None)
    L = _lang(ONE={20: "Dashboard.", 65: ("Book the room.", "Dana")},
              INFERRED=[(30, "them", "Write up the launch plan.")])
    page = render.render_call(d, L, tmp_path / "call-call.html")
    assert "Dana asked you to book the room." in page
    assert "They will write up the launch plan." in page


def test_old_phrasing_is_not_doubled():
    i = {"type": "action-me", "person": None}
    assert render.task_line(i, "Corey asked you to share the deck.") == \
        "Corey asked you to share the deck."
    assert render.task_line(i, "SOW goes out Friday.") == "You said you'd SOW goes out Friday."


def test_summary_json_written_next_to_page(tmp_path):
    out = tmp_path / "call-call.html"
    render.render_call(_digest(), _lang(), out)
    doc = json.loads((tmp_path / "call-summary.json").read_text())
    assert doc["title"] == "Corey Sync" and doc["duration"] == 55
    assert doc["people"] == ["Sam", "Corey Weathers"]
    assert doc["decisions"] == [{"decision": "Ship it.", "context": "No waiting on brand."}]
    ask = doc["asks_of_you"][0]
    assert (ask["t"], ask["person"], ask["text"], ask["badge"]) == \
        (8, "Corey Weathers", "Send the eval numbers.", "inferred")
    assert doc["asks_of_you"][1]["person"] is None
    assert doc["owed_to_you"][0]["person"] == "Corey Weathers"
    assert doc["questions"][0]["text"] == "Who owns the blog?"
    assert doc["markers"] == [{"t": 20, "tstr": "0:00:20", "type": "marker",
                               "summary": "Corey walked through the eval dashboard.",
                               "frame": None}]
    assert doc["subjects"][0]["name"] == "The call"


def _lines(rows):
    return [{"t": t, "label": lab, "text": txt, "tstr": render.hms(t)} for t, lab, txt in rows]


def test_speaker_order_puts_you_first_and_caller_last():
    talk = {"Caller": 50, "Ed": 10, "You": 5, "Corey": 30}
    assert render.speaker_order(talk) == ["You", "Corey", "Ed", "Caller"]


def test_speaking_spans_cap_long_gaps():
    spans = render.speaking_spans(_lines([(0, "You", "a"), (100, "Ed", "b")]), 120)
    assert spans == [("You", 0, render.SPAN_CAP), ("Ed", 100, 115)]


def test_transcript_columns_merge_runs_and_place_columns():
    html = render.transcript_columns_html(
        _lines([(0, "You", "hi"), (3, "You", "there"), (6, "Corey", "yo"), (9, "You", "ok")]),
        12, "/a.mp3")
    assert html.count('class="th"') == 2
    assert "hi there" in html
    assert 'grid-column:2;grid-row:3' in html
    assert "Scroll sideways" not in html


def test_speaking_section_reads_caption_names(tmp_path):
    parts = tmp_path / "parts"
    parts.mkdir()
    (parts / "speaker-names.json").write_text(json.dumps({
        "names": {"Corey": {"lines": 2, "spellings": {"Corey": 9}}},
        "frames": 10, "frames_with_meet": 9, "lines": [{"name": "Corey"}, {"name": None}]}))
    tx = tmp_path / "call.txt"
    html = render.speaking_html(_lines([(0, "You", "a"), (5, "Corey", "b")]), 20, str(tx), None)
    assert "How names were found" in html and "1 far-side lines" in html
    assert "speakSeek" not in html.split("<svg")[1].split(">")[0]
