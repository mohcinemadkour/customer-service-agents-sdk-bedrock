# 2. Agents

All six agents are defined in `python-backend/airline/agents.py` as `Agent[AirlineAgentChatContext]`.
They use the same model (`AGENT_MODEL`, Claude Sonnet 4.6 here) and the same two input guardrails.

Five of the six have **dynamic instructions**: a Python function that builds the prompt each turn from
the current context, so the model always sees the live confirmation number, flight and seat. The FAQ
agent and the Triage agent have fixed prompts.

## Summary

| Agent (`name`) | Purpose | Tools | Hands off to |
| --- | --- | --- | --- |
| Triage Agent | Entry point; routes the request | `get_trip_details` | all five specialists |
| Flight Information Agent | Status, connection risk, alternatives | `flight_status_tool`, `get_matching_flights` | Booking & Cancellation, Triage |
| Booking and Cancellation Agent | Book, rebook, cancel | `cancel_flight`, `get_matching_flights`, `book_new_flight` | Seat & Special Services, Refunds & Compensation, Triage |
| Seat and Special Services Agent | Seat changes, medical / front-row seating | `update_seat`, `assign_special_service_seat`, `display_seat_map` | Refunds & Compensation, Triage |
| FAQ Agent | Policy questions, answered only from the FAQ tool | `faq_lookup_tool` | Triage |
| Refunds and Compensation Agent | Compensation cases and vouchers after disruption | `issue_compensation`, `faq_lookup_tool` | FAQ, Triage |

## Triage Agent

- **Role:** the first agent for every new session. It chooses a specialist and hands off.
- **Instruction highlights:**
  - Route flight status to Flight Information, booking changes to Booking and Cancellation, seating to
    Seat and Special Services, policy to FAQ, and disruption support to Refunds and Compensation.
  - *"If the message mentions Paris/New York/Austin and context is missing, call get_trip_details"* to
    load the trip.
  - Do at most one tool call, then **one handoff**. Don't ask the customer to confirm each step.
- **Note:** `get_trip_details` chooses the *disrupted* trip only if the message names Paris, New York or
  Austin. Any other message loads the *on-time* trip (confirmation `LL0EZ6`). This is the cause of
  finding F2.

## Flight Information Agent

- **Role:** reports flight status and whether a delay breaks a connection.
- **Instruction highlights:**
  1. Use the confirmation and flight from context; ask once if missing, but *"do not block if you have
     hydrated data"*.
  2. Call `flight_status_tool` immediately.
  3. If the trip is disrupted, call `get_matching_flights`, then hand off to Booking and Cancellation
     *"to secure rebooking"*.
- **Note:** step 3 makes this agent start a rebooking whenever it sees a delay, even if the customer only
  asked for the status.

## Booking and Cancellation Agent

- **Role:** the only agent that can change a booking.
- **Instruction highlights:**
  1. Work from the confirmation and flight in context; *"proceed without asking"* when they are present.
  2. For a new flight, call `get_matching_flights`, then `book_new_flight` *"to secure the best match"*.
  3. For cancellations, *"confirm details and use cancel_flight"*.
  4. Summarise what changed.
  - *"Execute autonomously: perform multiple tool calls in your turn without waiting for user responses."*
- **Note:** the prompt asks for confirmation only before cancelling, never before rebooking, and nothing
  in code enforces it. See F1.

## Seat and Special Services Agent

- **Role:** seat changes and medical or front-row requests.
- **Instruction highlights:** use `update_seat` for a normal change, `assign_special_service_seat` for
  front-row or medical needs, and `display_seat_map` if the customer wants to choose visually. If the
  data is present, act without re-asking.
- **Note:** the prompt mentions handing off "to Baggage if baggage help is pending", but there is no
  Baggage agent in this app.

## FAQ Agent

- **Role:** answers policy questions.
- **Instruction highlights:** identify the last question, call `faq_lookup_tool`, and *"do not rely on
  your own knowledge"*.
- **Note:** answer quality is limited by the keyword matching inside `faq_lookup_tool` (see F3).

## Refunds and Compensation Agent

- **Role:** opens a compensation case and issues hotel, meal and ground-transport vouchers.
- **Instruction highlights:** check policy first (`faq_lookup_tool` or the FAQ agent), then call
  `issue_compensation`, then say which receipts to keep.
- **Note:** `issue_compensation` takes no amount and checks no eligibility. It issues whatever vouchers
  the active mock itinerary carries.

## Common settings

| Setting | Value | Where |
| --- | --- | --- |
| Model | `os.environ["AGENT_MODEL"]`, default `gpt-5.2` | `agents.py` |
| Prompt prefix | `RECOMMENDED_PROMPT_PREFIX` from the Agents SDK | every agent |
| Input guardrails | `relevance_guardrail`, `jailbreak_guardrail` | every agent |
| Max steps per turn | 12 (EvalTrust API); SDK default 10 (upstream ChatKit server) | `eval_api.py`, `server.py` |
| Handoff hooks | `on_booking_handoff`, `on_seat_booking_handoff` | `agents.py` |
