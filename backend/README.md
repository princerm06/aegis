# Backend — Prince

`app/api/` owns HTTP routing, `app/models.py` defines v1 validation schemas,
and `app/services/` loads synthetic fixtures. Tests live in `tests/`.
Run commands from the repository root; see `../docs/LOCAL_SETUP.md`.

Health and immutable demo retrieval remain unchanged. `app/simulation/` adds the
locked in-memory local coordinator, explicit simulated human decisions, simulated
device reports, and WebSocket synchronization. Optimization is a separate pure
module. See `../docs/INTEGRATION.md` for actual routes and payloads.

Production endpoints in the shared contract remain reserved. Local operator IDs
are not authenticated identities; local records are not a durable audit trail.
No CV/LLM output is a medical assessment or dispatch authorization. No real
hardware command path or real emergency dispatch service is implemented.

## Responder phone calls (optional)

`app/api/responder_calls.py` lets the operator call a responder's real phone
from the dashboard Comms panel (Twilio Programmable Voice). It is a talk channel
for the demo team only: it changes no simulation state, records nothing, and
lists numbers masked (`•••• 4567`).

- `GET /api/v1/simulations/responder-calls` → `{configured, responders: [{responder_id, number_hint}], note}`
- `POST /api/v1/simulations/responder-calls/{responder_id}/token` → a 10-minute Twilio access
  token whose only grant is an outgoing call to that responder's number (503 when
  not set up, 404 for a responder with no number).

Set up once (a trial account works, but only calls numbers verified on it, and
the callee hears a trial notice and presses a key before the call connects):

1. Twilio Console → Phone Numbers: get a number, and verify each responder's
   phone under Verified Caller IDs.
2. TwiML Bins → new bin:
   `<Response><Dial callerId="+1YOUR_TWILIO_NUMBER" answerOnBridge="true" timeLimit="1800">{{ResponderNumber}}</Dial></Response>`
3. Voice → TwiML Apps → new app, Voice request URL = the bin's URL.
4. Account → API keys → new Standard key.
5. On the backend host only, write git-ignored `backend/.env`:

   ```
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_API_KEY_SID=SK...
   TWILIO_API_KEY_SECRET=...
   TWILIO_TWIML_APP_SID=AP...
   AEGIS_RESPONDER_PHONES=responder-001=+15551234567
   ```

   Restart the backend. Environment variables override the file.

The operator's browser needs a secure context for the microphone: open the
dashboard as `http://localhost:3000` on the host, or allow the LAN origin in
Chrome's `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.

Optional historical NYC EMS dispatch-duration research is documented in
`../ml/ems/README.md`. Its read-only simulation advisory defaults to disabled
and cannot alter coordination, approval, dispatch, clinical or hardware state.
