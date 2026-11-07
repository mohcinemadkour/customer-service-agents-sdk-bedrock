"""EvalTrust runner.

  golden dataset (CSV) -> call the client's system -> Braintrust scores each case locally
  -> EvalTrust compares results to the signed criteria -> PASS / HOLD + report

Usage:
  python run.py --system mock:2.3
  python run.py --system mock:2.4 --baseline results/v2.3.json
  python run.py --system https://staging.client.com/agent --header "Authorization=Bearer XXX"

Exit code is 0 on PASS and 1 on HOLD, so the same command works as a CI release gate.
"""
import argparse
import csv
import datetime as dt
import html
import json
import math
import os
import sys

from braintrust import Eval

import importlib
import langfuse_export
from adapters.systems import AirlineApp, HttpEndpoint, MockPriorAuth

HERE = os.path.dirname(os.path.abspath(__file__))


def load_cases(path):
    if path.endswith(".jsonl"):
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def describe(case, out):
    """Human-readable input / expected / answer for reports and Langfuse."""
    if "turns" in case:
        e = case.get("expect", {})
        exp = []
        if e.get("agents_include"): exp.append("agent: " + " or ".join(e["agents_include"]))
        if e.get("tools_required"): exp.append("tools: " + ", ".join(e["tools_required"]))
        if e.get("tools_forbidden"): exp.append("never: " + ", ".join(e["tools_forbidden"]))
        if "guardrail" in e: exp.append("guardrail: " + str(e["guardrail"] or "none"))
        if e.get("end_context"): exp.append("state: " + ", ".join(f"{k}={v}" for k, v in e["end_context"].items()))
        if e.get("reply_contains_any"): exp.append("mentions: " + " / ".join(e["reply_contains_any"]))
        turns = (out or {}).get("turns") or []
        got = []
        if turns:
            got.append("agents: " + " > ".join(dict.fromkeys(a for t in turns for a in t.get("agents", []))))
            tools = [c["name"] for t in turns for c in t.get("tool_calls", [])]
            got.append("tools: " + (", ".join(tools) or "none"))
            g = [t["guardrail"]["name"] for t in turns if t.get("guardrail")]
            if g: got.append("guardrail: " + ", ".join(g))
            errs = [t["error"] for t in turns if t.get("error")]
            if errs: got.append("error: " + errs[0])
            got.append("reply: " + (turns[-1].get("reply") or "")[:160])
        return " ⏎ ".join(case["turns"]), "; ".join(exp), " | ".join(got)
    return case["input"], case["expected_decision"], f"{(out or {}).get('decision')} · {(out or {}).get('cited_policy')}"


def make_system(spec, headers, field_map=None):
    if spec.startswith("airline:"):
        return AirlineApp(spec.split(":", 1)[1])
    if spec.startswith("mock:"):
        return MockPriorAuth(spec.split(":", 1)[1])
    return HttpEndpoint(spec, headers=headers, field_map=field_map)


def p95(values):
    v = sorted(values)
    return v[max(0, math.ceil(0.95 * len(v)) - 1)] if v else None


def run(args):
    criteria = json.load(open(args.criteria))
    cases = load_cases(args.dataset)
    system = make_system(args.system, dict(h.split("=", 1) for h in args.header),
                         dict(m.split("=", 1) for m in args.field_map))

    # 1) Braintrust does the running and scoring, locally (no account, nothing uploaded)
    result = Eval(
        "EvalTrust",
        experiment_name=args.label or system.name,
        data=lambda: [{"input": c, "expected": c, "metadata": {"case_id": c["case_id"]}} for c in cases],
        task=lambda case: system(case),
        scores=importlib.import_module(args.scorers).ALL,
        no_send_logs=True,
        max_concurrency=args.concurrency,
    )

    # 2) EvalTrust layer: per-case record, aggregate, compare with signed targets
    rows = []
    for r in result.results:
        c = r.input
        inp, exp, ans = describe(c, r.output)
        rows.append({
            "case_id": c["case_id"], "category": c.get("category", ""), "input": inp,
            "expected": exp, "answer": ans, "output": r.output,
            "latency_s": (r.output or {}).get("latency_s"),
            "error": str(r.error) if r.error else None,
            "scores": r.scores,
        })

    measured = {}
    for crit in criteria["criteria"]:
        cid = crit["id"]
        if cid == "latency_p95_s":
            measured[cid] = p95([x["latency_s"] for x in rows if x["latency_s"] is not None])
            n = len(rows)
        else:
            vals = [x["scores"].get(cid) for x in rows if x["scores"].get(cid) is not None]
            measured[cid] = sum(vals) / len(vals) if vals else None
            n = len(vals)
        ok = measured[cid] is not None and (
            measured[cid] >= crit["target"] if crit["direction"] == "min" else measured[cid] <= crit["target"])
        crit.update({"value": measured[cid], "n": n, "pass": ok})

    errors = [x for x in rows if x["error"]]
    failed = [c for c in criteria["criteria"] if not c["pass"]]
    verdict = "PASS" if not failed and not errors else "HOLD"

    baseline = json.load(open(args.baseline)) if args.baseline else None
    out = {
        "run_at": dt.datetime.now().isoformat(timespec="seconds"),
        "project": criteria["project"], "client": criteria["client"],
        "system": system.name, "label": args.label or system.name,
        "dataset": os.path.basename(args.dataset), "cases": len(rows),
        "verdict": verdict, "criteria": criteria["criteria"],
        "failed_cases": [x for x in rows if any(v == 0 for v in x["scores"].values() if v is not None)],
        "errors": errors, "rows": rows,
        "baseline": {"label": baseline["label"], "criteria": baseline["criteria"]} if baseline else None,
    }
    os.makedirs(args.out, exist_ok=True)
    slug = (args.label or system.name).replace(" ", "_").replace(":", "").replace("/", "_")
    jpath = os.path.join(args.out, f"{slug}.json")
    hpath = os.path.join(args.out, f"{slug}_report.html")
    out["langfuse"] = langfuse_export.export(out, "golden-" + os.path.splitext(os.path.basename(args.dataset))[0])
    json.dump(out, open(jpath, "w"), indent=2)
    open(hpath, "w").write(report_html(out))
    run.last = (out, jpath, hpath)

    print(f"\nEvalTrust gate: {verdict}  ({len(failed)} of {len(criteria['criteria'])} criteria failed)")
    for c in criteria["criteria"]:
        print(f"  {'PASS' if c['pass'] else 'FAIL'}  {c['name']:<26} {fmt(c)}  target {fmt_t(c)}")
    print(f"Results: {jpath}\nReport:  {hpath}")
    lf = out["langfuse"]
    print(f"Langfuse: {'sent run ' + lf['run'] + ' to ' + lf['host'] if lf['ok'] else 'skipped (' + lf['reason'] + ')'}")
    return 0 if verdict == "PASS" else 1


def fmt(c, v=None):
    v = c["value"] if v is None else v
    if v is None:
        return "n/a"
    return f"{v:.1f} s" if c["id"] == "latency_p95_s" else f"{v * 100:.1f}%"


def fmt_t(c):
    return f"≤ {c['target']:.0f} s" if c["direction"] == "max" else f"≥ {c['target'] * 100:.0f}%"


def report_html(o):
    e = html.escape
    base = {c["id"]: c for c in (o["baseline"] or {}).get("criteria", [])}
    rows = ""
    for c in o["criteria"]:
        b = base.get(c["id"])
        rows += (f"<tr><td>{e(c['name'])}<div class=m>Owner: {e(c['owner'])} · n={c['n']}</div></td>"
                 f"<td>{fmt_t(c)}</td>" + (f"<td>{fmt(b)}</td>" if o["baseline"] else "") +
                 f"<td><b>{fmt(c)}</b></td><td><span class='b {'p' if c['pass'] else 'f'}'>"
                 f"{'Pass' if c['pass'] else 'Fail'}</span></td></tr>")
    fails = "".join(
        f"<tr><td class=mono>{e(x['case_id'])}</td><td>{e(x['input'])}</td><td>{e(x['expected'])}</td>"
        f"<td>{e(str(x['answer']))}</td>"
        f"<td>{', '.join(k for k, v in x['scores'].items() if v == 0)}</td></tr>"
        for x in o["failed_cases"]) or "<tr><td colspan=5>No failed cases.</td></tr>"
    hold = o["verdict"] == "HOLD"
    return f"""<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>EvalTrust report · {e(o['label'])}</title><style>
body{{font-family:Inter,system-ui,sans-serif;color:#14202E;max-width:900px;margin:40px auto;padding:0 20px;line-height:1.5}}
h1{{font-size:24px;margin:0}} h2{{font-size:14px;letter-spacing:.06em;text-transform:uppercase;color:#55616F;margin:28px 0 8px}}
.v{{border-radius:12px;padding:16px 20px;margin:18px 0;font-weight:700;font-size:18px;background:{'#FCEBEA' if hold else '#E6F5EE'};color:{'#C2362E' if hold else '#12865C'}}}
table{{width:100%;border-collapse:collapse}} th,td{{text-align:left;padding:9px 8px;border-bottom:1px solid #E3E7EC;vertical-align:top}}
th{{font-size:12px;color:#55616F;text-transform:uppercase}} .m{{font-size:12px;color:#55616F}} .mono{{font-family:monospace}}
.b{{border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600}} .p{{background:#E6F5EE;color:#12865C}} .f{{background:#FCEBEA;color:#C2362E}}
</style></head><body>
<div class=m>EVALTRUST · INDEPENDENT VALIDATION RUN</div><h1>{e(o['project'])}</h1>
<p class=m>Client: {e(o['client'])} · System: {e(o['system'])} · Dataset: {e(o['dataset'])} ({o['cases']} cases) · {e(o['run_at'])}</p>
<div class=v>{'HOLD: ' + str(sum(not c['pass'] for c in o['criteria'])) + ' criteria failed' if hold else 'PASS: all criteria met'}</div>
<h2>Results against signed criteria</h2><table><tr><th>Criterion</th><th>Target</th>{'<th>' + e(o['baseline']['label']) + '</th>' if o['baseline'] else ''}<th>This run</th><th>Gate</th></tr>{rows}</table>
<h2>Failed cases</h2><table><tr><th>Case</th><th>Request</th><th>Expected</th><th>System answered</th><th>Failed checks</th></tr>{fails}</table>
<h2>Method</h2><p>Cases from the client's golden dataset, scored per case by rule-based checks (Braintrust open-source SDK, run locally, nothing uploaded), compared with targets signed before testing. Gate rule: every criterion must meet its target.</p>
</body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EvalTrust evaluation runner")
    ap.add_argument("--system", required=True, help="mock:2.3 | mock:2.4 | mock:2.4b | https://client-endpoint")
    ap.add_argument("--dataset", default=os.path.join(HERE, "golden", "priorauth_v1.2.csv"))
    ap.add_argument("--criteria", default=os.path.join(HERE, "criteria.json"))
    ap.add_argument("--baseline", help="results JSON of the live version, to compare against")
    ap.add_argument("--label", help="name for this run, e.g. v2.4")
    ap.add_argument("--header", action="append", default=[], help="HTTP header for the endpoint, Key=Value")
    ap.add_argument("--field-map", action="append", default=[], help="map our field to the client's, e.g. decision=result.outcome")
    ap.add_argument("--scorers", default="scorers", help="scorer module: scorers | scorers_airline")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--concurrency", type=int, default=4)
    sys.exit(run(ap.parse_args()))
