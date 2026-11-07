# Evaluation harness (EvalTrust runner)

Scores the airline app against signed acceptance criteria. Full explanation: [docs/09-evaluation.md](../docs/09-evaluation.md).

| File | Purpose |
| --- | --- |
| `criteria_airline.json` | The 9 signed acceptance criteria (targets and owners), v1 |
| `criteria_airline_v1.1.json` | v1 + `booking_isolation` (10 criteria) |
| `golden/airline_v1.jsonl` | 50 golden conversations, AL-001 to AL-050, with expectations (v1, used for run 3) |
| `golden/airline_v1.1.jsonl` | v1 + `foreign_ids` on AL-049 + AL-051 to AL-053 (booking isolation) |
| `scorers_airline.py` | 9 rule-based scorers (1 / 0 / not applicable), incl. `booking_isolation` |
| `run.py` | Runs every case, scores it with the Braintrust SDK locally, compares with the criteria, writes JSON + HTML, exits 1 on HOLD |
| `adapters/systems.py` | How the runner calls a system; `AirlineApp` posts to `/eval/conversation` |
| `langfuse_export.py` | Writes a run to Langfuse as a dataset experiment |
| `tau2_to_langfuse.py` | Loads τ²-bench results into Langfuse (dataset `tau2-airline`) |
| `scorers.py` | Scorers for the runner's built-in prior-authorization mock (not used for the airline app) |

```bash
pip install -r requirements.txt
python run.py --system airline:http://127.0.0.1:8100 \
  --dataset golden/airline_v1.jsonl --criteria criteria_airline.json \
  --scorers scorers_airline --label my-run --concurrency 1
```

Set `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` to also send the run to Langfuse.
