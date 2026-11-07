"""EvalTrust harness around the unchanged airline multi-agent app.

Adds, next to the app's own endpoints:
  POST /eval/conversation  - run a scripted multi-turn conversation, return a structured
                             record per turn (agent path, handoffs, tool calls + args,
                             guardrail trips, context, final reply, latency). This is what
                             the EvalTrust runner calls (option A, black-box + record).
  POST /chat/api           - one turn of a live chat session (for the demo page)
  GET  /                   - a simple chat page that shows which agent / tools ran

The turn loop mirrors the app's own server.respond(): it starts from the current agent,
uses the same agents, context object and guardrail refusal text.
Run:  uvicorn eval_api:app --host 127.0.0.1 --port 8100
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

import tracing_langfuse

LF = tracing_langfuse.enable()  # must run before agents execute

import litellm  # noqa: E402  retry transient Bedrock throttling (infrastructure, not app behaviour)
litellm.num_retries = 4

from agents import (HandoffOutputItem, InputGuardrailTripwireTriggered, MessageOutputItem,  # noqa: E402
                    Runner, ToolCallItem, ToolCallOutputItem, ItemHelpers)
from agents.exceptions import MaxTurnsExceeded  # noqa: E402
from chatkit.types import ThreadMetadata  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from main import app  # noqa: E402  (the original FastAPI app, unchanged)
from memory_store import MemoryStore  # noqa: E402
from airline.context import AirlineAgentChatContext, create_initial_context, public_context  # noqa: E402
from server import _get_agent_by_name  # noqa: E402
from airline.agents import triage_agent  # noqa: E402

REFUSAL = "Sorry, I can only answer questions related to airline travel."
_store = MemoryStore()


class Session:
    def __init__(self, sid: str):
        self.id = sid
        self.thread = ThreadMetadata(id=sid, created_at=__import__("datetime").datetime.now())
        self.context = create_initial_context()
        self.input_items: List[Dict[str, Any]] = []
        self.current_agent = triage_agent.name


_sessions: Dict[str, Session] = {}


def _args(raw):
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return raw


async def run_turn(s: Session, text: str, meta: Optional[dict] = None) -> dict:
    from langfuse import propagate_attributes  # noqa: WPS433

    s.input_items.append({"role": "user", "content": text})
    ctx = AirlineAgentChatContext(thread=s.thread, store=_store, request_context={}, state=s.context)
    start_agent = s.current_agent
    rec = {"user": text, "start_agent": start_agent, "agents": [start_agent], "handoffs": [],
           "tool_calls": [], "guardrail": None, "reply": "", "error": None}
    t0 = time.time()
    attrs = dict(session_id=s.id, trace_name="airline-turn",
                 tags=["airline-app"] + ([meta["case_id"]] if meta and meta.get("case_id") else []),
                 metadata={k: str(v) for k, v in (meta or {}).items()})
    try:
        result = None
        for attempt in range(4):  # back off on Bedrock throttling (quota), recorded in the result
            try:
                with propagate_attributes(**attrs) if LF else _nullctx():
                    result = await Runner.run(_get_agent_by_name(s.current_agent), s.input_items, context=ctx, max_turns=12)
                break
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ != "RateLimitError" or attempt == 3:
                    raise
                rec["throttle_retries"] = attempt + 1
                await __import__("asyncio").sleep(15 * 2 ** attempt)
        for item in result.new_items:
            agent_name = getattr(getattr(item, "agent", None), "name", None)
            if isinstance(item, HandoffOutputItem):
                rec["handoffs"].append({"from": item.source_agent.name, "to": item.target_agent.name})
                rec["agents"].append(item.target_agent.name)
            elif isinstance(item, ToolCallItem):
                raw = item.raw_item
                name = getattr(raw, "name", None) or (raw.get("name") if isinstance(raw, dict) else None)
                args = getattr(raw, "arguments", None) or (raw.get("arguments") if isinstance(raw, dict) else None)
                if name and not name.startswith("transfer_to_"):
                    rec["tool_calls"].append({"agent": agent_name, "name": name, "args": _args(args)})
            elif isinstance(item, ToolCallOutputItem):
                if rec["tool_calls"] and "output" not in rec["tool_calls"][-1]:
                    rec["tool_calls"][-1]["output"] = str(item.output)[:400]
            elif isinstance(item, MessageOutputItem):
                rec.setdefault("messages", []).append({"agent": agent_name, "text": ItemHelpers.text_message_output(item)})
                rec["reply"] = "\n\n".join(m["text"] for m in rec["messages"])
        s.input_items = result.to_input_list()
        s.current_agent = result.last_agent.name
    except InputGuardrailTripwireTriggered as exc:
        g = exc.guardrail_result
        rec["guardrail"] = {"name": getattr(g.guardrail, "name", None) or g.guardrail.get_name(),
                            "reasoning": getattr(g.output.output_info, "reasoning", "")}
        rec["reply"] = REFUSAL
        s.input_items.append({"role": "assistant", "content": REFUSAL})
    except MaxTurnsExceeded:
        rec["error"] = "max_turns_exceeded"
    except Exception as e:  # keep the harness alive; record the failure
        rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    rec["end_agent"] = s.current_agent
    rec["context"] = public_context(s.context)
    rec["latency_s"] = round(time.time() - t0, 2)
    if LF:
        LF.flush()
    return rec


class _nullctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class ConversationIn(BaseModel):
    case_id: Optional[str] = None
    turns: List[str]


@app.post("/eval/conversation")
async def eval_conversation(body: ConversationIn):
    if not body.turns or len(body.turns) > 12:
        raise HTTPException(400, "1-12 turns")
    s = Session(f"eval-{body.case_id or 'adhoc'}-{uuid.uuid4().hex[:8]}")
    t0 = time.time()
    turns = [await run_turn(s, t, {"case_id": body.case_id}) for t in body.turns]
    return {"case_id": body.case_id, "session_id": s.id, "turns": turns,
            "final_context": public_context(s.context), "latency_s": round(time.time() - t0, 2)}


class ChatIn(BaseModel):
    session_id: Optional[str] = None
    message: str


@app.post("/chat/api")
async def chat_api(body: ChatIn):
    sid = body.session_id or f"chat-{uuid.uuid4().hex[:10]}"
    s = _sessions.setdefault(sid, Session(sid))
    rec = await run_turn(s, body.message[:2000])
    return {"session_id": sid, **rec}


PAGE = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Airline multi-agent app · EvalTrust lab</title><style>
body{margin:0;font-family:Inter,system-ui,sans-serif;background:#F5F7FA;color:#14202E}
header{background:#0E1A2B;color:#fff;padding:14px 20px;font-weight:700}
header span{color:#9AA4B2;font-weight:400;font-size:14px;margin-left:8px}
main{max-width:860px;margin:0 auto;padding:16px}
#log{display:flex;flex-direction:column;gap:12px;margin-bottom:90px}
.u{align-self:flex-end;background:#0E1A2B;color:#fff;padding:10px 14px;border-radius:14px;max-width:80%}
.a{align-self:flex-start;background:#fff;border:1px solid #E3E7EC;padding:12px 14px;border-radius:14px;max-width:90%;white-space:pre-wrap}
.meta{font-size:12px;color:#55616F;margin-top:8px;display:flex;flex-wrap:wrap;gap:6px}
.chip{border-radius:999px;padding:2px 9px;background:#EEF1F4}.h{background:#E9F0FD;color:#2457C5}.t{background:#E6F5EE;color:#12865C}.g{background:#FCEBEA;color:#C2362E}
form{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #E3E7EC;padding:12px;display:flex;gap:8px;justify-content:center}
input{flex:1;max-width:760px;font:inherit;padding:11px;border:1px solid #CDD3DA;border-radius:10px}
button{font:inherit;font-weight:600;background:#0E1A2B;color:#fff;border:0;border-radius:10px;padding:0 18px;cursor:pointer}
.hint{font-size:13px;color:#55616F}
</style></head><body><header>Airline customer service · multi-agent<span>OpenAI Agents SDK · Claude on Bedrock · traced to Langfuse</span></header>
<main><p class=hint>Try: "I'm flying to Paris tomorrow, can I change my seat to a window?" · "My flight to New York is delayed, what are my options?" · "What's the baggage allowance?" · "Write me a poem about strawberries."</p><div id=log></div></main>
<form id=f><label for=m class=hint style="position:absolute;left:-9999px">Message</label><input id=m placeholder="Message the airline assistant" autocomplete=off><button>Send</button></form>
<script>
let sid=null;const log=document.getElementById('log');
function el(c,t){const d=document.createElement('div');d.className=c;d.textContent=t;log.appendChild(d);window.scrollTo(0,document.body.scrollHeight);return d}
document.getElementById('f').onsubmit=async e=>{e.preventDefault();const m=document.getElementById('m');const t=m.value.trim();if(!t)return;m.value='';el('u',t);const w=el('a','…');
 try{const r=await fetch('/chat/api',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,message:t})});const d=await r.json();sid=d.session_id;
 w.textContent=d.reply||d.error||'(no reply)';const meta=document.createElement('div');meta.className='meta';
 const add=(c,x)=>{const s=document.createElement('span');s.className='chip '+c;s.textContent=x;meta.appendChild(s)};
 add('','agent: '+d.end_agent);(d.handoffs||[]).forEach(h=>add('h','handoff → '+h.to));(d.tool_calls||[]).forEach(c=>add('t','tool: '+c.name));if(d.guardrail)add('g','guardrail: '+d.guardrail.name);add('',d.latency_s+' s');w.appendChild(meta)}
 catch(err){w.textContent='Error: '+err}}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def home():
    return PAGE
