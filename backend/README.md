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

Optional historical NYC EMS dispatch-duration research is documented in
`../ml/ems/README.md`. Its read-only simulation advisory defaults to disabled
and cannot alter coordination, approval, dispatch, clinical or hardware state.
