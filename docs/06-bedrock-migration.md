# 6. OpenAI → Amazon Bedrock migration

The upstream app calls OpenAI models (`gpt-5.2` for agents, `gpt-4.1-mini` for guardrails). This
deployment runs the same agents on **Anthropic Claude through Amazon Bedrock**, with **no OpenAI account
and no API key**. Bedrock calls use the server's AWS IAM role.

## The whole code change

Two lines, so the model names come from the environment. The defaults keep the original behaviour.

```diff
# python-backend/airline/agents.py
-MODEL = "gpt-5.2"
+import os
+MODEL = os.environ.get("AGENT_MODEL", "gpt-5.2")

# python-backend/airline/guardrails.py
-GUARDRAIL_MODEL = "gpt-4.1-mini"
+import os
+GUARDRAIL_MODEL = os.environ.get("GUARDRAIL_MODEL", "gpt-4.1-mini")
```

No prompt, tool, handoff or guardrail logic changed.

## How it works

The Agents SDK accepts a model name with the prefix `litellm/`. It then sends calls through
[LiteLLM](https://docs.litellm.ai/), which speaks to many providers, instead of the OpenAI client. LiteLLM's
`bedrock/` provider calls the Bedrock Converse API with the standard AWS credential chain. On EC2 that is
the instance role.

```
Agent(model="litellm/bedrock/global.anthropic.claude-sonnet-4-6")
   └─ Agents SDK  →  LitellmModel  →  litellm.acompletion("bedrock/global.anthropic.claude-sonnet-4-6")
                                           └─ boto3  →  Bedrock Converse API (IAM role, us-east-2)
```

Tool definitions, structured outputs (the guardrails' Pydantic schemas) and handoffs are translated by
LiteLLM into Bedrock's tool-use format.

## Configuration

| Variable | Value used | Notes |
| --- | --- | --- |
| `AGENT_MODEL` | `litellm/bedrock/global.anthropic.claude-sonnet-4-6` | Claude Sonnet 4.6, *global* cross-region inference profile |
| `GUARDRAIL_MODEL` | `litellm/bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0` | Claude Haiku 4.5, US inference profile |
| `AWS_REGION_NAME`, `AWS_DEFAULT_REGION` | `us-east-2` | Region for the Bedrock calls |
| `OPENAI_API_KEY` | `not-used-bedrock-only` | A placeholder: some SDK code paths check that the variable exists. No call uses it |

Install `openai-agents[litellm]` (pinned in `requirements-evaltrust.txt`).

## Choices and why

| Choice | Reason |
| --- | --- |
| Sonnet 4.6 for agents | Strong tool use and instruction following. Closest match to the original's frontier model |
| Haiku 4.5 for guardrails | A yes/no classification: fast and cheap, the same role as `gpt-4.1-mini` |
| *Global* inference profile for Sonnet | Spreads requests across regions. Together with one conversation at a time and backoff, it took throttling errors from 26 in run 2 to 0 in run 3 |
| `litellm.num_retries = 4` plus backoff on `RateLimitError` (15 s, 30 s, 60 s) in `eval_api.py` | The account quota is 50 requests per minute. A turn can make 5–10 calls. Retries are counted in each turn record so they can't hide a problem |

## Turning off OpenAI tracing

By default the Agents SDK sends traces to OpenAI's dashboard. `tracing_langfuse.py` calls
`set_trace_processors([])` to stop that, and sends spans to the self-hosted Langfuse instead. No data
goes to OpenAI. See [Observability](08-observability-langfuse.md).

## IAM permissions needed

```json
{
  "Effect": "Allow",
  "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
             "bedrock:Converse", "bedrock:ConverseStream"],
  "Resource": ["arn:aws:bedrock:*::foundation-model/*",
               "arn:aws:bedrock:*:<account-id>:inference-profile/*"]
}
```

The `*` region in the foundation-model ARN is needed because a global or US inference profile can route
a call to any region in its group.

## Switching models

Change the environment file and restart the service. No code change is needed:

```bash
sudo sed -i 's#^AGENT_MODEL=.*#AGENT_MODEL=litellm/bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0#' /etc/airline-app.env
sudo systemctl restart airline-app
```

To compare models fairly, re-run the same golden set against the same signed criteria after the switch
([Evaluation](09-evaluation.md)).
