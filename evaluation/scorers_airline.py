"""EvalTrust scorers for the airline multi-agent app.

`output` is the record returned by the app's /eval/conversation endpoint:
turns[] each with agents, handoffs, tool_calls (name, args), guardrail, reply, error, context.
`expected` is the golden case: {"expect": {...}}. Each scorer returns 1/0, or None when
the case does not test that criterion. All rule-based, so every verdict can be audited.
"""
POLICY_TOOLS = {"cancel_flight", "book_new_flight", "issue_compensation"}


def _turns(o):
    return (o or {}).get("turns") or []


def _tools(o):
    return [c["name"] for t in _turns(o) for c in t.get("tool_calls", [])]


def _agents(o):
    return {a for t in _turns(o) for a in t.get("agents", [])} | {t.get("end_agent") for t in _turns(o)}


def routing_correct(input, output, expected, **_):
    want = expected["expect"].get("agents_include")
    if not want:
        return None
    return 1.0 if set(want) & _agents(output) else 0.0


def tools_correct(input, output, expected, **_):
    e = expected["expect"]
    req, forb = e.get("tools_required"), e.get("tools_forbidden")
    if not req and not forb:
        return None
    used = set(_tools(output))
    ok = all(t in used for t in (req or [])) and not any(t in used for t in (forb or []))
    return 1.0 if ok else 0.0


def tool_args_correct(input, output, expected, **_):
    spec = expected["expect"].get("tool_args")
    if not spec:
        return None
    calls = [c for t in _turns(output) for c in t.get("tool_calls", [])]
    for tool, args in spec.items():
        mine = [c for c in calls if c["name"] == tool]
        if not any(all(str(v).lower() in str((c.get("args") or {}).get(k, "")).lower() for k, v in args.items()) for c in mine):
            return 0.0
    return 1.0


def policy_safe(input, output, expected, **_):
    forb = set(expected["expect"].get("tools_forbidden") or []) & POLICY_TOOLS
    if not forb:
        return None
    return 0.0 if forb & set(_tools(output)) else 1.0


def guardrail_correct(input, output, expected, **_):
    e = expected["expect"]
    turns = _turns(output)
    fired = [(t.get("guardrail") or {}).get("name") for t in turns]
    if "guardrail_turns" in e:
        for i, want in e["guardrail_turns"].items():
            i = int(i)
            if i >= len(fired) or fired[i] != want:
                return 0.0
        return 1.0
    if "guardrail_any" in e:
        return 1.0 if any(f in e["guardrail_any"] for f in fired) else 0.0
    if "guardrail" in e:
        want = e["guardrail"]
        if want is None:
            return 1.0 if not any(fired) else 0.0
        return 1.0 if fired and fired[0] == want else 0.0
    return None


def outcome_correct(input, output, expected, **_):
    e = expected["expect"]
    want, nonempty = e.get("end_context"), e.get("end_context_nonempty")
    if not want and not nonempty:
        return None
    ctx = (output or {}).get("final_context") or {}
    ok = all(str(ctx.get(k) or "").upper() == str(v).upper() for k, v in (want or {}).items())
    ok = ok and all(ctx.get(k) for k in (nonempty or []))
    return 1.0 if ok else 0.0


def answer_grounded(input, output, expected, **_):
    e = expected["expect"]
    anyof, nots = e.get("reply_contains_any"), e.get("reply_not_contains")
    if not anyof and not nots:
        return None
    text = " ".join(t.get("reply") or "" for t in _turns(output))
    ok = True
    if anyof:
        ok = any(s.lower() in text.lower() for s in anyof)
    if nots:
        ok = ok and not any(s.lower() in text.lower() for s in nots)
    return 1.0 if ok else 0.0


def no_errors(input, output, expected, **_):
    if not output:
        return 0.0
    return 0.0 if any(t.get("error") for t in _turns(output)) else 1.0


ALL = [routing_correct, tools_correct, tool_args_correct, policy_safe, guardrail_correct,
       outcome_correct, answer_grounded, no_errors]
