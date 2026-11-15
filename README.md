# ⚡ LocalClip

> **Your private, on-device AI short-form video clipper.**  
> Paste a link or upload a file $\to$ transcribes on your machine with Whisper $\to$ AI picks high-retention viral moments $\to$ FFmpeg cuts 9:16 vertical clips with word-by-word animated captions and hooks.

---

## ✨ Features

- 🔒 **100% On-Device Transcription:** Runs locally using `faster-whisper` (CTranslate2) with GPU/CPU acceleration. No audio leaves your computer.
- 🤖 **Bring Any AI Key:** Plug in Gemini, OpenAI, Groq, DeepSeek, Anthropic, xAI, OpenRouter, or **100% free local Ollama**. If no AI key is provided, it automatically falls back to speech-density heuristics.
- 🎨 **Animated Karaoke Captions:** Dynamic word-by-word active bounce highlight (Alex Hormozi style) + on-screen hook banners burned directly via FFmpeg.
- 📐 **Smart Framing:** Choose between standard 9:16 vertical crop or **9:16 Blurred Background** (keeps 16:9 widescreen video centered without cutting off speakers).
- 📱 **Phone Mode (LAN Access):** Preview and download clips directly onto your phone over local Wi-Fi with one-time secure token authentication.
- ⚡ **Zero Framework Bloat:** Ultra-fast, single-process architecture with real-time Server-Sent Events (SSE) and HTTP 206 video streaming.

---

## 🚀 Quick Setup

### 1. Prerequisites
- **Python 3.10+**
- **FFmpeg** installed and added to your system `PATH`.

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/PiyushC2727/PiyushC2727-localclip.git
cd PiyushC2727-localclip

# Install dependencies (using uv or pip)
pip install -r requirements.txt
```

### 3. Configure AI (Optional)
Copy the example environment file:
```bash
cp .env.example .env
```
Open `.env` and set your preferred provider:
```ini
LC_PROVIDER=gemini
LC_MODEL=gemini-1.5-flash
LC_KEY=your_api_key_here
```
*(You can also paste your API key directly in the web UI at runtime—it is held in memory and never logged).*

---

## 🖥️ Usage

### Web Dashboard (Recommended)
On **Windows**, double-click `run_local.bat`, or in terminal:
```bash
python server.py
```
Automatically opens `http://127.0.0.1:8765`. Paste a YouTube link or upload a video, tweak parameters, and hit **Make Viral Clips**.

### Mobile Phone Access (Over Wi-Fi)
On **Windows**, double-click `run_lan.bat`, or in terminal:
```bash
python server.py --lan
```
It prints a secure LAN URL with a token:
```
http://192.168.1.50:8765/?token=secret_token
```
Open this link in your phone's browser on the same Wi-Fi network to generate, preview, and download clips directly to your camera roll.

### Command Line Interface (CLI)
```bash
# Clip a YouTube video with default settings
python agent.py "https://www.youtube.com/watch?v=..." -n 5 --min 20 --max 60

# Clip a local video with blurred background and cyan subtitles
python agent.py podcast.mp4 --framing blur --caption-color cyan -o output/

# No-captions export
python agent.py stream.mp4 --no-captions -o clips/
```

---

## 🤖 Supported AI Providers

| Provider | Default Models | Requires Key? |
| :--- | :--- | :---: |
| **Gemini** | `gemini-1.5-flash`, `gemini-2.0-flash` | Yes (Free tier available) |
| **Groq** | `llama-3.3-70b-versatile`, `llama-3.1-8b-instant` | Yes (Ultra fast) |
| **OpenAI** | `gpt-4o-mini`, `gpt-4o` | Yes |
| **DeepSeek** | `deepseek-chat` | Yes |
| **Anthropic**| `claude-3-5-haiku-20241022` | Yes |
| **Ollama** | `llama3.2`, `mistral` | **No** (100% offline & free) |
| **Speech Density** | None | **No** (Built-in offline fallback) |

---

## 🐳 Docker Deployment

To run in Docker (e.g. for Render, Railway, Fly.io, or your home server):
```bash
docker build -t localclip .
docker run -p 8765:8765 \
  -e LC_PROVIDER=gemini \
  -e LC_KEY=your_key \
  -e LC_TOKEN=your_secure_password \
  localclip
```

---

## 📄 License
MIT License. Free to use, modify, and build upon.
