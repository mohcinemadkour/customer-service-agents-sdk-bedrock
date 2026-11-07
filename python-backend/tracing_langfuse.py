"""EvalTrust tracing hook - the ONLY thing a client adds to get full traces.

Sends every step the OpenAI Agents SDK records (agent turns, LLM calls, tool calls,
handoffs, guardrail checks) to Langfuse over OpenTelemetry, instead of to OpenAI.
No agent, prompt, tool or guardrail code is changed.

Needs: LANGFUSE_HOST (or LANGFUSE_BASE_URL), LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY.
"""
import os


def enable():
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        print("[tracing] Langfuse keys not set - tracing off")
        return None
    os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "0"
    os.environ["OPENAI_TRACING_DISABLED"] = "0"
    from agents import set_trace_processors
    from langfuse import get_client
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    set_trace_processors([])          # stop sending traces to OpenAI's dashboard
    client = get_client()             # Langfuse OTel exporter (v4 ingestion)
    OpenAIAgentsInstrumentor().instrument()
    print("[tracing] Agents SDK -> Langfuse enabled:", client.auth_check())
    return client
