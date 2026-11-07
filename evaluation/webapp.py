"""EvalTrust Runner - small web UI around run.py.

Start:  LANGFUSE_HOST=... LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... python webapp.py
Login protection is done by the reverse proxy (Caddy basic auth) in front of it.
"""
import argparse
import glob
import html
import json
import os
import threading

from flask import Flask, abort, redirect, request, send_file

import run as runner

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
LANGFUSE_PUBLIC_URL = os.environ.get("LANGFUSE_PUBLIC_URL", os.environ.get("LANGFUSE_HOST", ""))
app = Flask(__name__)
lock = threading.Lock()

STYLE = """
body{font-family:Inter,system-ui,-apple-system,sans-serif;color:#14202E;background:#F5F7FA;margin:0;line-height:1.5}
header{background:#0E1A2B;color:#fff;padding:16px 24px;display:flex;gap:12px;align-items:center}
header b{font-size:18px} header a{color:#2ED39A;margin-left:auto;text-decoration:none;font-weight:600}
main{max-width:980px;margin:0 auto;padding:24px}
.card{background:#fff;border:1px solid #E3E7EC;border-radius:12px;padding:20px;margin-bottom:18px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:0 0 12px} .m{color:#55616F;font-size:14px}
label{display:block;font-weight:600;font-size:14px;margin:12px 0 6px}
input,select{width:100%;font:inherit;padding:10px;border:1px solid #CDD3DA;border-radius:8px;box-sizing:border-box}
button{background:#0E1A2B;color:#fff;border:0;border-radius:8px;padding:11px 20px;font:inherit;font-weight:600;cursor:pointer;margin-top:16px}
table{width:100%;border-collapse:collapse} th,td{text-align:left;padding:9px 8px;border-bottom:1px solid #E3E7EC}
th{font-size:12px;color:#55616F;text-transform:uppercase}
.b{border-radius:999px;padding:2px 10px;font-size:12px;font-weight:700} .p{background:#E6F5EE;color:#12865C} .f{background:#FCEBEA;color:#C2362E}
.row{display:grid;grid-template-columns:1fr 1fr;gap:14px} @media(max-width:700px){.row{grid-template-columns:1fr}}
a{color:#2457C5}
"""


def page(body):
    lf = f'<a href="{html.escape(LANGFUSE_PUBLIC_URL)}" target="_blank" rel="noopener">Open Langfuse →</a>' if LANGFUSE_PUBLIC_URL else ""
    return (f"<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>EvalTrust Runner</title><style>{STYLE}</style></head><body>"
            f"<header><b>EvalTrust Runner</b><span class=m style='color:#9AA4B2'>lab</span>{lf}</header><main>{body}</main></body></html>")


def runs():
    out = []
    for p in sorted(glob.glob(os.path.join(RESULTS, "*.json")), key=os.path.getmtime, reverse=True):
        try:
            d = json.load(open(p))
            out.append((os.path.splitext(os.path.basename(p))[0], d))
        except Exception:
            pass
    return out


@app.get("/")
def home():
    hist = runs()
    opts = "".join(f"<option value='{html.escape(s)}'>{html.escape(d['label'])}</option>" for s, d in hist)
    rows = "".join(
        f"<tr><td><a href='/run/{html.escape(s)}'>{html.escape(d['label'])}</a></td><td>{html.escape(d['system'])}</td>"
        f"<td>{d['cases']}</td><td><span class='b {'p' if d['verdict']=='PASS' else 'f'}'>{d['verdict']}</span></td>"
        f"<td class=m>{html.escape(d['run_at'])}</td></tr>" for s, d in hist) or "<tr><td colspan=5 class=m>No runs yet.</td></tr>"
    return page(f"""
<div class=card><h1>Run an evaluation</h1>
<p class=m>Golden dataset: priorauth_v1.2 (12 sample cases) · 6 signed criteria. Scoring by the open-source Braintrust SDK, run locally on this server.</p>
<form method=post action=/run>
<div class=row>
 <div><label for=sys>System to test</label><select id=sys name=system onchange="document.getElementById('urlbox').style.display=this.value=='url'?'block':'none'">
  <option value="mock:2.3">Sample system v2.3 (live version)</option>
  <option value="mock:2.4">Sample system v2.4 (candidate)</option>
  <option value="mock:2.4b">Sample system v2.4b (candidate)</option>
  <option value="url">A real endpoint (URL)…</option></select></div>
 <div><label for=lab>Run name</label><input id=lab name=label placeholder="e.g. v2.4 candidate" required></div>
</div>
<div id=urlbox style="display:none">
 <label for=url>Endpoint URL</label><input id=url name=url placeholder="https://staging.client.com/agent">
 <div class=row><div><label for=hd>Header (optional)</label><input id=hd name=header placeholder="Authorization=Bearer …"></div>
 <div><label for=fm>Field map (optional)</label><input id=fm name=field_map placeholder="decision=result.outcome"></div></div>
</div>
<label for=bl>Compare with (optional)</label><select id=bl name=baseline><option value="">— none —</option>{opts}</select>
<button type=submit>Run evaluation</button></form></div>
<div class=card><h2>Previous runs</h2><table><tr><th>Run</th><th>System</th><th>Cases</th><th>Gate</th><th>When</th></tr>{rows}</table></div>""")


@app.post("/run")
def do_run():
    f = request.form
    spec = f.get("system", "mock:2.3")
    if spec == "url":
        spec = f.get("url", "").strip()
        if not spec.startswith(("http://", "https://")):
            return page("<div class=card>Please enter a valid endpoint URL. <a href='/'>Back</a></div>"), 400
    label = (f.get("label") or spec).strip()[:60]
    base = f.get("baseline") or None
    base_path = os.path.join(RESULTS, base + ".json") if base else None
    if base_path and not os.path.exists(base_path):
        base_path = None
    args = argparse.Namespace(
        system=spec, label=label, baseline=base_path,
        header=[f["header"]] if f.get("header") and "=" in f["header"] else [],
        field_map=[m.strip() for m in (f.get("field_map") or "").split(",") if "=" in m],
        dataset=os.path.join(HERE, "golden", "priorauth_v1.2.csv"),
        criteria=os.path.join(HERE, "criteria.json"), out=RESULTS, concurrency=4, scorers="scorers")
    with lock:
        try:
            runner.run(args)
        except Exception as e:
            return page(f"<div class=card><h2>The run failed</h2><pre>{html.escape(str(e))[:2000]}</pre><a href='/'>Back</a></div>"), 500
    out, jpath, _ = runner.run.last
    return redirect("/run/" + os.path.splitext(os.path.basename(jpath))[0], code=303)


@app.get("/run/<slug>")
def show(slug):
    p = os.path.join(RESULTS, slug + ".json")
    if "/" in slug or not os.path.exists(p):
        abort(404)
    d = json.load(open(p))
    lf = d.get("langfuse") or {}
    lf_line = (f"Sent to Langfuse as run <b>{html.escape(lf['run'])}</b> in dataset <b>{html.escape(lf['dataset'])}</b>. "
               f"<a href='{html.escape(LANGFUSE_PUBLIC_URL)}' target=_blank rel=noopener>Open Langfuse → Datasets</a>"
               if lf.get("ok") else f"Langfuse: {html.escape(lf.get('reason', 'not sent'))}")
    return page(f"<div class=card><p class=m style='margin:0'>{lf_line} · <a href='/'>New run</a> · "
                f"<a href='/report/{html.escape(slug)}' target=_blank>Open report alone</a></p></div>"
                f"<div class=card style='padding:0;overflow:hidden'><iframe src='/report/{html.escape(slug)}' title='Report' "
                f"style='width:100%;height:1100px;border:0'></iframe></div>")


@app.get("/report/<slug>")
def report(slug):
    p = os.path.join(RESULTS, slug + "_report.html")
    if "/" in slug or not os.path.exists(p):
        abort(404)
    return send_file(p)


@app.get("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8080)))
