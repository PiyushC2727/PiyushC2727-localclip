# localclip

Your own local clipping agent. Paste a link or a file → transcribes on your machine →
your AI of choice picks the best moments → ffmpeg cuts 9:16 clips with word-by-word captions and a hook.

## Setup
1. Install Python 3.10+ and `ffmpeg` (on PATH).
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and set `LC_PROVIDER`, `LC_MODEL`, `LC_KEY`.

## Use in your browser (easiest)
On Windows, simply double-click `run_local.bat`, or in terminal:

    python server.py

Opens http://127.0.0.1:8765 automatically. Paste a link, press **Make clips**, watch progress, preview and download clips.
You can pick the AI provider and paste a key right in the page (kept in memory only).

## Use from your phone
`127.0.0.1` on a phone means the phone itself, so run the server on your computer in phone mode:
On Windows, double-click `run_lan.bat`, or in terminal:

    python server.py --lan

It prints a link like `http://192.168.1.20:8765/?token=…`. Open it on your phone (same Wi-Fi as the computer).
The secret token is required; don't share the link. Allow Python through the firewall if asked (private networks only).

## Use from the command line
    python agent.py "https://youtube.com/watch?v=..." -n 5 --min 20 --max 60
    python agent.py my_stream.mp4 --no-captions -o clips/

Clips and `clips.json` (titles, hooks, timestamps) land in `output/`.
No AI key? It still works, picking the most talkative stretches.

## Providers
openai, anthropic, gemini, xai, groq, openrouter, ollama (local, no key), custom (any OpenAI-compatible URL).
To add one, add a line to `PROVIDERS` in `ai.py`.

## Ideas to extend
- Face-tracking crop instead of center crop
- Auto-post to YouTube Shorts / Reels
- Live-stream mode: record in segments and run the pipeline on each

## Host it online (get a link that works anywhere)
Needs a host that runs Docker (Render, Railway, Fly.io, a VPS). Push this folder to a GitHub repo, create a
"Web Service" from it, and set environment variables: `LC_TOKEN` (a long secret password),
`LC_PROVIDER`, `LC_MODEL`, `LC_KEY`. Your link is `https://YOUR-APP.onrender.com/?token=YOUR_LC_TOKEN`.
Use at least 2 GB RAM (speech model), and set `LC_WHISPER=base` if memory is tight.
Note: YouTube often blocks downloads from cloud servers; uploading/using your own files is more reliable there.

## If YouTube blocks the link
Cloud servers are often blocked by YouTube ("Sign in to confirm you're not a bot"). Use the **upload a video file** picker on the page instead. Limit: `LC_MAX_MB` (default 500).
Advanced: set `LC_COOKIES` to a Netscape-format cookies.txt (use a throwaway account; cookies expire and this may still fail).
