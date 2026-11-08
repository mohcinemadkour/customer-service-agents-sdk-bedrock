#!/usr/bin/env python3
"""Error-discovery review server (stdlib only).

usage: python3 server.py DATA_DIR [--host 127.0.0.1] [--port 8092]

GET  /                      HTML app
GET  /api/records           all records (full traces) + boilerplate list
GET  /api/samples           {"ids": [...], "batches": [...]}
POST /api/samples           {"ids": [...], "why": "..."}  -> appended as a new batch
GET  /api/annotations       [ ... ]
POST /api/annotations       full list (the app owns this file)
GET  /api/graph             2D projection of all records
GET  /api/patterns          {"modes": [...]}
POST /api/patterns          full taxonomy (the agent owns this file)
GET  /api/suggestions       [ ... ]
POST /api/suggestions       full list (agent) | {"id":..,"status":..} status update (app)
GET/POST /api/state         {"visited": [...], "current": id}  (which samples the reviewer has opened)
"""
import json, os, sys, threading, argparse, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ap = argparse.ArgumentParser()
ap.add_argument("data"); ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8092)
a = ap.parse_args()
DATA = os.path.abspath(a.data)
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.html")
LOCK = threading.Lock()

def path(name): return os.path.join(DATA, name)
def load(name, default):
    try:
        with open(path(name)) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default
def save(name, obj):
    tmp = path(name) + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=1)
    os.replace(tmp, path(name))

FILES = {"records": ("records.json", {}), "samples": ("samples.json", {"ids": [], "batches": []}),
         "annotations": ("annotations.json", []), "graph": ("graph.json", {"nodes": []}),
         "patterns": ("patterns.json", {"modes": []}), "suggestions": ("suggestions.json", []),
         "state": ("state.json", {"visited": [], "current": None})}

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def route(self):
        p = self.path.split("?")[0].rstrip("/")
        return p[len("/api/"):] if p.startswith("/api/") else ("" if p in ("", "/index.html") else None)

    def do_GET(self):
        r = self.route()
        if r == "":
            with open(APP, "rb") as f: return self.send(200, f.read(), "text/html; charset=utf-8")
        if r in FILES:
            name, default = FILES[r]
            with LOCK: return self.send(200, load(name, default))
        if r == "health":
            return self.send(200, {"ok": True, "t": time.time()})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        r = self.route()
        n = int(self.headers.get("Content-Length") or 0)
        try: body = json.loads(self.rfile.read(n) or b"null")
        except json.JSONDecodeError: return self.send(400, {"error": "bad json"})
        with LOCK:
            if r == "annotations" and isinstance(body, list):
                save("annotations.json", body); return self.send(200, {"ok": True, "n": len(body)})
            if r == "state" and isinstance(body, dict):
                save("state.json", body); return self.send(200, {"ok": True})
            if r == "patterns" and isinstance(body, dict):
                save("patterns.json", body); return self.send(200, {"ok": True})
            if r == "samples" and isinstance(body, dict) and isinstance(body.get("ids"), list):
                s = load("samples.json", {"ids": [], "batches": []})
                new = [i for i in body["ids"] if i not in s["ids"]]
                s["ids"] += new
                s.setdefault("batches", []).append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ids": new, "why": body.get("why", "")})
                save("samples.json", s); return self.send(200, {"ok": True, "added": new})
            if r == "suggestions":
                if isinstance(body, list):
                    old = {x["id"]: x for x in load("suggestions.json", [])}
                    for x in body:  # keep the reviewer's decision if the agent re-pushes an id
                        if x["id"] in old and old[x["id"]].get("status") != "pending":
                            x["status"] = old[x["id"]]["status"]
                    save("suggestions.json", body); return self.send(200, {"ok": True, "n": len(body)})
                if isinstance(body, dict) and "id" in body:
                    ids = body["id"] if isinstance(body["id"], list) else [body["id"]]
                    s = load("suggestions.json", [])
                    for x in s:
                        if x["id"] in ids: x["status"] = body.get("status", "pending")
                    save("suggestions.json", s); return self.send(200, {"ok": True})
        self.send(400, {"error": "bad request"})

print(f"error-discovery on http://{a.host}:{a.port}  data={DATA}", flush=True)
ThreadingHTTPServer((a.host, a.port), H).serve_forever()
