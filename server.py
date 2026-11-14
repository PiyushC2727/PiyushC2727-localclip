#!/usr/bin/env python3
"""Local web UI for localclip. Run: python server.py  →  http://127.0.0.1:8765
Binds to this computer only. Keys typed in the page are passed to the job in memory and never saved."""
import hmac, json, os, re, secrets, socket, subprocess, sys, threading, uuid, webbrowser
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agent

agent.load_env()
HERE = Path(__file__).parent
JOBS_DIR = HERE / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
LAN = "--lan" in sys.argv or os.getenv("LC_LAN") == "1"  # let your phone reach it over Wi-Fi
HOST, PORT = ("0.0.0.0" if LAN else "127.0.0.1"), int(os.getenv("LC_PORT") or os.getenv("PORT") or 8765)
TOKEN = (os.getenv("LC_TOKEN") or secrets.token_urlsafe(9)) if LAN else None  # required in LAN mode
JOBS = {}  # id -> {"status", "log", "clips"}


def run_job(jid, body):
    job = JOBS[jid]
    out = JOBS_DIR / jid
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    for field, var in (("provider", "LC_PROVIDER"), ("model", "LC_MODEL"),
                       ("key", "LC_KEY"), ("base_url", "LC_BASE_URL")):
        if str(body.get(field, "")).strip():
            env[var] = str(body[field]).strip()
    cmd = [sys.executable, str(HERE / "agent.py"), body["source"], "-o", str(out),
           "-n", str(int(body.get("n", 5))), "--min", str(int(body.get("min", 20))),
           "--max", str(int(body.get("max", 60)))]
    if not body.get("captions", True):
        cmd.append("--no-captions")
    try:
        p = subprocess.Popen(cmd, env=env, cwd=HERE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            job["log"].append(line.rstrip())
        p.wait()
        meta = out / "clips.json"
        if p.returncode == 0 and meta.exists():
            job["clips"] = json.loads(meta.read_text())
            job["status"] = "done"
        else:
            job["status"] = "error"
    except Exception as e:
        job["log"].append(f"Error: {e}")
        job["status"] = "error"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authed(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "lc_token" in c and hmac.compare_digest(c["lc_token"].value, TOKEN)

    def _host_ok(self):
        if LAN:  # phone mode: the secret token is the protection
            return self._authed()
        # computer-only mode: blocks DNS-rebinding style requests
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
            return self._json({"error": "Open the full link (with ?token=…) shown in the computer's terminal."}, 403)
        path = self.path.split("?")[0]
        if path == "/":
            data = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/api/config":
            self._json({"provider": os.getenv("LC_PROVIDER", ""), "model": os.getenv("LC_MODEL", ""),
                        "has_key": bool(os.getenv("LC_KEY")),
                        "providers": list(__import__("ai").PROVIDERS)})
        elif (m := re.fullmatch(r"/api/job/([0-9a-f]{12})", path)):
            j = JOBS.get(m.group(1))
            self._json(j or {"error": "not found"}, 200 if j else 404)
        elif (m := re.fullmatch(r"/files/([0-9a-f]{12})/([\w.-]+\.mp4)", path)):
            self._serve(JOBS_DIR / m.group(1) / m.group(2))
        else:
            self._json({"error": "not found"}, 404)

    def _serve(self, f: Path):
        if not f.is_file():
            return self._json({"error": "not found"}, 404)
        size, start, end, code = f.stat().st_size, 0, f.stat().st_size - 1, 200
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = int(rng.group(2)) if rng.group(2) else end
            else:
                start = max(0, size - int(rng.group(2)))
            end, code = min(end, size - 1), 206
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
                chunk = fh.read(min(65536, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                left -= len(chunk)

    def do_POST(self):
        if not self._host_ok() or self.path != "/api/start":
            return self._json({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            src = str(body.get("source", "")).strip()
            if not src or src.startswith("-"):
                return self._json({"error": "Enter a video link or file path."}, 400)
            if any(j["status"] == "running" for j in JOBS.values()):
                return self._json({"error": "A job is already running."}, 409)
            body["source"] = src
            jid = uuid.uuid4().hex[:12]
            JOBS[jid] = {"status": "running", "log": [], "clips": []}
            threading.Thread(target=run_job, args=(jid, body), daemon=True).start()
            self._json({"id": jid})
        except (ValueError, KeyError):
            self._json({"error": "bad request"}, 400)


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), H)
    if LAN:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                sk.connect(("8.8.8.8", 80))  # no data sent; just finds your Wi-Fi address
                ip = sk.getsockname()[0]
        except OSError:
            ip = "YOUR-COMPUTER-IP"
        print(f"\nOpen this on your phone (same Wi-Fi):\n\n  http://{ip}:{PORT}/?token={TOKEN}\n\nCtrl+C to stop.")
    else:
        url = f"http://{HOST}:{PORT}"
        print(f"localclip running at {url}  (Ctrl+C to stop)")
        threading.Timer(1, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
