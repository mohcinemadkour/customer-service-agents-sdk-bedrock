"""How EvalTrust reaches the client's system.

Every adapter takes one golden-dataset case and returns the system's answer in
one shared shape (the "response contract"):

    {
      "decision":      "approve" | "deny" | "send_back" | "nurse_review",
      "cited_policy":  "LBP-2026-07",
      "letter":        "text sent to the provider / member",
      "tool_calls":    [{"name": "check_criteria", "args": {...}}],
      "latency_s":     2.4
    }

Two adapters:
  * HttpEndpoint  - a client's real staging API (option A, no code changes on their side)
  * MockPriorAuth - a simulated client system, so the runner can be demoed end to end
"""
import json
import time
import urllib.request


class HttpEndpoint:
    """Calls the client's staging endpoint with one case.

    `field_map` lets you rename fields when the client's API uses different names,
    e.g. {"decision": "result.outcome"}.
    """

    def __init__(self, url, headers=None, timeout=60, field_map=None):
        self.url = url
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.timeout = timeout
        self.field_map = field_map or {}
        self.name = url

    def _get(self, obj, dotted):
        for part in dotted.split("."):
            obj = obj.get(part, {}) if isinstance(obj, dict) else {}
        return obj or None

    def __call__(self, case):
        body = json.dumps({"case_id": case["case_id"], "input": case["input"]}).encode()
        req = urllib.request.Request(self.url, data=body, headers=self.headers, method="POST")
        start = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            raw = json.loads(r.read())
        latency = time.time() - start
        out = {k: self._get(raw, self.field_map.get(k, k))
               for k in ("decision", "cited_policy", "letter", "tool_calls")}
        out["tool_calls"] = out["tool_calls"] or []
        out["letter"] = out["letter"] or ""
        out["latency_s"] = raw.get("latency_s", latency)
        return out


class MockPriorAuth:
    """A fake prior-authorization multi-agent system with known behaviour per version.

    v2.3  - retriever returns superseded policies (the bug found in the pilot)
    v2.4  - retriever fixed, but new writer model over-shares patient history
            and escalates fewer unclear cases
    v2.4b - retriever fixed, original writer model: should pass every check
    """

    def __init__(self, version="2.3"):
        self.version = version
        self.name = f"mock PriorAuth v{version}"

    def __call__(self, case):
        v = self.version
        policy = case["current_policy"]
        decision = case["expected_decision"]
        letter = f"Decision for {case['case_id']}: {decision}. Basis: {policy}."
        latency = 4.2 + (hash(case["case_id"]) % 20) / 10  # 4.2 - 6.1 s

        # v2.3: retriever picks the superseded policy when one exists -> wrong rules, wrong decision
        if v == "2.3" and case["superseded_policy"]:
            policy = case["superseded_policy"]
            decision = "approve"
            letter = f"Decision for {case['case_id']}: approve. Basis: {policy}."

        # v2.4: new writer model copies the full note into the letter, and auto-decides one unclear case
        if v == "2.4":
            if case["forbidden_terms"]:
                letter += " Member history: " + case["input"]
            if case["case_id"] == "GD-0119":
                decision = "deny"
            latency += 0.7

        return {
            "decision": decision,
            "cited_policy": policy,
            "letter": letter,
            "tool_calls": [{"name": "check_criteria",
                            "args": {"policy": policy, "case_id": case["case_id"]}}],
            "latency_s": round(latency, 2),
        }


class AirlineApp:
    """The airline multi-agent app's EvalTrust endpoint: POST {case_id, turns} -> per-turn record."""

    def __init__(self, base_url, timeout=600):
        self.url = base_url.rstrip("/") + "/eval/conversation"
        self.timeout = timeout
        self.name = "airline multi-agent app @ " + base_url

    def __call__(self, case):
        body = json.dumps({"case_id": case["case_id"], "turns": case["turns"]}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            out = json.loads(r.read())
        out["latency_s"] = max((t.get("latency_s") or 0) for t in out.get("turns", [])) if out.get("turns") else None
        return out
