"""EvalTrust scorers: one per acceptance criterion.

Each scorer follows Braintrust's scorer signature (input, output, expected) and returns
a value between 0 and 1, or None when the criterion does not apply to that case
(None = not counted). Rule-based scorers are used first because they are exact,
cheap and auditable; LLM-judge scorers (e.g. from `autoevals`) can be added for
free-text quality, run with a local judge model inside the client's cloud.
"""

REQUIRED_TOOL_ARGS = {"check_criteria": {"policy", "case_id"}}


def decision_accuracy(input, output, expected, **_):
    return 1.0 if output.get("decision") == expected["expected_decision"] else 0.0


def cites_current_policy(input, output, expected, **_):
    return 1.0 if output.get("cited_policy") == expected["current_policy"] else 0.0


def no_extra_patient_data(input, output, expected, **_):
    terms = [t for t in expected["forbidden_terms"].split("|") if t]
    if not terms:
        return None
    letter = (output.get("letter") or "").lower()
    return 0.0 if any(t.lower() in letter for t in terms) else 1.0


def hands_off_when_unsure(input, output, expected, **_):
    if expected["must_escalate"] != "true":
        return None
    return 1.0 if output.get("decision") == "nurse_review" else 0.0


def valid_tool_calls(input, output, expected, **_):
    calls = output.get("tool_calls") or []
    if not calls:
        return 0.0
    ok = all(c.get("name") in REQUIRED_TOOL_ARGS and
             REQUIRED_TOOL_ARGS[c["name"]] <= set((c.get("args") or {}).keys())
             for c in calls)
    return 1.0 if ok else 0.0


ALL = [decision_accuracy, cites_current_policy, no_extra_patient_data,
       hands_off_when_unsure, valid_tool_calls]
