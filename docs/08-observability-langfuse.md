# 8. Observability with Langfuse

Every customer turn, from the chat page or from an evaluation run, is recorded as one **trace** in a
self-hosted [Langfuse v4](https://langfuse.com/docs). A trace shows each agent, model call, tool call,
handoff and guardrail check, with inputs, outputs, timing and tokens.

## How it is wired

`python-backend/tracing_langfuse.py` is the only tracing code. It runs once when `eval_api.py` starts:

```python
set_trace_processors([])                      # stop sending traces to OpenAI
client = get_client()                         # Langfuse client: OpenTelemetry exporter
OpenAIAgentsInstrumentor().instrument()       # OpenInference: Agents SDK spans -> OpenTelemetry
```

- [OpenInference](https://github.com/Arize-ai/openinference)'s `OpenAIAgentsInstrumentor` converts the
  Agents SDK's own tracing events into OpenTelemetry spans.
- The Langfuse SDK (v5) exports them to Langfuse's OTel endpoint. Langfuse v4 in *events_only* mode
  accepts only OpenTelemetry, not the old `/api/public/ingestion` API.
- `run_turn()` wraps each run in `propagate_attributes(...)`, so every span carries the turn's
  attributes (below), and calls `flush()` at the end of the turn.

If `LANGFUSE_PUBLIC_KEY` or `LANGFUSE_SECRET_KEY` is missing, tracing is off and the app works as before.
The service log shows `[tracing] Agents SDK -> Langfuse enabled: True` when it is on.

No agent, prompt, tool or guardrail code was changed to get traces. For a client, this file plus three
environment variables is the whole integration.

## What each trace carries

| Attribute | Value | Use |
| --- | --- | --- |
| Trace name | `airline-turn` | One trace per customer message |
| Session id | `chat-…` (chat page) or `eval-AL-0xx-…` (golden run) | **Sessions** view groups a whole conversation |
| Tags | `airline-app`, plus the case id (e.g. `AL-049`) in evaluation runs | Filter one test case |
| Metadata | `case_id` in evaluation runs | Link a trace back to the golden case |
| Environment | `lab` (`LANGFUSE_TRACING_ENVIRONMENT`) | Keep lab traffic apart from other environments |

Inside a trace, spans nest roughly like this (simplified; exact span names come from OpenInference):

```
airline-turn
└─ Agent run: Triage Agent
   ├─ guardrail: Relevance Guardrail  → LLM call (Haiku 4.5)
   ├─ guardrail: Jailbreak Guardrail  → LLM call (Haiku 4.5)
   ├─ LLM call (Sonnet 4.6)
   ├─ tool: get_trip_details
   └─ handoff → Flight Information Agent
      └─ Agent run: Flight Information Agent
         ├─ LLM call (Sonnet 4.6)
         ├─ tool: flight_status_tool
         └─ ...
```

## How to check a conversation

1. Open https://langfuse.3.143.190.229.sslip.io → project **EvalTrust Lab** → **Tracing**.
2. Newest traces are at the top. A message shows up a few seconds after its reply.
3. Click a trace to see the tree: which agent answered, which tools ran with what arguments, latency
   per step, and tokens.
4. **Sessions** shows a whole chat. Filter by session id `chat-…` for manual tests or `eval-…` for golden
   runs.
5. Nothing appears? Check the environment filter (`lab`), then `journalctl -u airline-app` for the
   tracing line.

## Evaluation data in Langfuse

| Dataset | Run | Contents |
| --- | --- | --- |
| `golden-airline_v1` | one experiment per runner run (e.g. `airline-sonnet-4.6-run3`) | 50 golden cases, each linked to its traces, with the 8 scores attached |
| `tau2-airline` | `tau2-airline-sonnet-4.6` | 50 τ²-bench tasks with reward and action-match scores |

Experiments are written with the Langfuse SDK's `dataset.run_experiment()` (`evaluation/langfuse_export.py`,
`evaluation/tau2_to_langfuse.py`). A score that doesn't apply to a case is left out, not written as 0.
