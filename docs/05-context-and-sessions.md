# 5. Context and sessions

**In short:** the app remembers a conversation by sending the *whole history* to the model on every
turn, and keeps a small structured *booking context* that the tools read and write. Both live only in the
memory of the server process.

## What a session holds

`Session` in `eval_api.py`, the same fields as `ConversationState` in the upstream `server.py`:

| Field | Type | What it holds | How it's used |
| --- | --- | --- | --- |
| `id` | `str` | `chat-<10 hex>` for chats, `eval-<case>-<8 hex>` for evaluation runs | The chat page sends it with every message; Langfuse groups traces by it |
| `input_items` | list of messages | Every user message, agent reply, tool call, tool result and handoff | Passed whole to `Runner.run()` on every turn. This is the agents' only "memory" of the conversation |
| `context` | `AirlineAgentContext` | Structured booking state (below) | Read by agent instructions and tools; written by tools and handoff hooks |
| `current_agent` | `str` | The agent that answered last | The next message starts with this agent instead of Triage |
| `thread` | ChatKit `ThreadMetadata` | Thread id and creation time | Needed by the ChatKit context wrapper |

## The booking context

`AirlineAgentContext` (`airline/context.py`) is a Pydantic model. It starts **empty** and gets filled
during the conversation:

| Field | Shown in UI | Filled by |
| --- | --- | --- |
| `passenger_name` | yes | `apply_itinerary_defaults()` |
| `confirmation_number` | yes | itinerary defaults, `update_seat` (from the model's argument), handoff hooks (random if empty) |
| `flight_number` | yes | itinerary defaults, `flight_status_tool`, `book_new_flight`, handoff hooks (random if empty) |
| `seat_number` | yes | `update_seat`, `assign_special_service_seat`, `book_new_flight` |
| `origin`, `destination` | yes | itinerary defaults, `get_trip_details` |
| `special_service_note` | yes | `assign_special_service_seat` |
| `vouchers` | only once issued | `issue_compensation` |
| `account_number` | yes | never set in this demo |
| `itinerary` | hidden | itinerary defaults, `book_new_flight` |
| `scenario` | hidden | `disrupted` or `on_time` |
| `compensation_case_id` | hidden | `issue_compensation` |
| `baggage_claim_id` | hidden | never set in this demo |

`public_context()` returns the visible fields. That is what the API returns as `context` and what the
evaluator compares as the end state.

## How context reaches the model

1. **In the prompt.** Five agents' instructions are functions that read the context. For example, the
   Seat agent's prompt says *"The customer's confirmation number is IR-D204 for flight PA441 and current
   seat 14C"*.
2. **Through tools.** A tool receives `RunContextWrapper[AirlineAgentChatContext]` and reads
   `context.context.state`.
3. **Through history.** Earlier tool results, such as "Rebooked to NY950…", stay in `input_items`, so the
   model can see them again.

## Where it is stored, and what that means

| Store | Where | Survives a page refresh | Survives a restart | Expires |
| --- | --- | --- | --- | --- |
| Sessions (`_sessions` dict) | server memory | **no:** the page keeps the session id only in a JavaScript variable | **no** | never |
| ChatKit thread store (`MemoryStore`) | server memory | n/a | **no** | never |
| Langfuse traces | Langfuse (Postgres + ClickHouse) | yes | yes | per Langfuse retention |

Langfuse holds a copy of every turn for review only. The agents never read from it.

Consequences:

- A refresh or a server restart loses the conversation.
- Memory grows with every conversation, because nothing removes old sessions.
- Each turn resends the whole history, so long conversations get slower and use more tokens.
- **Nothing ties the context to a real customer.** Tools and handoff hooks fill an empty context with a
  mock trip or random identifiers, and `update_seat` takes the confirmation number from the model's own
  argument. This is the root of finding F2 (wrong booking loaded).

## What production would need

| Change | Why |
| --- | --- |
| Store sessions in Redis or a database with a time-to-live. The Agents SDK has built-in session classes for this. | Survive restarts; bound memory; run more than one server |
| Keep the session id in a cookie | A refresh keeps the conversation |
| Load the booking from the authenticated customer's record and check every write against it | No acting on someone else's booking |
| Trim or summarise old turns | Keep latency and cost flat in long chats |
| Never invent identifiers; ask the customer instead | No made-up confirmation numbers |

Test cases for these are proposed for golden set v1.1: a long conversation, switching bookings mid-chat,
and continuing after a restart.
