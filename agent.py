#!/usr/bin/env python3
"""localclip: turn a long video into captioned 9:16 clips.
Runs locally; bring any AI key or use speech density fallback.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def load_env(path=None):
    if path is None:
        path = HERE / ".env"
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


# 1. Video acquisition -----------------------------------------------------
def fetch(source: str, work: Path, progress_cb=None) -> Path:
    """Download video from URL or return path if local file."""
    # 1. Clean source string & check for local/uploaded file
    src = str(source).strip().strip('"').strip("'")
    if os.path.exists(src) or Path(src).is_file():
        src_path = Path(src).resolve()
        if progress_cb:
            progress_cb("download", 100, f"Using local file: {src_path.name}")
        print(f"Using local file: {src_path.name}")
        return src_path

    try:
        resolved = Path(src).resolve()
        if resolved.is_file():
            if progress_cb:
                progress_cb("download", 100, f"Using local file: {resolved.name}")
            print(f"Using local file: {resolved.name}")
            return resolved
    except Exception:
        pass

    out = work / "source.mp4"
    if out.exists() and out.stat().st_size > 1024:
        print(f"Using already cached video: {out.name}")
        if progress_cb:
            progress_cb("download", 100, "Using cached video.")
        return out

    print("Downloading video stream with yt-dlp…")
    if progress_cb:
        progress_cb("download", 5, "Connecting to video source…")

    # Use sys.executable -m yt_dlp to avoid WinError 2 when yt-dlp.exe is not in system PATH
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--geo-bypass",
        "--no-check-certificates",
        "--extractor-args", "youtube:player_client=android,web;formats=missing_pot",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/best[height<=1080]/best",
        "--merge-output-format", "mp4",
        "--newline",
        "-o", str(out),
        src,
    ]

    cf = os.getenv("LC_COOKIES_FILE")
    cb = os.getenv("LC_COOKIES_BROWSER")
    if cf and os.path.exists(cf):
        cmd[1:1] = ["--cookies", cf]
    elif cb:
        cmd[1:1] = ["--cookies-from-browser", cb]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    percent_re = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
    for line in proc.stdout:
        line_clean = line.strip()
        if not line_clean:
            continue
        print(line_clean)
        m = percent_re.search(line_clean)
        if m and progress_cb:
            pct = min(99, float(m.group(1)))
            progress_cb("download", pct, f"Downloading video: {pct:.1f}%")

    proc.wait()
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(
            "Download failed. Sites like YouTube often block servers or require verification.\n"
            "Try uploading the video file directly or verify your cookies/network."
        )

    if progress_cb:
        progress_cb("download", 100, "Download complete.")
    return out


# 2. Local transcription ---------------------------------------------------
def get_whisper_device():
    """Detect available accelerator for faster-whisper."""
    env_dev = os.getenv("LC_DEVICE")
    if env_dev:
        return env_dev.lower()
    try:
        import ctranslate2
        if "cuda" in ctranslate2.get_supported_devices():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def transcribe(video: Path, work: Path, model_size: str, scan_mins: int = 0, progress_cb=None):
    cache = work / f"transcript{f'_first{scan_mins}m' if scan_mins > 0 else ''}.json"
    if cache.exists():
        if progress_cb:
            progress_cb("transcribe", 100, "Using cached transcript.")
        return json.loads(cache.read_text(encoding="utf-8"))

    from faster_whisper import WhisperModel

    device = get_whisper_device()
    compute_type = "float16" if device == "cuda" else "int8"
    threads = os.cpu_count() or 4
    scan_label = f" (first {scan_mins}m)" if scan_mins > 0 else ""
    print(f"Transcribing locally (whisper {model_size} on {device.upper()}/{compute_type}, {threads} threads{scan_label})…")
    if progress_cb:
        progress_cb("transcribe", 10, f"Loading Whisper ({model_size} on {device.upper()}{scan_label})…")

    # Extract 16kHz mono audio first with FFmpeg for 3x-5x faster Whisper ingestion
    audio_wav = work / f"audio_16k{f'_first{scan_mins}m' if scan_mins > 0 else ''}.wav"
    audio_target = str(video)
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    try:
        if not audio_wav.exists() or audio_wav.stat().st_size == 0:
            if progress_cb:
                progress_cb("transcribe", 15, f"Extracting audio stream{scan_label}...")
            limit_args = ["-t", str(scan_mins * 60)] if scan_mins > 0 else []
            subprocess.run([
                ffmpeg_bin, "-y", "-loglevel", "error",
                "-i", str(video),
                *limit_args,
                "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le",
                str(audio_wav)
            ], check=True)
        if audio_wav.exists() and audio_wav.stat().st_size > 0:
            audio_target = str(audio_wav)
    except Exception as e:
        print(f"Audio extraction fallback: {e}")
        audio_target = str(video)

    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        cpu_threads=threads,
        num_workers=max(1, threads // 2)
    )

    if progress_cb:
        progress_cb("transcribe", 30, f"Transcribing audio with {threads} CPU threads…")

    segs, info = model.transcribe(
        audio_target,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )
    duration = getattr(info, "duration", 0) or 1.0

    data = []
    for s in segs:
        data.append({
            "start": s.start,
            "end": s.end,
            "text": s.text.strip(),
            "words": [
                {"w": w.word.strip(), "start": w.start, "end": w.end}
                for w in (s.words or [])
                if w.word.strip()
            ],
        })
        if progress_cb and duration > 0:
            pct = min(95, 35 + int((s.end / duration) * 60))
            progress_cb("transcribe", pct, f"Transcribing audio: {int(s.end)}s / {int(duration)}s")

    cache.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if progress_cb:
        progress_cb("transcribe", 100, f"Transcription finished ({len(data)} segments).")
    return data


# 3. Moment selection & AI scoring -----------------------------------------
SYSTEM = (
    "You are a master short-form viral video editor (TikTok, YouTube Shorts, Reels).\n"
    "From the provided timestamped transcript, identify the most compelling, high-retention segments.\n"
    "Look for: high emotion, surprising insights, punchy humor, strong debate, or standalone value.\n"
    "Rules:\n"
    "1. Each clip MUST begin at a natural conversational opening and end cleanly on a completed thought.\n"
    "2. 'hook': 3-7 word punchy title banner for on-screen overlay (e.g. 'THE TRUTH ABOUT AI', 'HOW I LOST $10,000').\n"
    "3. 'score': Viral rating from 1 to 10.\n"
    "4. 'title': Short descriptive title for the clip.\n"
    "5. Output valid JSON only with structure:\n"
    '{"clips":[{"start":<seconds>,"end":<seconds>,"score":<1-10>,"title":"<short>","hook":"<punchy hook>","reason":"<why viral>"}]}'
)


def chunks(segs, max_chars=14000):
    buf, size = [], 0
    for s in segs:
        line = f"[{int(s['start'])}] {s['text']}"
        if size + len(line) > max_chars and buf:
            yield buf
            buf, size = [], 0
        buf.append(line)
        size += len(line)
    if buf:
        yield buf


def pick_with_ai(segs, n, lo, hi, progress_cb=None):
    import ai
    all_chunks = list(chunks(segs))
    found = []
    for i, c in enumerate(all_chunks, 1):
        msg = f"Analyzing transcript with AI (part {i}/{len(all_chunks)})…"
        print(msg)
        if progress_cb:
            pct = 10 + int((i / len(all_chunks)) * 80)
            progress_cb("ai", pct, msg)

        user = (
            f"Select up to {n} viral clips between {lo} and {hi} seconds in duration.\n"
            f"Timestamps are in seconds.\n\n" + "\n".join(c)
        )
        try:
            res = ai.chat_json(SYSTEM, user)
            found.extend(res.get("clips", []))
        except Exception as e:
            print(f"  ! AI chunk {i} failed: {e}")

    return found


def pick_by_density(segs, n, lo, hi):
    """Fallback when no AI key is provided: speech density heuristic."""
    if not segs:
        return []
    end = segs[-1]["end"]
    win = (lo + hi) // 2
    found = []
    t = 0.0
    while t + win <= end:
        words = sum(len(s.get("words", [])) for s in segs if t <= s["start"] < t + win)
        found.append({
            "start": t,
            "end": t + win,
            "score": min(9.5, round(5.0 + (words / max(win, 1)) * 1.5, 1)),
            "title": f"Clip at {int(t)}s",
            "hook": "WATCH THIS MOMENT",
            "reason": "High speech density segment",
        })
        t += max(10, win // 2)
    return found


def select(found, n, lo, hi, total):
    clean = []
    for c in found:
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        e = min(e, s + hi, total)
        if e - s < lo * 0.7:
            continue
        clean.append({**c, "start": max(0.0, s), "end": e})

    clean.sort(key=lambda c: -float(c.get("score", 0)))
    chosen = []
    for c in clean:
        # Avoid overlapping clips
        if all(c["end"] <= k["start"] or c["start"] >= k["end"] for k in chosen):
            chosen.append(c)
        if len(chosen) == n:
            break
    return sorted(chosen, key=lambda c: c["start"])


# 4. ASS Karaoke Subtitle Synthesis & Video Rendering -----------------------
def ts(t):
    t = max(0.0, float(t))
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def get_ass_header(res_x=1080, res_y=1920, highlight_bgr="&H00D7FF&"):
    """
    Generate ASS header with styles.
    highlight_bgr in &HBBGGRR& format:
    &H00D7FF& = Gold/Vibrant Yellow
    &H00FFFF& = Cyan
    &H0055FF& = Bright Orange
    &H55FF55& = Neon Green
    """
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Cap,Montserrat,82,&H00FFFFFF,{highlight_bgr},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,2,2,60,60,420,1
Style: Hook,Montserrat,68,&H00000000,&H00FFFFFF,&H00FFFFFF,{highlight_bgr},1,0,0,0,100,100,0,0,1,0,0,8,60,60,260,1
Style: HookText,Montserrat,68,{highlight_bgr},&H00000000,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,7,0,8,60,60,260,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def make_karaoke_ass(words, start, end, hook, path, style="karaoke", color="gold"):
    """
    Generates word-by-word active-highlight ASS subtitles.
    Words in the active 2-3 word window are displayed;
    the currently spoken word highlights in the vibrant accent color with a subtle bounce.
    """
    color_map = {
        "gold": "&H00D7FF&",      # Vibrant Gold / Yellow
        "cyan": "&H00FFFF&",      # Electric Cyan
        "green": "&H00FF55&",     # Neon Lime
        "magenta": "&H00D020FF&",  # Hot Magenta
        "white": "&H00FFFFFF&",   # Classic White
    }
    highlight_bgr = color_map.get(color.lower(), "&H00D7FF&")

    lines = [get_ass_header(1080, 1920, highlight_bgr)]

    # 1. On-screen attention hook for first 3.5 seconds
    if hook:
        hook_clean = hook.strip().upper()
        hook_end = min(3.5, end - start)
        lines.append(f"Dialogue: 0,{ts(0)},{ts(hook_end)},HookText,,0,0,0,,★ {hook_clean} ★\n")

    # 2. Filter words within this clip's time range
    ws = [w for w in words if start <= w["start"] < end and w.get("w")]
    if not ws:
        Path(path).write_text("".join(lines), encoding="utf-8")
        return

    # Group words into short chunks of 2-3 words for high short-form readability
    chunk_size = 3
    for i in range(0, len(ws), chunk_size):
        group = ws[i:i + chunk_size]
        group_start = group[0]["start"] - start
        group_end = (ws[i + chunk_size]["start"] - start) if (i + chunk_size < len(ws)) else (group[-1]["end"] - start + 0.3)

        if style == "classic":
            # Clean bold text without karaoke highlighting
            group_txt = " ".join(w["w"] for w in group).upper()
            lines.append(f"Dialogue: 0,{ts(group_start)},{ts(group_end)},Cap,,0,0,0,,{group_txt}\n")
        else:
            # Active word karaoke bounce
            for active_idx, active_word in enumerate(group):
                w_start = max(0.0, active_word["start"] - start)
                w_end = (group[active_idx + 1]["start"] - start) if (active_idx + 1 < len(group)) else group_end
                if w_end <= w_start:
                    w_end = w_start + 0.25

                # Build line where the active word has override tags
                parts = []
                for j, w in enumerate(group):
                    w_text = w["w"].upper()
                    if j == active_idx:
                        # Highlight active word with color override & slight size pop
                        parts.append(f"{{\\c{highlight_bgr}\\fscx106\\fscy106}}{w_text}{{\\r\\c&H00FFFFFF&}}")
                    else:
                        parts.append(w_text)

                line_content = " ".join(parts)
                lines.append(f"Dialogue: 0,{ts(w_start)},{ts(w_end)},Cap,,0,0,0,,{line_content}\n")

    Path(path).write_text("".join(lines), encoding="utf-8")


def render(video: Path, clip, words, out_dir: Path, idx: int,
           captions: bool = True, framing: str = "crop",
           caption_style: str = "karaoke", caption_color: str = "gold"):
    """
    Renders clip to 1080x1920 MP4 using FFmpeg.
    Framing:
      'crop' = 9:16 center crop
      'blur' = 9:16 blurred background with fitted video in center (no cutoff)
    """
    start, end = float(clip["start"]), float(clip["end"])
    name = f"clip{idx:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    ass_filename = f"{name}.ass"
    ass_path = out_dir / ass_filename

    # Build video filtergraph
    if framing == "blur":
        # Professional blurred background: scales video to fit 1080 width,
        # behind a zoomed, blurred 1080x1920 background
        vf = (
            "split[v1][v2];"
            "[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
            "[v2]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        # Standard 9:16 vertical crop
        vf = "crop='min(iw,ih*9/16)':ih,scale=1080:1920"

    if captions and words:
        make_karaoke_ass(
            words, start, end, clip.get("hook", ""),
            ass_path, style=caption_style, color=caption_color
        )
        # Use relative filename because ffmpeg runs with cwd=out_dir
        vf += f",ass={ass_filename}"

    out_file = out_dir / f"{name}.mp4"

    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg_bin, "-y", "-loglevel", "error",
        "-threads", str(os.cpu_count() or 4),
        "-ss", f"{start:.2f}",
        "-i", str(video),
        "-t", f"{end - start:.2f}",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        f"{name}.mp4",
    ]

    run(cmd, cwd=out_dir)
    return out_file


# 5. CLI Entrypoint --------------------------------------------------------
def main():
    load_env()
    ap = argparse.ArgumentParser(description="Local AI Video Clipper with Animated Subtitles")
    ap.add_argument("source", help="Video URL (YouTube/Twitch/…) or local video path")
    ap.add_argument("-n", "--clips", type=int, default=5, help="Number of clips to generate")
    ap.add_argument("--min", type=int, default=20, help="Min clip seconds")
    ap.add_argument("--max", type=int, default=60, help="Max clip seconds")
    ap.add_argument("--whisper", default=os.getenv("LC_WHISPER", "base"), help="Whisper model size")
    ap.add_argument("--scan-mins", type=int, default=0, help="Scan only first N minutes of video (0 = all)")
    ap.add_argument("--framing", choices=["crop", "blur"], default=os.getenv("LC_FRAMING", "crop"),
                    help="Framing: 'crop' (center crop) or 'blur' (blurred background)")
    ap.add_argument("--caption-style", choices=["karaoke", "classic"], default="karaoke",
                    help="Caption style: 'karaoke' (active word highlight) or 'classic'")
    ap.add_argument("--caption-color", default="gold", help="Caption highlight color (gold, cyan, green, magenta)")
    ap.add_argument("--no-captions", action="store_true", help="Disable burned-in subtitles")
    ap.add_argument("-o", "--out", default="output", help="Output directory")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    work = out / "_work"
    work.mkdir(parents=True, exist_ok=True)

    def progress_cli(stage, pct, msg):
        print(f"[{stage.upper()}] {pct}% - {msg}")

    video = fetch(args.source, work, progress_cb=progress_cli)
    segs = transcribe(video, work, args.whisper, scan_mins=args.scan_mins, progress_cb=progress_cli)
    if not segs:
        sys.exit("No speech detected in video.")

    words = [dict(w) for s in segs for w in s.get("words", [])]

    import ai
    if ai.configured():
        provider = os.getenv("LC_PROVIDER", "gemini")
        print(f"Selecting moments with AI ({provider})…")
        found = pick_with_ai(segs, args.clips, args.min, args.max, progress_cb=progress_cli)
    else:
        print("No AI key configured (see .env): falling back to speech density analysis.")
        found = []

    chosen = select(found, args.clips, args.min, args.max, segs[-1]["end"]) if found else []
    if not chosen:
        chosen = select(pick_by_density(segs, args.clips, args.min, args.max),
                        args.clips, args.min, args.max, segs[-1]["end"])

    if not chosen:
        sys.exit("No valid clip intervals could be selected.")

    meta = []
    total = len(chosen)
    for i, c in enumerate(chosen, 1):
        title = c.get("title", f"Clip {i}")
        duration = c["end"] - c["start"]
        print(f"\nRendering clip {i}/{total}: '{title}' ({c['start']:.0f}s - {c['end']:.0f}s, {duration:.1f}s)")
        clip_file = render(
            video, c, words, out, i,
            captions=not args.no_captions,
            framing=args.framing,
            caption_style=args.caption_style,
            caption_color=args.caption_color,
        )
        meta.append({**c, "file": clip_file.name})

    (out / "clips.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nAll {len(meta)} clips generated successfully! Output: {out}")


if __name__ == "__main__":
    main()
