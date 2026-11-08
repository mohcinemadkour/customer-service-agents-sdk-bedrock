# Error discovery review app

A small review tool for finding failure modes in EvalTrust runner results. You read real agent traces, highlight what is wrong, and write a free-text note. The agent groups the notes into named failure modes and suggests similar spans in other traces.

## Files

| File | What it does |
|---|---|
| `prep.py` | Turns a runner result JSON plus its golden JSONL into review records. Flags outliers and boilerplate, clusters the cases (k-means), projects them to 2D (PCA) and picks a varied first sample. Uses only the standard library. |
| `server.py` | Standard-library HTTP server for the app and its JSON API. |
| `app.html` | Single-page app with three views: Review, Map and Progress. |

## Run

```bash
python3 prep.py /opt/evaltrust-runner/results/airline-v1.1-run1.json \
                /opt/evaltrust-runner/golden/airline_v1.1.jsonl \
                error_discovery_data --k 7 --n 20
python3 server.py error_discovery_data --port 8092
```

On the lab server the app runs as the `error-discovery` systemd unit. It is served at `https://runner.<ip>.sslip.io/review/`, behind the runner's basic auth.

## Data files (`error_discovery_data/`)

- `records.json`: every case as a full trace (turns, agent groups, tool calls, guardrails, scores).
- `samples.json`: the case ids in the sample, in batches.
- `graph.json`: 2D coordinates and the cluster for every case.
- `annotations.json`: the reviewer's notes. The app owns this file.
- `patterns.json`: the failure-mode taxonomy. The agent owns this file.
- `suggestions.json`: spans the agent suggests, each `pending`, `accepted` or `dismissed`.
- `state.json`: which samples the reviewer has opened.

## API

- `GET /api/{records,samples,graph,annotations,patterns,suggestions,state}` returns the matching file.
- `POST /api/samples` with `{"ids":[...],"why":"..."}` appends a batch.
- `POST /api/patterns` with `{"modes":[{"id","name","description","annotation_ids":[...]}]}` replaces the taxonomy.
- `POST /api/suggestions` with a full list replaces the suggestions. Statuses the reviewer already decided are kept.
- `POST /api/suggestions` with `{"id":..,"status":..}` changes one suggestion's status.

Each suggestion looks like this:

```json
{"id":"s1","record_id":"AL-049","seg":"t1.g0.c0.out","quote":"confirmation LL0EZ6","mode":"Wrong booking loaded","note":"...","status":"pending"}
```

`seg` points to one block of a trace:

- `expected`: the golden expectation.
- `t<turn>.user`: the customer's message.
- `t<turn>.guard`: the guardrail's reasoning.
- `t<turn>.g<agent>.c<call>.args` and `.out`: a tool call's arguments and result.
- `t<turn>.g<agent>.m<msg>`: an agent's message.

## Limitation

Inside one turn, the trace records which agent ran each tool and message, but not the exact order of events. The app groups tool calls and messages by agent, in the order the agents ran.
