# ripa Output Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ripa` write a per-recording folder with a combined MP3 + merged transcript at the top and per-track MP3s + raw transcripts in a `parts/` subdirectory, keeping all existing transcript processing.

**Architecture:** One script, `bin/extract_audio_stereo.sh`. Phase 1 becomes a single `ffmpeg` demux pass whose `filter_complex` fans each track into a whisper WAV (transient), a source-rate MP3 (deliverable), and an amix branch for the combined MP3. Phase 2 is the existing transcribe/weave/clean/markers/relabel logic retargeted to the new paths, additionally rendering each track's SRT to `parts/<track>.txt`.

**Tech Stack:** bash, ffmpeg (libavfilter `loudnorm`/`asplit`/`amix`, libmp3lame encoder), whisper-cli, the `casting_call` Python package (unchanged).

## Global Constraints

- Scope is `ripa` (`bin/extract_audio_stereo.sh`) only. Do NOT touch `ript`/`transcribe_batch_stereo.sh` or the docs.
- No Python package changes. `clean.py`, `markers.py`, `transcript.py`, `speakers.json` relabel run as-is, only on new paths.
- No new dependencies. `libmp3lame` is already in the brew ffmpeg build.
- Combined MP3: mono, mixes all present tracks (you + caller + markers-when-present).
- Deliverable MP3s keep source sample rate. you/caller get `loudnorm=I=-16:TP=-1.5:LRA=11`; markers MP3 is untouched.
- `parts/` transcripts are raw per-track output (pre-clean). Top-level `<name>.txt` is the processed one.
- Track 3 outputs (`markers.mp3`, `markers.txt`, the third amix input) appear only when `has_stream "$input_file" "a:2"`.
- Keep the `ripa` alias and script filename. No error-swallowing: if extraction fails, skip that recording with a clear message.
- No em dashes in any doc.

---

### Task 1: Per-recording output layout

**Files:**
- Modify: `bin/extract_audio_stereo.sh` (lines 83, 258-265, 269-270, 363; header line 23)

**Interfaces:**
- Produces (for later tasks): `$rec_dir="$INPUT_DIR/$filename"`, `$parts_dir="$rec_dir/parts"`, `$txt_out="$rec_dir/${filename}.txt"`, `$combined_mp3="$rec_dir/${filename}.mp3"`. Per-track paths: `$parts_dir/you.mp3`, `caller.mp3`, `markers.mp3`, `you.txt`, `caller.txt`, `markers.txt`. WAVs stay transient in `/tmp`.

- [ ] **Step 1: Retarget path variables in the per-recording loop**

Replace the `txt_out` line and add the new dir/path vars (current lines 258-265):

```bash
    filename=$(basename "$input_file"); filename="${filename%.*}"
    current=$((current + 1))
    rec_dir="$INPUT_DIR/$filename"
    parts_dir="$rec_dir/parts"
    txt_out="$rec_dir/${filename}.txt"
    combined_mp3="$rec_dir/${filename}.mp3"
    tmp_you="/tmp/whisper_${filename}_you.wav"
    tmp_caller="/tmp/whisper_${filename}_caller.wav"
    tmp_t3="/tmp/whisper_${filename}_t3.wav"
    tmp_srt_you="/tmp/whisper_${filename}_you.srt"
    tmp_srt_caller="/tmp/whisper_${filename}_caller.srt"
```

- [ ] **Step 2: Move the "already done" skip to the new transcript path and create the folders**

The skip check (current lines 269-271) already reads `$txt_out`, which now points at the new location, so it works unchanged. Immediately after the `has_stream a:1` guard (current line 276), create the output dirs:

```bash
    mkdir -p "$parts_dir"
```

Remove the now-unused top-level `mkdir -p "$INPUT_DIR/transcripts"` (current line 83).

- [ ] **Step 3: Update the final completion message**

Replace current line 363:

```bash
echo "Complete!  Output → $INPUT_DIR/<name>/ (combined mp3 + transcript, parts/ for tracks)"
```

- [ ] **Step 4: Update the header Output comment**

Replace current line 23 (`# Output: transcripts/<name>.txt`):

```bash
# Output: <name>/<name>.txt (merged) + <name>/<name>.mp3 (combined) + <name>/parts/<track>.{mp3,txt}
```

- [ ] **Step 5: Syntax check**

Run: `bash -n bin/extract_audio_stereo.sh`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add bin/extract_audio_stereo.sh
git commit -m "feat(ripa): per-recording output folder + parts/ layout"
```

---

### Task 2: Single-pass extraction of WAVs + MP3s + combined mix

**Files:**
- Modify: `bin/extract_audio_stereo.sh` (current extraction block, lines 278-290)

**Interfaces:**
- Consumes: `$tmp_you`, `$tmp_caller`, `$tmp_t3`, `$parts_dir`, `$combined_mp3` from Task 1.
- Produces: transient 16k mono WAVs at `$tmp_you`/`$tmp_caller`/`$tmp_t3`; deliverable MP3s at `$parts_dir/{you,caller,markers}.mp3`; combined mono MP3 at `$combined_mp3`.

- [ ] **Step 1: Replace the extraction block with a filter_complex single pass**

Replace current lines 278-290 with a conditionally-built graph. Two-track and three-track cases differ only in the Track 3 branch and the amix input count:

```bash
    # ONE demux pass. filter_complex fans each track into: a 16k mono WAV (whisper,
    # transient), a source-rate MP3 (deliverable), and an amix branch for the combined
    # mono MP3. you/caller get loudnorm; the markers track is left raw. The combined
    # mix is loudnorm'd once more so the summed file sits at a sane level.
    echo "  → Extracting tracks..."
    LN="loudnorm=I=-16:TP=-1.5:LRA=11"
    if has_stream "$input_file" "a:2"; then
        fc="[0:a:0]${LN},asplit=3[you_w][you_m][you_x];"
        fc+="[0:a:1]${LN},asplit=3[cal_w][cal_m][cal_x];"
        fc+="[0:a:2]asplit=3[t3_w][t3_m][t3_x];"
        fc+="[you_x][cal_x][t3_x]amix=inputs=3:normalize=1,${LN}[mix]"
        ffmpeg -i "$input_file" -filter_complex "$fc" \
            -map "[you_w]" -ar 16000 -ac 1 -c:a pcm_s16le "$tmp_you" \
            -map "[cal_w]" -ar 16000 -ac 1 -c:a pcm_s16le "$tmp_caller" \
            -map "[t3_w]"  -ar 16000 -ac 1 -c:a pcm_s16le "$tmp_t3" \
            -map "[you_m]" -c:a libmp3lame -q:a 2 "$parts_dir/you.mp3" \
            -map "[cal_m]" -c:a libmp3lame -q:a 2 "$parts_dir/caller.mp3" \
            -map "[t3_m]"  -c:a libmp3lame -q:a 4 "$parts_dir/markers.mp3" \
            -map "[mix]"   -ac 1 -c:a libmp3lame -q:a 2 "$combined_mp3" \
            -y -loglevel error
    else
        fc="[0:a:0]${LN},asplit=3[you_w][you_m][you_x];"
        fc+="[0:a:1]${LN},asplit=3[cal_w][cal_m][cal_x];"
        fc+="[you_x][cal_x]amix=inputs=2:normalize=1,${LN}[mix]"
        ffmpeg -i "$input_file" -filter_complex "$fc" \
            -map "[you_w]" -ar 16000 -ac 1 -c:a pcm_s16le "$tmp_you" \
            -map "[cal_w]" -ar 16000 -ac 1 -c:a pcm_s16le "$tmp_caller" \
            -map "[you_m]" -c:a libmp3lame -q:a 2 "$parts_dir/you.mp3" \
            -map "[cal_m]" -c:a libmp3lame -q:a 2 "$parts_dir/caller.mp3" \
            -map "[mix]"   -ac 1 -c:a libmp3lame -q:a 2 "$combined_mp3" \
            -y -loglevel error
    fi
    if [ $? -ne 0 ] || [ ! -s "$tmp_you" ] || [ ! -s "$tmp_caller" ]; then
        echo "  ✗ Extraction failed for $filename; skipping."; continue
    fi
```

Note: the markers branch is left raw (no `loudnorm`), matching the current script and the spec. The combined output forces `-ac 1` so it is mono regardless of source channel layouts.

- [ ] **Step 2: Isolated filtergraph verification (fast, no whisper)**

Build a short synthetic 3-track recording and run ONLY the extraction command shape against it to confirm the graph is valid and all seven outputs appear. See Task 4 Step 1 for the synthetic-file recipe; run the extraction, then:

Run: `ffprobe -v error -show_entries format=duration -of csv=p=0 /tmp/rc_you.mp3 && ls -la /tmp/rc_parts/`
Expected: `you.mp3`, `caller.mp3`, `markers.mp3` non-empty, combined mp3 non-empty, three WAVs non-empty.

- [ ] **Step 3: Syntax check**

Run: `bash -n bin/extract_audio_stereo.sh`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add bin/extract_audio_stereo.sh
git commit -m "feat(ripa): emit per-track + combined MP3s in the single demux pass"
```

---

### Task 3: Per-track transcripts into parts/

**Files:**
- Modify: `bin/extract_audio_stereo.sh` (after weave/clean, current lines 304-334, and cleanup line 359)

**Interfaces:**
- Consumes: `$tmp_srt_you`, `$tmp_srt_caller` (produced by `transcribe_track`), `$tmp_t3_txt`, `$parts_dir`, the `merge_srt` helper.
- Produces: `$parts_dir/you.txt`, `caller.txt`, and `markers.txt` (when Track 3 present).

- [ ] **Step 1: Render you/caller per-track transcripts to parts/ after weaving**

After the weave + `✓ Woven` line (current line 311), before the clean step, add:

```bash
    # Raw per-track transcripts (pre-clean) for reference in parts/.
    merge_srt "$tmp_srt_you"    "/dev/null" "$YOU_LABEL"    "-" "$parts_dir/you.txt"    >/dev/null
    merge_srt "$tmp_srt_caller" "/dev/null" "$CALLER_LABEL" "-" "$parts_dir/caller.txt" >/dev/null
```

- [ ] **Step 2: Keep the markers transcript in parts/ instead of deleting it**

In the Track 3 block, the marker WAV is already extracted in Task 2, so drop the WAV-extraction line if any remains and stop deleting `markers.mp3`/txt. Change the block (current lines 323-334) so it:
- does NOT re-extract `$tmp_t3` (already produced in Phase 1),
- writes the marker transcript to `$parts_dir/markers.txt`,
- removes only the transient SRT, keeping `$parts_dir/markers.mp3` and `$parts_dir/markers.txt`.

Replace the block body with:

```bash
    if has_stream "$input_file" "a:2"; then
        echo "  → Parsing Track 3 markers..."
        tmp_t3_srt="/tmp/whisper_${filename}_t3.srt"
        GGML_METAL_PATH_RESOURCES="$WHISPER_METAL_RESOURCES" \
        "$WHISPER_BIN" --model "$WHISPER_MODEL" -mc 0 "${VAD_FLAG[@]}" --output-srt \
            --output-file "/tmp/whisper_${filename}_t3" "$tmp_t3" 2>/dev/null \
            | whisper_progress "markers" "$(get_duration "$tmp_t3")"
        merge_srt "$tmp_t3_srt" "/dev/null" "T3" "-" "$parts_dir/markers.txt" >/dev/null
        ( cd "$REPO_DIR" && python3 -m casting_call.markers "$txt_out" "$parts_dir/markers.txt" | sed 's/^/  → /' )
        rm -f "$tmp_t3_srt"
    fi
```

Note: `casting_call.markers` reads the marker transcript and edits `$txt_out` in place; pointing it at `$parts_dir/markers.txt` (a kept file) is equivalent to the old temp file.

- [ ] **Step 3: Update end-of-loop cleanup to remove only transients**

Replace current line 359 so it deletes the WAVs and SRTs but never the kept MP3s/txt in `parts/`:

```bash
    rm -f "$tmp_you" "$tmp_caller" "$tmp_t3" "$tmp_srt_you" "$tmp_srt_caller"
```

- [ ] **Step 4: Syntax check**

Run: `bash -n bin/extract_audio_stereo.sh`
Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add bin/extract_audio_stereo.sh
git commit -m "feat(ripa): write raw per-track transcripts to parts/"
```

---

### Task 4: End-to-end verification on a synthetic 3-track recording

**Files:**
- No source changes. Verification only.

- [ ] **Step 1: Build a short synthetic 3-track .mkv**

Generate spoken audio with `say`, then mux three audio tracks plus a tiny video stream into an MKV (Track 3 carries one recognizable marker phrase):

```bash
WORK=/tmp/ripa_e2e; rm -rf "$WORK"; mkdir -p "$WORK/calls"
say -o "$WORK/you.aiff"    "Hi Ed, thanks for hopping on. Let us talk through the roadmap."
say -o "$WORK/caller.aiff" "Sounds good. I had a few questions about the timeline."
say -o "$WORK/mark.aiff"   "mark action"
ffmpeg -y -loglevel error \
  -f lavfi -i "color=c=black:s=320x240:d=6" \
  -i "$WORK/you.aiff" -i "$WORK/caller.aiff" -i "$WORK/mark.aiff" \
  -map 0:v -map 1:a -map 2:a -map 3:a \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
  "$WORK/calls/2026-07-28-ed-test.mkv"
ffprobe -v error -show_entries stream=index,codec_type -of csv=p=0 "$WORK/calls/2026-07-28-ed-test.mkv"
```

Expected: one video stream and three audio streams.

- [ ] **Step 2: Run ripa on the synthetic folder**

Run: `bash bin/extract_audio_stereo.sh /tmp/ripa_e2e/calls`
Expected: `→ Extracting tracks...` (single ffmpeg pass), You/Caller transcription bars, `✓ Woven`, `→ Parsing Track 3 markers...`, then `Complete!`.

- [ ] **Step 3: Assert the output tree**

Run:
```bash
find /tmp/ripa_e2e/calls/2026-07-28-ed-test -type f | sort
```
Expected exactly:
```
.../2026-07-28-ed-test/2026-07-28-ed-test.mp3
.../2026-07-28-ed-test/2026-07-28-ed-test.txt
.../2026-07-28-ed-test/parts/caller.mp3
.../2026-07-28-ed-test/parts/caller.txt
.../2026-07-28-ed-test/parts/markers.mp3
.../2026-07-28-ed-test/parts/markers.txt
.../2026-07-28-ed-test/parts/you.mp3
.../2026-07-28-ed-test/parts/you.txt
```

- [ ] **Step 4: Assert content sanity**

Run:
```bash
echo "== merged =="; cat /tmp/ripa_e2e/calls/2026-07-28-ed-test/2026-07-28-ed-test.txt
echo "== combined plays =="; ffprobe -v error -show_entries format=duration -of csv=p=0 /tmp/ripa_e2e/calls/2026-07-28-ed-test/2026-07-28-ed-test.mp3
```
Expected: merged transcript has `[You]` and `[Caller]` lines and a `[MARKER] action` line folded in; combined mp3 reports a duration.

- [ ] **Step 5: Assert re-run is a no-op**

Run: `bash bin/extract_audio_stereo.sh /tmp/ripa_e2e/calls`
Expected: `⏭  Transcript already exists, skipping`.

- [ ] **Step 6: Clean up the scratch folder**

Run: `rm -rf /tmp/ripa_e2e`

No commit (verification only). If any assertion fails, fix the script and re-run from Task 4 Step 2.
