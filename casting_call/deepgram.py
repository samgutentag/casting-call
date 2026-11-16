"""
deepgram — transcribe one call track with Deepgram, for ripa.

ripa hands this the same 16 kHz track WAV it would give whisper. The track is
encoded to Opus (about 14 MB an hour instead of 115 MB of WAV) and sent to
Deepgram's prerecorded API. The answer is written as an SRT in the shape
whisper writes, so ripa's weave, clean, caption and marker steps run unchanged.

Every request carries mip_opt_out=true, which keeps the call audio out of
Deepgram's Model Improvement Program. It is not a parameter: no caller can
build a request without it.

On any failure (no key, network, an error response, a bad answer) transcribe()
returns None and a short reason, and ripa falls back to whisper for that track.

Usage (from ripa):
    python3 -m casting_call.deepgram has-key
    python3 -m casting_call.deepgram transcribe <track.wav> --srt <out.srt> \\
        --raw <parts/track.deepgram.json> [--diarize --speakers <parts/caller.speakers.json>]
Exit codes: 0 done, 3 failed (reason on stdout), 2 bad usage.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.deepgram.com/v1/listen"
MODEL = "nova-3"
BASE_PARAMS = {
    "model": MODEL,
    "smart_format": "true",
    "punctuate": "true",
    "utterances": "true",
    "mip_opt_out": "true",
}
ENGINE_NOTE = f"Deepgram {MODEL} (mip_opt_out)"

GAP = 1.5          # seconds of silence that end a line
SOFT_MAX = 20.0    # after this long, a line ends at the next sentence end
HARD_MAX = 30.0    # a line never runs longer than this
RETRIES = 2
BACKOFF = (5, 15)

REPO = Path(__file__).resolve().parents[1]


# --- key ---------------------------------------------------------------------

def _env_files():
    yield REPO / ".env"
    # A worktree has no .env of its own; the main checkout's is the real one.
    try:
        common = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        if common:
            yield (REPO / common).resolve().parent / ".env"
    except (OSError, subprocess.SubprocessError):
        pass


def load_key():
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if key:
        return key
    for path in _env_files():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                name, _, value = line.partition("=")
                if name.strip() == "DEEPGRAM_API_KEY" and value.strip():
                    return value.strip().strip('"').strip("'")
        except OSError:
            continue
    return None


# --- request -----------------------------------------------------------------

def build_url(diarize=False):
    params = dict(BASE_PARAMS)
    if diarize:
        params["diarize"] = "true"
    # Set last so nothing above can drop or change it.
    params["mip_opt_out"] = "true"
    return API + "?" + urllib.parse.urlencode(params)


def encode_opus(wav, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ac", "1",
                    "-c:a", "libopus", "-b:a", "32k", str(out)], check=True, timeout=600)


def timeout_for(nbytes):
    """Upload plus processing time, generous: a slow uplink is the usual failure."""
    return 120 + int(nbytes / 1e6 * 8)


def post(audio_path, key, diarize=False, urlopen=urllib.request.urlopen, sleep=time.sleep):
    """POST the audio. Returns (response dict, None) or (None, reason)."""
    data = Path(audio_path).read_bytes()
    url = build_url(diarize)
    reason = "no attempt made"
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization": f"Token {key}", "Content-Type": "audio/ogg"})
        try:
            with urlopen(req, timeout=timeout_for(len(data))) as resp:
                return json.loads(resp.read().decode("utf-8")), None
        except urllib.error.HTTPError as err:
            reason = f"HTTP {err.code}"
            # A bad key or a rejected file will not get better on a retry.
            if err.code < 500 and err.code != 429:
                return None, reason
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            reason = f"network: {getattr(err, 'reason', err)}"
        except ValueError:
            return None, "unreadable response"
        if attempt < RETRIES:
            sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    return None, reason


# --- response -> lines -------------------------------------------------------

def words_of(response):
    try:
        return response["results"]["channels"][0]["alternatives"][0].get("words", [])
    except (KeyError, IndexError, TypeError):
        raise ValueError("no words in the response")


def to_lines(words, gap=GAP, soft_max=SOFT_MAX, hard_max=HARD_MAX):
    """Group words into transcript lines: a line ends on a speaker change, a
    pause of `gap`, a sentence end once it is `soft_max` long, or `hard_max`."""
    lines, cur = [], None
    for w in words:
        text = w.get("punctuated_word") or w.get("word") or ""
        if not text:
            continue
        spk = w.get("speaker", 0)
        start, end = float(w["start"]), float(w["end"])
        if cur:
            length = cur["end"] - cur["start"]
            ended = cur["text"].rstrip().endswith((".", "?", "!"))
            if (spk != cur["speaker"] or start - cur["end"] > gap or length >= hard_max
                    or (length >= soft_max and ended)):
                lines.append(cur)
                cur = None
        if cur is None:
            cur = {"start": start, "end": end, "speaker": spk, "text": text}
        else:
            cur["end"] = end
            cur["text"] += " " + text
    if cur:
        lines.append(cur)
    return lines


def _srt_time(sec):
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(lines):
    return "".join(f"{n}\n{_srt_time(l['start'])} --> {_srt_time(l['end'])}\n{l['text']}\n\n"
                   for n, l in enumerate(lines, 1))


def speakers_doc(lines):
    """Per-line Deepgram speaker, keyed by the whole second the transcript shows."""
    return [{"t": int(l["start"]), "start": round(l["start"], 2), "end": round(l["end"], 2),
             "speaker": l["speaker"]} for l in lines]


# --- the whole step ----------------------------------------------------------

def transcribe(wav, srt_out, raw_out=None, diarize=False, speakers_out=None,
               key=None, urlopen=urllib.request.urlopen, sleep=time.sleep):
    """Returns (number of lines, None) on success or (None, reason)."""
    key = key or load_key()
    if not key:
        return None, "no DEEPGRAM_API_KEY"
    with tempfile.TemporaryDirectory() as tmp:
        ogg = Path(tmp) / "track.ogg"
        try:
            encode_opus(wav, ogg)
        except (OSError, subprocess.SubprocessError) as err:
            return None, f"could not encode the track ({type(err).__name__})"
        response, reason = post(ogg, key, diarize, urlopen=urlopen, sleep=sleep)
    if response is None:
        return None, reason
    try:
        lines = to_lines(words_of(response))
    except (ValueError, KeyError, TypeError) as err:
        return None, f"bad response: {err}"
    try:
        if raw_out:
            Path(raw_out).write_text(json.dumps(response, indent=1), encoding="utf-8")
        Path(srt_out).write_text(to_srt(lines), encoding="utf-8")
        if speakers_out and diarize:
            Path(speakers_out).write_text(json.dumps(speakers_doc(lines), indent=1),
                                          encoding="utf-8")
    except OSError as err:
        return None, f"could not write output ({err.strerror})"
    return len(lines), None


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Transcribe a call track with Deepgram.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("has-key")
    tr = sub.add_parser("transcribe")
    tr.add_argument("wav")
    tr.add_argument("--srt", required=True)
    tr.add_argument("--raw")
    tr.add_argument("--diarize", action="store_true")
    tr.add_argument("--speakers")
    args = ap.parse_args(argv)
    if args.cmd == "has-key":
        return 0 if load_key() else 1
    began = time.time()
    n, reason = transcribe(args.wav, args.srt, args.raw, args.diarize, args.speakers)
    if n is None:
        print(reason)
        return 3
    print(f"{n} lines in {time.time() - began:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
