"""Send an EvalTrust run to Langfuse (self-hosted v4) as an Experiment.

Langfuse v4 only accepts traces over OpenTelemetry, and experiments are created with
the official SDK's experiment runner. So we:
  1. make sure the golden dataset exists in Langfuse (one item per case),
  2. call dataset.run_experiment(): the "task" replays the answer EvalTrust already
     collected for that case, and the evaluators replay the Braintrust scores,
  3. add run-level scores: each criterion's value, and the gate verdict (1 = PASS, 0 = HOLD).

In Langfuse: Datasets -> priorauth_v1.2 -> Experiments -> compare v2.3 / v2.4 / v2.4b.
Config: LANGFUSE_HOST (or LANGFUSE_BASE_URL), LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY.
Fails soft: if Langfuse is missing or unreachable, the evaluation still completes.
"""
import os


def _configured():
    return all(os.environ.get(k) for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")) and \
        (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL"))


def export(out, dataset_name):
    if not _configured():
        return {"ok": False, "reason": "Langfuse not configured"}
    try:
        from langfuse import Evaluation, get_client
    except Exception as e:
        return {"ok": False, "reason": f"langfuse SDK missing: {e}"}
    try:
        lf = get_client()
        try:
            lf.create_dataset(name=dataset_name, description=f"EvalTrust golden dataset for {out['project']}")
        except Exception:
            pass  # already exists
        for row in out["rows"]:
            lf.create_dataset_item(
                dataset_name=dataset_name, id=f"{dataset_name}:{row['case_id']}",
                input={"case_id": row["case_id"], "request": row["input"], "category": row["category"]},
                expected_output={"expected": row["expected"]},
                metadata={"category": row["category"]})
        dataset = lf.get_dataset(dataset_name)
        by_case = {r["case_id"]: r for r in out["rows"]}

        def task(*, item, **kwargs):
            r = by_case[item.input["case_id"]]
            return {"answer": r["answer"], "latency_s": r["latency_s"], "detail": r.get("output")}

        def make_item_eval(name):
            def ev(*, input, output, expected_output, metadata=None, **kwargs):
                v = by_case[input["case_id"]]["scores"].get(name)
                return [] if v is None else Evaluation(name=name, value=float(v))  # [] = not applicable
            ev.__name__ = name
            return ev

        score_names = sorted({k for r in out["rows"] for k in r["scores"]})
        evaluators = [make_item_eval(n) for n in score_names]

        def make_run_eval(c):
            def ev(*, item_results, **kwargs):
                if c["value"] is None:
                    return []
                return Evaluation(name=f"criterion.{c['id']}", value=c["value"],
                                  comment=f"{c['name']}: target {c['direction']} {c['target']} -> "
                                          f"{'PASS' if c['pass'] else 'FAIL'}")
            return ev

        def verdict(*, item_results, **kwargs):
            failed = [c["name"] for c in out["criteria"] if not c["pass"]]
            return Evaluation(name="evaltrust.gate", value=1.0 if out["verdict"] == "PASS" else 0.0,
                              comment=out["verdict"] + (": " + ", ".join(failed) if failed else ""))

        run_evaluators = [make_run_eval(c) for c in out["criteria"]] + [verdict]
        dataset.run_experiment(
            name=out["label"], run_name=out["label"],
            description=f"EvalTrust gate {out['verdict']} · system {out['system']}",
            task=task, evaluators=evaluators, run_evaluators=run_evaluators,
            metadata={"system": out["system"], "verdict": out["verdict"]}, max_concurrency=4)
        lf.flush()
        return {"ok": True, "dataset": dataset_name, "run": out["label"],
                "host": os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")}
    except Exception as e:  # never break the evaluation because of the dashboard
        return {"ok": False, "reason": f"{type(e).__name__}: {str(e)[:300]}"}
