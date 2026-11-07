"""Import a τ²-bench results.json into Langfuse as an Experiment, EvalTrust-style.

Each τ² task becomes a dataset item (what the simulated customer wants + the task's
expected actions/assertions). Each simulation becomes an experiment item with the
benchmark's own verdicts as scores: reward (pass/fail), db_match, communicate,
action checks, plus duration and turn count. Run-level scores: pass^1, DB-match rate.

Usage: python tau2_to_langfuse.py <results.json> --run-name airline-sonnet-4.6 [--dataset tau2-airline]
"""
import argparse
import json
import statistics


def _tools(messages):
    out = []
    for m in messages or []:
        for tc in (m.get("tool_calls") or []):
            out.append(tc.get("name") or (tc.get("function") or {}).get("name"))
    return [t for t in out if t]


def _text(m):
    c = m.get("content")
    return c if isinstance(c, str) else json.dumps(c)[:500] if c else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--dataset", default="tau2-airline")
    a = ap.parse_args()
    d = json.load(open(a.results))
    tasks = {str(t["id"]): t for t in d["tasks"]}
    sims = {str(s["task_id"]): s for s in d["simulations"]}

    from langfuse import Evaluation, get_client
    lf = get_client()
    try:
        lf.create_dataset(name=a.dataset, description="τ²-bench airline tasks (Sierra Research), imported by EvalTrust")
    except Exception:
        pass
    for tid, t in tasks.items():
        us = (t.get("user_scenario") or {}).get("instructions") or {}
        ec = t.get("evaluation_criteria") or {}
        lf.create_dataset_item(
            dataset_name=a.dataset, id=f"{a.dataset}:{tid}",
            input={"task_id": tid, "reason_for_call": us.get("reason_for_call"), "known_info": us.get("known_info")},
            expected_output={"actions": [x.get("name") for x in (ec.get("actions") or [])],
                             "nl_assertions": ec.get("nl_assertions"), "reward_basis": ec.get("reward_basis")},
            metadata={"description": (t.get("description") or {}) if isinstance(t.get("description"), dict) else str(t.get("description"))[:300]})
    ds = lf.get_dataset(a.dataset)

    def task(*, item, **kw):
        s = sims.get(str(item.input["task_id"]))
        if not s:
            return {"missing": True}
        msgs = s.get("messages") or []
        last_agent = next((_text(m) for m in reversed(msgs) if m.get("role") == "assistant" and _text(m)), "")
        return {"termination": s.get("termination_reason"), "turns": len(msgs), "tool_calls": _tools(msgs),
                "last_agent_message": last_agent[:600], "duration_s": round(float(s.get("duration") or 0), 1)}

    def ri(item_input):
        s = sims.get(str(item_input["task_id"])) or {}
        return s.get("reward_info") or {}

    def ev_reward(*, input, **kw):
        r = ri(input)
        return [] if not r else Evaluation(name="tau2.reward", value=float(r.get("reward", 0)))

    def ev_db(*, input, **kw):
        db = (ri(input).get("db_check") or {})
        return [] if "db_match" not in db else Evaluation(name="tau2.db_match", value=1.0 if db["db_match"] else 0.0)

    def ev_comm(*, input, **kw):
        b = (ri(input).get("reward_breakdown") or {})
        return [] if "COMMUNICATE" not in b else Evaluation(name="tau2.communicate", value=float(b["COMMUNICATE"]))

    def ev_actions(*, input, **kw):
        ac = ri(input).get("action_checks")
        if not ac:
            return []
        ok = [1.0 if (x.get("action_match") or x.get("match")) else 0.0 for x in ac]
        return Evaluation(name="tau2.actions_matched", value=sum(ok) / len(ok))

    def ev_turns(*, output, **kw):
        return [] if not output or output.get("missing") else Evaluation(name="tau2.turns", value=float(output["turns"]))

    rewards = [float((s.get("reward_info") or {}).get("reward", 0)) for s in sims.values()]
    dbm = [1.0 if ((s.get("reward_info") or {}).get("db_check") or {}).get("db_match") else 0.0 for s in sims.values()]

    def run_pass(*, item_results, **kw):
        return Evaluation(name="tau2.pass^1", value=statistics.mean(rewards) if rewards else None,
                          comment=f"{sum(rewards):.0f} of {len(rewards)} tasks passed")

    def run_db(*, item_results, **kw):
        return Evaluation(name="tau2.db_match_rate", value=statistics.mean(dbm) if dbm else None)

    ds.run_experiment(name=a.run_name, run_name=a.run_name,
                      description=f"τ²-bench airline · pass^1 {statistics.mean(rewards):.3f} over {len(rewards)} tasks",
                      task=task, evaluators=[ev_reward, ev_db, ev_comm, ev_actions, ev_turns],
                      run_evaluators=[run_pass, run_db], max_concurrency=4,
                      metadata={"benchmark": "tau2-bench", "domain": "airline"})
    lf.flush()
    fails = [tid for tid, s in sims.items() if float((s.get("reward_info") or {}).get("reward", 0)) < 1]
    summary = {"tasks": len(sims), "pass^1": round(statistics.mean(rewards), 3), "db_match_rate": round(statistics.mean(dbm), 3),
               "failed_task_ids": fails,
               "terminations": {k: sum(1 for s in sims.values() if s.get("termination_reason") == k)
                                for k in {s.get("termination_reason") for s in sims.values()}}}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
