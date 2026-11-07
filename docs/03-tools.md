# 3. Tools

Tools are Python functions in `python-backend/airline/tools.py`, exposed to the models with the Agents SDK
`@function_tool` decorator. The SDK turns each function's signature into a JSON schema the model fills
in. Tools that take `context` read and write the shared booking context
([Context and sessions](05-context-and-sessions.md)).

**All tools are mock.** They don't call any airline system. They return text built from the two
itineraries in `demo_data.py`:

| Itinerary | Passenger | Confirmation | Flights | Seat |
| --- | --- | --- | --- | --- |
| `disrupted` | Morgan Lee | IR-D204 | PA441 Paris → New York (delayed 5 h), NY802 New York → Austin (connection missed); rebook options NY950, NY982 | 14C |
| `on_time` | Taylor Lee | LL0EZ6 | FLT-123 San Francisco → Los Angeles (on time) | 23A |

## Tool reference

| Tool | Used by | Inputs (from the model) | Reads / writes context | What it does |
| --- | --- | --- | --- | --- |
| `get_trip_details` | Triage | `message` | writes scenario, passenger, confirmation, flight, seat, itinerary, origin, destination | Loads the *disrupted* trip if the text contains "paris", "new york" or "austin", otherwise the *on-time* trip |
| `flight_status_tool` | Flight Information | `flight_number` | writes flight number, may switch scenario | Returns status, gate and times; for PA441 adds "will cause a missed connection to NY802"; unknown flights are reported "on time at gate A10" |
| `get_matching_flights` | Flight Information, Booking | `origin?`, `destination?` | reads scenario | Lists rebook options (NY950, NY982) for the disrupted trip; "no alternates needed" for the on-time trip |
| `book_new_flight` | Booking | `flight_number?` | writes flight, seat, itinerary, confirmation | Books the named option, **or the first option if none is named**; drops the missed NY → Austin leg; makes up a confirmation if none exists |
| `cancel_flight` | Booking | none | reads flight; may write confirmation | Returns "Flight X successfully cancelled"; **takes no confirmation number and asks nothing** |
| `update_seat` | Seat | `confirmation_number`, `new_seat` | **overwrites** confirmation and seat with the model's arguments | Changes the seat |
| `assign_special_service_seat` | Seat | `seat_request` | writes seat (1A if the text contains "front", else 2A) and special-service note | Front-row or medical seating |
| `display_seat_map` | Seat | none | none | Returns `DISPLAY_SEAT_MAP`; the ChatKit UI opens a seat picker. The EvalTrust chat page shows the text only |
| `faq_lookup_tool` | FAQ, Refunds | `question` | none | Keyword lookup, first match wins: bag/baggage → compensation/delay/voucher → seats/plane → wifi → "I don't know" |
| `issue_compensation` | Refunds | `reason` | writes case id `CMP-####` and vouchers | Opens a case and issues the itinerary's vouchers (hotel up to $180, $60 meal, $40 ground for the disrupted trip) |
| `baggage_tool` | **none** | `query` | none | Defined in the file but not attached to any agent |

## Things to know

- **Writes don't check anything.** `cancel_flight`, `book_new_flight` and `issue_compensation` have no
  consent flag, eligibility check or idempotency key. The model's decision is the only control. This is
  the gap behind F1.
- **Missing data gets invented.** Several tools, and both handoff hooks, fill an empty confirmation with
  a random 6-character code and an empty flight with `FLT-<random>`. A conversation can therefore show a
  confirmation number the customer never had.
- **`apply_itinerary_defaults()` runs inside most tools.** If the context is empty it loads the
  *disrupted* trip by default, so a tool can silently attach a booking to the session.
- **Keyword order matters in `faq_lookup_tool`.** *"Is there wifi on the plane?"* matches "plane" before
  "wifi" and returns the seats answer. *"Which rows are exit rows?"* contains neither "seats" nor "plane"
  and gets "I don't know", although the seats answer has the exit rows. This is F3.
- **Progress events.** Several tools call `context.stream(ProgressUpdateEvent(...))` so the ChatKit UI
  can show "Checking status…". In the EvalTrust API these events are accepted and ignored.
