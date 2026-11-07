"""Build plan.json from scenes.json + boxes.json (+ optional durations.json). Run on the server."""
import json, sys
B = json.load(open("/opt/capture/shots/boxes.json"))
SC = {s["id"]: s for s in json.load(open("scenes.json"))}
DUR = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}

def b(shot, *keys):
    out = []
    for k in keys:
        v = B.get(shot, {}).get(k)
        if v and isinstance(v[0], list): out += [x for x in v if 0 <= x[1] < 1080 and 0 <= x[0] < 1920]
        elif v and 0 <= v[1] < 1080 and 0 <= v[0] < 1920: out.append(v)
    return out

def union(boxes):
    if not boxes: return None
    x0 = min(x for x, y, w, h in boxes); y0 = min(y for x, y, w, h in boxes)
    x1 = max(x + w for x, y, w, h in boxes); y1 = max(y + h for x, y, w, h in boxes)
    return [x0, y0, x1 - x0, y1 - y0]

def S(img, keys=(), zoom=True, at=0.0, fallback=None, **kw):
    hl = b(img, *keys)
    z = union(hl) if (zoom and hl) else (fallback if zoom else None)
    return dict(img=img, at=at, hl=hl, zoom=z, **kw)

def est(sid):
    return round(len(SC[sid]["text"].split()) / 2.45 + 1.2, 2)

def D(sid):
    return DUR.get(sid, est(sid))

plan = []
def add(sid, label, shots=None, card=None):
    d = D(sid)
    for s in shots or []:
        if isinstance(s.get("at"), float) and s["at"] < 1: s["at"] = round(s["at"] * d, 2)
    plan.append(dict(id=sid, dur=d, label=label, shots=shots or [], card=card))

add("intro", None, card=dict(title="Reading a validation in Langfuse", sub=["EvalTrust internal training", "Case: OpenAI airline support agents on Claude in Amazon Bedrock"]))
add("setup", "The lab", [S("home", ["project"])])
add("tracing", "Tracing", [S("traces", ["session"])])
add("trace", "One trace: AL-051", [S("trace", ["g1", "g2"], hl_start=6.0, hl_gap=1.2), S("trace", ["tool", "seat"], at=0.62, hl_start=1.0, hl_gap=2.0)])
add("leak", "The finding", [S("trace_tool", ["hit"], hl_red=True, hl_start=1.5), S("leak", ["conf", "flight", "seat"], at=0.42, hl_red=True, hl_start=1.2)])
add("sessions", "Sessions", [S("sessions", ["session"])])
add("datasets", "Datasets", [S("datasets", ["v11", "tau"], zoom=False)])
add("items", "Golden cases", [S("items", ["al51", "exp"], zoom=False)])
add("experiments", "Experiments", [S("experiments", ["run"]), S("experiments_gate", ["crit", "gate"], at=0.38)])
add("run", "One run, every case", [S("run", ["bi", "rows"], zoom=False)])
zb = B.get("zero", {}); a51 = zb.get("al51"); near = None
if a51 and zb.get("bi"):
    below = [x for x in zb["bi"] if x[1] > a51[1]]
    near = min(below, key=lambda x: x[1] - a51[1]) if below else None
B.setdefault("zero", {})["near"] = near
add("zero", "From a zero to its trace", [S("zero", ["al51", "near"], hl_red=True)])
add("compare", "Comparing runs", [S("compare", ["base", "cmp", "routing"], zoom=False)])
add("rules", "Where the rules live", [S("gh_criteria", ["bi", "owner", "target"], fallback=[300, 200, 1150, 500]), S("gh_scorer", ["fid"], at=0.63, fallback=[300, 380, 1150, 500])])
add("report", "The verdict", [S("report", ["hold", "bi"], hl_red=True, zoom=False)])
add("tau", "Public benchmark", [S("tau", ["summary"], zoom=False)])
add("wrap", None, card=dict(title="Reviewing a validation", sub=[], list=["Start with the verdict", "Find failed criteria and their owners", "Open the zeros, read the traces", "Fix, re-run the same version", "Compare the runs"]))
json.dump({"fps": 30, "scenes": plan}, open("plan.json", "w"), indent=1)
print("total", round(sum(s["dur"] for s in plan), 1), "s")
for s in plan: print(s["id"], s["dur"], [ (x["img"], len(x["hl"]), bool(x["zoom"])) for x in s["shots"]])
