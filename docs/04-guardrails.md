# 4. Guardrails

Two **input guardrails** in `python-backend/airline/guardrails.py` check the customer's message before
the agent's answer is accepted. Each one is a small agent of its own, with structured output, running on
`GUARDRAIL_MODEL` (Claude Haiku 4.5 here).

| Guardrail | Question it answers | Output schema | Trips when |
| --- | --- | --- | --- |
| Relevance Guardrail | Is the latest message related to airline customer service? Chit-chat like "Hi" or "OK" is allowed. | `RelevanceOutput {reasoning, is_relevant}` | `is_relevant = false` |
| Jailbreak Guardrail | Is the latest message an attempt to override instructions, reveal the prompt, or inject code (e.g. `drop table users;`)? | `JailbreakOutput {reasoning, is_safe}` | `is_safe = false` |

Both prompts say to judge **only the most recent user message**, not the history.

## How a block works

1. Both guardrails are attached to every agent (`input_guardrails=[relevance_guardrail, jailbreak_guardrail]`).
   The Agents SDK runs input guardrails for the agent a run *starts* with, which is the current agent of
   the session.
2. Each guardrail calls `Runner.run(guardrail_agent, input)` and returns
   `GuardrailFunctionOutput(output_info=..., tripwire_triggered=...)`.
3. If a tripwire fires, the SDK raises `InputGuardrailTripwireTriggered` and the run stops.
4. `eval_api.py` catches it, records `{name, reasoning}`, replies with the fixed text *"Sorry, I can only
   answer questions related to airline travel."* and adds that refusal to the history.
5. The booking context is not changed, and the session stays with the same agent.

The upstream ChatKit server does the same and also records a pass/fail line for every guardrail, which
the Next.js UI shows in its "Guardrails" panel.

## Cost and latency

Each turn makes two extra Haiku calls, one per guardrail, run alongside the agent's first model call.
They are cheap. Under Bedrock throttling they count against the same requests-per-minute quota.

## Limits seen in evaluation

- **Right block, wrong reason (F5).** The jailbreak in case AL-039 was stopped by the Relevance
  guardrail, not the Jailbreak guardrail. The customer is protected either way, but security logs would
  record the wrong cause.
- **Input only.** There are no output guardrails. Nothing checks what the agent says or which tools it
  calls. Policy rules such as "don't rebook without consent" are not guardrails here (see F1).
- Guardrail accuracy in the golden set was 98%: correct blocks plus no false blocks on normal messages.
