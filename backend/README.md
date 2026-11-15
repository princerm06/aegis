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

## Talk link (operator ⇄ responder phone)

`app/api/talk.py` relays WebRTC call setup between the dashboard's **Talk** button
and the responder's phone page (`/responder`). Only the offer and answer (a few
KB) pass through; audio goes directly between the two browsers. In memory, no
recording, no simulation state, no keys or accounts.

- `POST /api/v1/simulations/talk/channels/{responder_id}/heartbeat`: phone page, every 1.5 s; returns any ringing call with its offer
- `GET /api/v1/simulations/talk/channels/{responder_id}`: dashboard; phone online (heartbeat within 6 s) and the call with the phone's answer
- `POST …/offer` `{sdp}` starts a call (replaces any earlier one), `POST …/answer` `{session_id, sdp}`, `POST …/hangup` `{session_id, by}`

Stale or ended calls return 409; at most 16 channels.

`GET /api/v1/simulations/talk/ice-servers` tells both browsers how to find a network
path: STUN by default. When the operator's laptop and the phone can't reach each other
directly (for example both only on Tailscale), add a TURN relay that both can reach.
For example, coturn on a team Mac on the tailnet (`brew install coturn`):

```
turnserver -n --listening-ip <mac-tailscale-ip> --relay-ip <mac-tailscale-ip> --listening-port 3478 \
  --lt-cred-mech --user aegis:<password> --realm aegis --no-tls --fingerprint --no-cli
```

Then, in the backend host's git-ignored `backend/.env` (environment variables override
the file), restart the backend:

```
AEGIS_TALK_TURN_URLS=turn:<mac-tailscale-ip>:3478
AEGIS_TALK_TURN_USERNAME=aegis
AEGIS_TALK_TURN_CREDENTIAL=<password>
AEGIS_TALK_FORCE_RELAY=1   # optional: always use the relay
```

The TURN password is handed to every browser that loads the dashboard, so use a
throwaway one and keep the relay on the tailnet.

Optional historical NYC EMS dispatch-duration research is documented in
`../ml/ems/README.md`. Its read-only simulation advisory defaults to disabled
and cannot alter coordination, approval, dispatch, clinical or hardware state.
