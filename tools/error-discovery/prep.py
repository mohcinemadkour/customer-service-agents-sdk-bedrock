#!/usr/bin/env python3
"""Turn an EvalTrust runner result file into error-discovery records,
cluster them, and pick a diverse initial sample. Stdlib only.

usage: python3 prep.py RESULTS.json GOLDEN.jsonl OUT_DIR [--k 7] [--n 20] [--seed 7]
"""
import json, math, os, random, sys, argparse, collections

ap = argparse.ArgumentParser()
ap.add_argument("results"); ap.add_argument("golden"); ap.add_argument("out")
ap.add_argument("--k", type=int, default=7); ap.add_argument("--n", type=int, default=20)
ap.add_argument("--seed", type=int, default=7)
a = ap.parse_args()
random.seed(a.seed)

res = json.load(open(a.results))
golden = {}
for line in open(a.golden):
    line = line.strip()
    if line:
        g = json.loads(line); golden[g.get("case_id") or g.get("id")] = g

CAT_GROUPS = [("guardrail", "Guardrail"), ("isolation", "Isolation"), ("multi-turn", "Multi-turn"),
              ("compensation", "Compensation"), ("rebook", "Booking"), ("booking", "Booking"),
              ("cancel", "Booking"), ("policy", "Policy"), ("seat", "Seat"), ("special", "Seat"),
              ("status", "Flight status"), ("disruption", "Flight status"), ("false trip", "Flight status"),
              ("faq", "FAQ")]
def cat_group(c):
    lc = c.lower()
    for key, g in CAT_GROUPS:
        if key in lc:
            return g
    return "Other"

# ---------- build records ----------
records = []
for r in res["rows"]:
    out = r.get("output") or {}
    turns = []
    for ti, t in enumerate(out.get("turns", [])):
        agents = t.get("agents") or [t.get("start_agent") or "Agent"]
        msgs = t.get("messages") or ([{"agent": t.get("end_agent"), "text": t["reply"]}] if t.get("reply") else [])
        calls = t.get("tool_calls") or []
        # group by agent in the order agents ran (event order inside one turn is not recorded)
        groups = []
        for ag in agents:
            groups.append({"agent": ag,
                           "tools": [c for c in calls if c.get("agent") == ag],
                           "messages": [m["text"] for m in msgs if m.get("agent") == ag and m.get("text")]})
        known = set(agents)
        stray_c = [c for c in calls if c.get("agent") not in known]
        stray_m = [m["text"] for m in msgs if m.get("agent") not in known and m.get("text")]
        if stray_c or stray_m:
            groups.append({"agent": "(unattributed)", "tools": stray_c, "messages": stray_m})
        turns.append({"user": t.get("user", ""), "groups": groups,
                      "handoffs": t.get("handoffs") or [], "guardrail": t.get("guardrail"),
                      "error": t.get("error"), "context": t.get("context") or {},
                      "latency_s": t.get("latency_s")})
    scores = r.get("scores") or {}
    fails = [k for k, v in scores.items() if v == 0]
    g = golden.get(r["case_id"], {})
    reply_len = sum(len(m) for t in turns for gr in t["groups"] for m in gr["messages"])
    records.append({
        "id": r["case_id"], "category": r.get("category", ""), "group": cat_group(r.get("category", "")),
        "input": r.get("input", ""), "expected": r.get("expected", ""), "expect": g.get("expect", {}),
        "turns": turns, "scores": scores, "fails": fails, "error": r.get("error"),
        "latency_s": r.get("latency_s"), "session_id": out.get("session_id"),
        "final_context": out.get("final_context") or {},
        "stats": {"turns": len(turns),
                  "tool_calls": sum(len(gr["tools"]) for t in turns for gr in t["groups"]),
                  "handoffs": sum(len(t["handoffs"]) for t in turns),
                  "reply_chars": reply_len,
                  "latency_s": r.get("latency_s") or 0}})

# ---------- boilerplate: agent texts repeated verbatim across >=3 records ----------
cnt = collections.Counter()
for rec in records:
    seen = set()
    for t in rec["turns"]:
        for gr in t["groups"]:
            for m in gr["messages"]:
                seen.add(m.strip())
    cnt.update(seen)
boiler = sorted(m for m, c in cnt.items() if c >= 3)

# ---------- outlier flags ----------
FLAG_LABELS = {"tool_calls": ("tool calls", "more", "fewer"), "reply_chars": ("reply", "longer", "shorter"),
               "latency_s": ("latency", "slower", "faster"), "handoffs": ("handoffs", "more", "fewer")}
n = len(records)
for rec in records:
    flags = []
    for key, (lab, hi, lo) in FLAG_LABELS.items():
        v = rec["stats"][key]
        below = sum(1 for o in records if o["stats"][key] < v) / n
        above = sum(1 for o in records if o["stats"][key] > v) / n
        if below >= 0.9:
            txt = f"{v:g} {lab}" if key in ("tool_calls", "handoffs") else lab
            flags.append((below, f"{txt} ({hi} than {round(below*100)}%)"))
        elif above >= 0.9 and key in ("reply_chars", "latency_s"):
            flags.append((above, f"{lab} {lo} than {round(above*100)}%"))
    flags.sort(reverse=True)
    rec["flags"] = [f for _, f in flags[:2]]

# ---------- features ----------
groups = sorted({r["group"] for r in records})
end_agents = sorted({(r["turns"][-1]["groups"][-1]["agent"] if r["turns"] and r["turns"][-1]["groups"] else "-") for r in records})
tools = sorted({c["name"] for r in records for t in r["turns"] for gr in t["groups"] for c in gr["tools"]})
def feats(r):
    s = r["stats"]
    v = [s["turns"], s["tool_calls"], s["handoffs"], math.log1p(s["reply_chars"]), s["latency_s"],
         1.0 if any(t["guardrail"] for t in r["turns"]) else 0.0, len(r["fails"])]
    ea = r["turns"][-1]["groups"][-1]["agent"] if r["turns"] and r["turns"][-1]["groups"] else "-"
    v += [1.0 if r["group"] == g else 0.0 for g in groups]
    v += [1.0 if ea == e else 0.0 for e in end_agents]
    used = {c["name"] for t in r["turns"] for gr in t["groups"] for c in gr["tools"]}
    v += [1.0 if tl in used else 0.0 for tl in tools]
    return v
X = [feats(r) for r in records]
d = len(X[0])
mu = [sum(x[j] for x in X) / n for j in range(d)]
sd = [math.sqrt(sum((x[j] - mu[j]) ** 2 for x in X) / n) or 1.0 for j in range(d)]
Z = [[(x[j] - mu[j]) / sd[j] for j in range(d)] for x in X]

def dist2(a_, b_): return sum((p - q) ** 2 for p, q in zip(a_, b_))

def kmeans(Z, k, iters=100, restarts=12):
    best = None
    for _ in range(restarts):
        cents = [Z[random.randrange(len(Z))]]
        while len(cents) < k:  # k-means++
            ds = [min(dist2(z, c) for c in cents) for z in Z]
            tot = sum(ds); pick = random.random() * tot; acc = 0
            for z, dd in zip(Z, ds):
                acc += dd
                if acc >= pick: cents.append(z); break
        for _ in range(iters):
            lab = [min(range(k), key=lambda c: dist2(z, cents[c])) for z in Z]
            new = []
            for c in range(k):
                mem = [z for z, l in zip(Z, lab) if l == c]
                new.append([sum(col) / len(mem) for col in zip(*mem)] if mem else cents[c])
            if new == cents: break
            cents = new
        sse = sum(dist2(z, cents[l]) for z, l in zip(Z, lab))
        if best is None or sse < best[0]: best = (sse, lab, cents)
    return best[1], best[2]

labels, cents = kmeans(Z, a.k)

# ---------- PCA (power iteration, 2 components) ----------
def pca2(Z):
    dd = len(Z[0])
    C = [[sum(z[i] * z[j] for z in Z) / len(Z) for j in range(dd)] for i in range(dd)]
    comps = []
    for _ in range(2):
        v = [random.random() for _ in range(dd)]
        for _ in range(300):
            w = [sum(C[i][j] * v[j] for j in range(dd)) for i in range(dd)]
            for c in comps:
                dot = sum(p * q for p, q in zip(w, c)); w = [p - dot * q for p, q in zip(w, c)]
            nn = math.sqrt(sum(p * p for p in w)) or 1.0
            v = [p / nn for p in w]
        comps.append(v)
    return [[sum(p * q for p, q in zip(z, c)) for c in comps] for z in Z]
P = pca2(Z)

# ---------- sample selection ----------
order = list(range(n))
picked = []
for c in range(a.k):
    mem = sorted([i for i in order if labels[i] == c], key=lambda i: dist2(Z[i], cents[c]))
    take = mem[:2] if len(mem) >= 4 else mem[:1]
    # prefer mixing pass/fail inside a cluster
    if len(mem) >= 4 and bool(records[take[0]]["fails"]) == bool(records[take[1]]["fails"]):
        alt = next((i for i in mem[2:] if bool(records[i]["fails"]) != bool(records[take[0]]["fails"])), None)
        if alt is not None: take[1] = alt
    picked += take
target_random = max(a.n - len(picked), round(a.n * 0.3))
rest = [i for i in order if i not in picked]
random.shuffle(rest)
picked += rest[:target_random]
picked = sorted(set(picked), key=lambda i: records[i]["id"])

for i, rec in enumerate(records):
    rec["cluster"] = labels[i]

os.makedirs(a.out, exist_ok=True)
def dump(name, obj):
    tmp = os.path.join(a.out, name + ".tmp")
    json.dump(obj, open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(a.out, name))

dump("records.json", {"source": os.path.basename(a.results), "label": res.get("label"),
                      "verdict": res.get("verdict"), "criteria": res.get("criteria"),
                      "boilerplate": boiler, "records": records})
dump("graph.json", {"nodes": [{"id": r["id"], "x": P[i][0], "y": P[i][1], "cluster": labels[i],
                               "category": r["category"], "group": r["group"], "failed": bool(r["fails"]),
                               "fails": r["fails"], "title": r["input"][:90]} for i, r in enumerate(records)],
                    "k": a.k})
dump("samples.json", {"ids": [records[i]["id"] for i in picked], "batches": [
    {"at": "initial", "ids": [records[i]["id"] for i in picked], "why": "cluster representatives + random picks"}]})
for name, empty in (("annotations.json", []), ("patterns.json", {"modes": []}), ("suggestions.json", [])):
    if not os.path.exists(os.path.join(a.out, name)):
        dump(name, empty)

print(f"records={n} clusters={a.k} sample={len(picked)} boilerplate={len(boiler)}")
for c in range(a.k):
    mem = [records[i]["id"] for i in order if labels[i] == c]
    print(f"cluster {c}: {len(mem)} -> {', '.join(mem)}")
print("sample:", ", ".join(records[i]["id"] for i in picked))
