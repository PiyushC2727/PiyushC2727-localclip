#!/usr/bin/env python3
"""Local web UI server for localclip.
Run: python server.py  →  http://127.0.0.1:8765
Supports local execution, phone access via LAN mode, SSE real-time streaming,
range requests for video playback, and persistent settings.
"""
import hmac
import json
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import agent

agent.load_env()
HERE = Path(__file__).parent.resolve()
JOBS_DIR = HERE / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
UPLOADS = JOBS_DIR / "uploads"
UPLOADS.mkdir(exist_ok=True)

LAN = "--lan" in sys.argv or os.getenv("LC_LAN") == "1"
HOST = "0.0.0.0" if LAN else "127.0.0.1"
PORT = int(os.getenv("LC_PORT") or os.getenv("PORT") or 8765)
TOKEN = (os.getenv("LC_TOKEN") or secrets.token_urlsafe(9)) if LAN else None
MAX_MB = int(os.getenv("LC_MAX_MB", "500"))

# In-memory jobs state and SSE event listeners
# jid -> {"status", "stage", "progress", "message", "log", "clips", "listeners"}
JOBS = {}
JOBS_LOCK = threading.Lock()

if os.getenv("LC_COOKIES"):
    _cf = Path(tempfile.gettempdir()) / "lc_cookies.txt"
    _cf.write_text(os.environ["LC_COOKIES"].replace("\\n", "\n"), encoding="utf-8")
    os.environ["LC_COOKIES_FILE"] = str(_cf)


def broadcast_event(jid, event_type, data):
    """Notify all connected SSE clients for a job."""
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if not job:
            return
        listeners = list(job.get("listeners", []))

    payload = json.dumps({"type": event_type, **data})
    for q in listeners:
        try:
            q.put_nowait(payload)
        except Exception:
            pass


def run_job(jid, body):
    job = JOBS[jid]
    out = JOBS_DIR / jid
    out.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ, PYTHONUNBUFFERED="1")
    for field, var in (
        ("provider", "LC_PROVIDER"),
        ("model", "LC_MODEL"),
        ("key", "LC_KEY"),
        ("base_url", "LC_BASE_URL"),
        ("whisper", "LC_WHISPER"),
    ):
        val = str(body.get(field, "")).strip()
        if val:
            env[var] = val

    # Build agent command
    cmd = [
        sys.executable,
        str(HERE / "agent.py"),
        body["source"],
        "-o", str(out),
        "-n", str(int(body.get("n", 5))),
        "--min", str(int(body.get("min", 20))),
        "--max", str(int(body.get("max", 60))),
        "--framing", str(body.get("framing", "crop")),
        "--caption-style", str(body.get("caption_style", "karaoke")),
        "--caption-color", str(body.get("caption_color", "gold")),
    ]

    if body.get("whisper"):
        cmd.extend(["--whisper", str(body["whisper"])])

    if body.get("scan_mins") is not None:
        cmd.extend(["--scan-mins", str(int(body["scan_mins"]))])

    if not body.get("captions", True):
        cmd.append("--no-captions")

    stage_re = re.compile(r"^\[([A-Z0-9_-]+)\]\s*(\d+(?:\.\d+)?)%\s*-\s*(.*)$")

    try:
        p = subprocess.Popen(
            cmd,
            env=env,
            cwd=HERE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        for line in p.stdout:
            line_str = line.rstrip()
            if not line_str:
                continue
            job["log"].append(line_str)

            # Check if this line is a structured progress marker
            m = stage_re.match(line_str)
            if m:
                stg = m.group(1).lower()
                pct = float(m.group(2))
                msg = m.group(3)
                job["stage"] = stg
                job["progress"] = pct
                job["message"] = msg
                broadcast_event(jid, "progress", {
                    "stage": stg,
                    "progress": pct,
                    "message": msg,
                    "line": line_str,
                })
            else:
                broadcast_event(jid, "log", {"line": line_str})

        p.wait()
        meta = out / "clips.json"
        if p.returncode == 0 and meta.exists():
            clips = json.loads(meta.read_text(encoding="utf-8"))
            job["clips"] = clips
            job["status"] = "done"
            job["stage"] = "completed"
            job["progress"] = 100
            job["message"] = f"Finished! {len(clips)} clips created."
            broadcast_event(jid, "done", {"clips": clips, "message": job["message"]})
        else:
            job["status"] = "error"
            job["message"] = "Process failed. Check console log."
            broadcast_event(jid, "error", {"message": job["message"]})
    except Exception as e:
        job["log"].append(f"Error executing agent: {e}")
        job["status"] = "error"
        job["message"] = str(e)
        broadcast_event(jid, "error", {"message": str(e)})


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        # Silence default request logging to avoid terminal clutter
        pass

    def _authed(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "lc_token" in c and hmac.compare_digest(c["lc_token"].value, TOKEN)

    def _host_ok(self):
        if LAN:
            return self._authed()
        host = self.headers.get("Host", "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "0.0.0.0")

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.end_headers()

    def do_GET(self):
        if LAN:
            tok = parse_qs(urlparse(self.path).query).get("token", [""])[0]
            if tok and hmac.compare_digest(tok, TOKEN):
                self.send_response(302)
                self.send_header("Set-Cookie", f"lc_token={TOKEN}; HttpOnly; SameSite=Strict; Path=/")
                self.send_header("Location", "/")
                self.end_headers()
                return

        if not self._host_ok():
            return self._json({"error": "Unauthorized. Open the full link with ?token= displayed in the terminal."}, 403)

        path = self.path.split("?")[0]

        # 1. Frontend Homepage
        if path == "/":
            data = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # 2. Config Endpoint
        elif path == "/api/config":
            import ai
            recent = []
            try:
                for p in JOBS_DIR.iterdir():
                    if p.is_dir() and (p / "clips.json").is_file():
                        recent.append({
                            "id": p.name,
                            "time": p.stat().st_mtime,
                        })
                recent.sort(key=lambda x: x["time"], reverse=True)
            except Exception:
                pass

            self._json({
                "provider": os.getenv("LC_PROVIDER", "gemini"),
                "model": os.getenv("LC_MODEL", "gemini-1.5-flash"),
                "has_key": bool(os.getenv("LC_KEY")),
                "whisper": os.getenv("LC_WHISPER", "base"),
                "framing": os.getenv("LC_FRAMING", "crop"),
                "providers": list(ai.PROVIDERS.keys()),
                "default_models": {p: ai.get_default_models(p) for p in ai.PROVIDERS},
                "max_mb": MAX_MB,
                "recent_jobs": [r["id"] for r in recent[:5]],
            })

        # 3. Server-Sent Events (SSE) for Real-Time Streaming
        elif (m := re.fullmatch(r"/api/events/([a-zA-Z0-9_-]+)", path)):
            jid = m.group(1)
            job = JOBS.get(jid)
            if not job:
                return self._json({"error": "Job not found"}, 404)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            q = queue.Queue()
            with JOBS_LOCK:
                job["listeners"].append(q)

            # Send initial state snapshot
            initial_data = json.dumps({
                "type": "init",
                "status": job["status"],
                "stage": job.get("stage", "init"),
                "progress": job.get("progress", 0),
                "message": job.get("message", ""),
                "log": job.get("log", []),
                "clips": job.get("clips", []),
            })
            self.wfile.write(f"data: {initial_data}\n\n".encode("utf-8"))
            self.wfile.flush()

            try:
                while True:
                    try:
                        msg = q.get(timeout=25.0)
                        self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        if job["status"] in ("done", "error") and q.empty():
                            break
                    except queue.Empty:
                        # Keep-alive heartbeat
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with JOBS_LOCK:
                    if q in job["listeners"]:
                        job["listeners"].remove(q)

        # 4. Job Status Fallback Polling / Retrieval
        elif (m := re.fullmatch(r"/api/job/([a-zA-Z0-9_-]+)", path)):
            jid = m.group(1)
            j = JOBS.get(jid)
            if j:
                # Return state without non-serializable listeners
                safe_job = {k: v for k, v in j.items() if k != "listeners"}
                self._json(safe_job, 200)
            elif (JOBS_DIR / jid / "clips.json").is_file():
                try:
                    clips = json.loads((JOBS_DIR / jid / "clips.json").read_text(encoding="utf-8"))
                    safe_job = {
                        "id": jid,
                        "status": "done",
                        "stage": "completed",
                        "progress": 100,
                        "message": "Completed",
                        "log": ["Loaded from disk"],
                        "clips": clips,
                    }
                    self._json(safe_job, 200)
                except Exception as e:
                    self._json({"error": f"Failed reading job clips: {e}"}, 500)
            else:
                self._json({"error": "Job not found"}, 404)

        # 5. Serve Video Files with Partial Content (HTTP 206)
        elif (m := re.fullmatch(r"/files/([a-zA-Z0-9_-]+)/([\w.-]+\.mp4)", path)):
            self._serve_video(JOBS_DIR / m.group(1) / m.group(2))

        else:
            self._json({"error": "Endpoint not found"}, 404)

    def _serve_video(self, f: Path):
        if not f.is_file():
            return self._json({"error": "File not found"}, 404)

        size = f.stat().st_size
        start, end = 0, size - 1
        code = 200

        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = int(rng.group(2)) if rng.group(2) else end
            else:
                start = max(0, size - int(rng.group(2)))
            end = min(end, size - 1)
            code = 206

        self.send_response(code)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with open(f, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(128 * 1024, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                left -= len(chunk)

    def _handle_upload(self):
        size = int(self.headers.get("Content-Length", 0))
        if size <= 0 or size > MAX_MB * 1024 * 1024:
            return self._json({"error": f"File exceeds maximum upload limit of {MAX_MB} MB."}, 413)

        raw_filename = self.headers.get("X-Filename", "video.mp4")
        ext = os.path.splitext(raw_filename)[1].lower()
        if ext not in (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".wav", ".m4a"):
            return self._json({"error": f"Unsupported file type: {ext}"}, 400)

        dest = UPLOADS / f"{uuid.uuid4().hex[:12]}{ext}"
        left = size
        with open(dest, "wb") as fh:
            while left > 0:
                chunk = self.rfile.read(min(1024 * 1024, left))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)

        if left > 0:
            dest.unlink(missing_ok=True)
            return self._json({"error": "Upload stream was interrupted."}, 400)

        self._json({"path": str(dest)})

    def do_POST(self):
        if not self._host_ok():
            return self._json({"error": "Forbidden"}, 403)

        path = self.path.split("?")[0]

        # 1. File Upload
        if path == "/api/upload":
            return self._handle_upload()

        # 2. Persist Configuration to .env
        elif path == "/api/save_env":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                env_path = HERE / ".env"
                existing = {}
                if env_path.exists():
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            existing[k.strip()] = v.strip()

                if "provider" in body:
                    existing["LC_PROVIDER"] = body["provider"]
                    os.environ["LC_PROVIDER"] = body["provider"]
                if "model" in body and body["model"]:
                    existing["LC_MODEL"] = body["model"]
                    os.environ["LC_MODEL"] = body["model"]
                if "key" in body and body["key"]:
                    existing["LC_KEY"] = body["key"]
                    os.environ["LC_KEY"] = body["key"]
                if "whisper" in body:
                    existing["LC_WHISPER"] = body["whisper"]
                    os.environ["LC_WHISPER"] = body["whisper"]

                content = "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n"
                env_path.write_text(content, encoding="utf-8")
                return self._json({"status": "saved"})
            except Exception as e:
                return self._json({"error": str(e)}, 500)

        # 3. Start Clipping Pipeline
        elif path == "/api/start":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                src = str(body.get("source", "")).strip()

                if not src or src.startswith("-"):
                    return self._json({"error": "Please provide a valid YouTube/video link or choose a file."}, 400)

                if LAN and not src.lower().startswith(("http://", "https://")):
                    if not Path(src).resolve().is_relative_to(UPLOADS.resolve()) or not os.path.isfile(src):
                        return self._json({"error": "In phone mode, upload files through the web interface."}, 400)

                with JOBS_LOCK:
                    if any(j.get("status") == "running" for j in JOBS.values()):
                        return self._json({"error": "A clipping job is already running. Please wait for it to complete."}, 409)

                    jid = uuid.uuid4().hex[:12]
                    JOBS[jid] = {
                        "id": jid,
                        "status": "running",
                        "stage": "init",
                        "progress": 0,
                        "message": "Initializing job…",
                        "log": [],
                        "clips": [],
                        "listeners": [],
                    }

                body["source"] = src
                threading.Thread(target=run_job, args=(jid, body), daemon=True).start()
                self._json({"id": jid})
            except Exception as e:
                self._json({"error": f"Failed to start job: {e}"}, 400)
        else:
            self._json({"error": "Not found"}, 404)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)
    if LAN:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                sk.connect(("8.8.8.8", 80))
                ip = sk.getsockname()[0]
        except OSError:
            ip = "YOUR-COMPUTER-IP"
        print(f"\n========================================================")
        print(f"  LocalClip Phone Mode Active")
        print(f"  Open on your mobile phone (same Wi-Fi network):")
        print(f"  http://{ip}:{PORT}/?token={TOKEN}")
        print(f"========================================================\n")
    else:
        url = f"http://{HOST}:{PORT}"
        print(f"LocalClip running locally at {url}  (Ctrl+C to stop)")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down LocalClip server.")
