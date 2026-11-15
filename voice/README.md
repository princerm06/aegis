# AEGIS Voice V1 — Read-Only Conversational Situational-Awareness Assistant

AEGIS Voice V1 is a standalone, read-only conversational assistant for the **AEGIS** simulation-only emergency-response coordination platform. It allows an operator on a desktop or mobile browser (or via the local CLI) to ask natural spoken or typed questions, ask contextual follow-up questions, and hear grounded spoken answers derived directly from authoritative live simulation data.

> **Strict Safety Boundary**: AEGIS Voice V1 is **100% read-only** and **simulation-only**. It never approves or rejects proposals, never authorizes or executes dispatch, never records or infers medical triage severity, never associates or relinks camera tracks, never mutates maps or responder statuses, and never triggers Arduino hardware actions.

---

## 1. Architecture Overview

```text
Browser Microphone / Wispr Flow / Text Input
  │
  ▼
Audio Capture Adapter (voice/audio.py)
  │  Validates MIME (webm/mp4/wav/ogg/mp3), non-empty size (<= 4 MB), duration (<= 20s)
  ▼
ElevenLabs Scribe Speech-to-Text (voice/elevenlabs.py :: ElevenLabsSTTAdapter)
  │  POST https://api.elevenlabs.io/v1/speech-to-text (model_id: scribe_v1)
  ▼
Deterministic Intent & Safety Interpreter (voice/intents.py)
  │  Intercepts prohibited mutations & resolves pronouns via SessionStore (voice/context.py)
  ▼
Read-Only AEGIS Simulation Client (voice/client.py :: ReadOnlyAegisClient)
  │  Hardcoded GET-only allowlist (/state, /events, /intelligence, /explanation)
  ▼
State-Grounded Answer Generator (voice/responder.py)
  │  Builds simulation-framed spoken + display response with explicit field trace_fields
  ▼
ElevenLabs Text-to-Speech Adapter (voice/elevenlabs.py :: ElevenLabsTTSAdapter)
  │  POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id} (eleven_flash_v2_5, LRU cached)
  ▼
Browser Speaker / Transcript Panel (voice/static/index.html or Next.js Dashboard)
```

### Module Layout

| File | Responsibility |
| --- | --- |
| `voice/config.py` | Loads `voice/.env` server-side, redacts secrets (`redact_secrets`), and exposes safe public diagnostics (`public_summary()`). |
| `voice/audio.py` | Validates push-to-talk audio clips (`AudioClip`, MIME normalization, byte/duration limits, no disk persistence). |
| `voice/client.py` | `ReadOnlyAegisClient` with a hardcoded `GET`-only route allowlist (`validate_readonly_request`). Blocks any state-mutating method or path with `ReadOnlyViolationError`. |
| `voice/context.py` | `ConversationContext` and thread-safe `SessionStore` with TTL expiration. Stores only entity IDs (`last_responder_id`, `last_casualty_id`, `last_proposal_id`, `last_assignment_id`, `salient_*`) and turn history—never caches simulation state. |
| `voice/intents.py` | Deterministic intent classification, prohibited-mutation & prompt-injection interception (`prohibited_mutation`), and conservative pronoun/follow-up resolution. |
| `voice/responder.py` | Generates `GroundedAnswer` from fresh `SimulationState` and `EventPage` with field-level provenance (`trace_fields`). |
| `voice/elevenlabs.py` | `ElevenLabsSTTAdapter` (`scribe_v1`), `ElevenLabsTTSAdapter` (`eleven_flash_v2_5` with 64-entry in-memory LRU cache), and `check_elevenlabs_subscription`. |
| `voice/service.py` | `VoiceAssistantService` orchestrating full speech mode, text-only mode, and graceful fallback modes. |
| `voice/server.py` | Standalone FastAPI server (`port 8010`) serving REST endpoints and the responsive push-to-talk web UI. |
| `voice/static/index.html` | Self-contained desktop & mobile push-to-talk + text fallback web interface with mute and stop-speaking controls. |
| `voice/cli.py` | Local development CLI and interactive multi-turn REPL. |
| `voice/tests/test_voice_assistant.py` | Offline automated test suite covering all intents, follow-ups, safety gates, and mocked ElevenLabs adapters. |

---

## 2. ElevenLabs Setup & Security

### Secret Location

1. Copy `voice/.env.example` to `voice/.env` (which is excluded by `.gitignore`):
   ```bash
   cp voice/.env.example voice/.env
   ```
2. Edit `voice/.env` and set your credentials:
   ```dotenv
   ELEVENLABS_API_KEY=replace_with_your_key
   ELEVENLABS_VOICE_ID=JBFqnCBsd6RMkjVDRZzb
   ELEVENLABS_TTS_MODEL=eleven_flash_v2_5
   ELEVENLABS_STT_MODEL=scribe_v1
   AEGIS_BACKEND_URL=http://127.0.0.1:8000
   AEGIS_SCENARIO_ID=demo
   ```

### Supported Models

- **Speech-to-Text (`ELEVENLABS_STT_MODEL`)**:
  - `scribe_v1` *(default, recommended for push-to-talk clips)*
  - `scribe_v1_experimental`
- **Text-to-Speech (`ELEVENLABS_TTS_MODEL`)**:
  - `eleven_flash_v2_5` *(default, ultra-low ~75ms model latency, 50% lower character credit cost)*
  - `eleven_turbo_v2_5`
  - `eleven_multilingual_v2`

### Verifying Subscription & Remaining Credits

Run the built-in subscription check before a live demo to confirm your API key is valid and has remaining character credits:

```bash
uv run python -m voice.cli --check-elevenlabs
```

Example output:
```json
{
  "configured": true,
  "valid": true,
  "tier": "creator",
  "status": "active",
  "character_count": 4120,
  "character_limit": 100000,
  "characters_remaining": 95880,
  "has_remaining_credits": true
}
```

### Cost & Credit Safeguards

- **Zero paid API calls in tests**: Automated tests use deterministic mock transports.
- **Recording bounds**: Audio uploads are capped at `20 seconds` and `4 MB`.
- **TTS character bounds**: Spoken responses are capped at `420 characters`.
- **LRU Audio Cache**: `ElevenLabsTTSAdapter` caches synthesized MP3 bytes by `(voice_id, model_id, text)` in memory so repeated questions or UI rerenders never re-bill credits.
- **Quota fallback**: If credits are exhausted (`HTTP 402` or `429 quota_exceeded`), the service automatically returns `delivery_mode: "partial_text_fallback"` with the full grounded text answer.

---

## 3. Running AEGIS Voice

### A. Text-Only or Speech CLI

Works immediately without an ElevenLabs key in `text_only` mode, or with TTS/STT when `voice/.env` is configured:

```bash
# Single text query against the running AEGIS backend
uv run python -m voice.cli --query "AEGIS, what's our current situation?"

# Transcribe a recorded audio file and save spoken MP3 answer
uv run python -m voice.cli --audio question.wav --speak --save-audio answer.mp3

# Interactive multi-turn REPL (supports follow-up questions)
uv run python -m voice.cli
```

### B. Standalone Voice Server & Push-to-Talk Web UI (`port 8010`)

Start the AEGIS backend on port `8000` (if not already running) and the Voice service on port `8010`:

```bash
# Terminal 1: Authoritative AEGIS Backend
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# Terminal 2: AEGIS Voice V1 Server
uv run uvicorn voice.server:app --host 127.0.0.1 --port 8010
```

Open `http://127.0.0.1:8010` in your desktop or mobile browser.

---

## 4. Supported Voice Commands & Contextual Follow-Ups

| Capability | Intent ID | Example Utterances | Backend Fields Used (`trace_fields`) |
| --- | --- | --- | --- |
| **A. Incident Status** | `incident_status` | *"AEGIS, status report."*, *"What's happening?"*, *"Give me the current situation."* | `state.scenario.casualties`, `state.scenario.responders`, `state.tracks`, `state.current_proposal_id`, `state.assignments` |
| **B. Explain Optimization** | `explain_optimization` | *"Explain the current plan."*, *"Why did you assign Responder Alpha?"*, *"Why was that route selected?"* | `state.proposals[id].assignments`, `state.proposals[id].engine`, `state.proposals[id].reasons`, `state.proposals[id].objective_values` |
| **C. Responder Status** | `responder_status` | *"Where is Responder Alpha?"*, *"What's Alpha doing?"*, *"Is Responder One available?"* | `state.scenario.responders[id]`, `state.assignments`, `state.proposals` |
| **D. Unassigned Casualties** | `unassigned_casualties` | *"Which casualties are unassigned?"*, *"Who still needs a responder?"*, *"Are there casualties without coverage?"* | `state.scenario.casualties`, `state.resolved_casualty_ids`, `state.assignments`, `state.proposals`, `state.last_recommendation` |
| **E. Recent Changes** | `recent_changes` | *"What changed?"*, *"What happened recently?"*, *"What's the latest update?"* | `events.events[seq]`, `events.latest_sequence`, `state.dispatches` |
| **F. Acknowledgment Status** | `acknowledgment_status` | *"Who hasn't acknowledged?"*, *"Are there outstanding acknowledgments?"*, *"Has Responder Alpha received the assignment?"* | `state.assignments[id].status`, `state.proposals[id].status` |
| **G. Casualty Status** | `casualty_status` | *"How is casualty-001?"*, *"What is casualty 2's priority?"* | `state.scenario.casualties[id]`, `state.tracks`, `state.assignments`, `state.proposals` |
| **H. Prohibited Action Guard** | `prohibited_mutation` | *"AEGIS, dispatch Alpha immediately."*, *"Approve the proposal."*, *"Mark casualty 1 immediate."* | `state.mode`, `state.scene_revision`, `state.current_proposal_id` |

### Contextual Follow-Up Rules

- Each `session_id` maintains an isolated `ConversationContext` recording `last_responder_id`, `last_casualty_id`, `last_proposal_id`, `last_assignment_id`, and `salient_responder_ids`.
- Follow-ups such as *"Which responder?"*, *"Why was that responder selected?"*, *"What is their assignment?"*, and *"Has it been acknowledged?"* resolve against the session's active entity focus while always fetching **fresh** backend state on every turn.
- If a pronoun reference is ambiguous (e.g., asking *"Why was that responder selected?"* right after a general plan overview with two proposed responders), AEGIS returns `intent: "clarification_needed"` and asks the operator to name the responder rather than guessing.

---

## 5. Frontend Integration Contract (For Hillary's Next.js Dashboard)

Hillary's Next.js frontend (`http://localhost:3000`) can integrate AEGIS Voice without touching any API keys by calling the local Voice HTTP service (`http://127.0.0.1:8010`, CORS pre-enabled for `localhost:3000`).

### REST Endpoints

#### 1. `GET /health`
Returns operational mode (`"full_speech"` or `"text_only"`), model names, and limits. Never includes `ELEVENLABS_API_KEY`.

#### 2. `POST /api/v1/voice/query` (Text or Wispr Flow Input)
Request:
```json
{
  "mode": "simulation",
  "session_id": "dashboard-operator-1",
  "scenario_id": "demo",
  "text": "AEGIS, what's our current situation?",
  "speak": true
}
```

#### 3. `POST /api/v1/voice/audio-turn` (Push-to-Talk Audio)
Request:
```json
{
  "mode": "simulation",
  "session_id": "dashboard-operator-1",
  "scenario_id": "demo",
  "audio_base64": "<base64-encoded-webm-or-mp4-bytes>",
  "mime_type": "audio/webm",
  "filename": "recording.webm",
  "duration_seconds": 3.2,
  "speak": true
}
```
*(Alternatively, `POST /api/v1/voice/turn` accepts `multipart/form-data` with a `file` field or raw binary audio body.)*

Response (`200 OK` for both `/query` and `/audio-turn`):
```json
{
  "mode": "simulation",
  "read_only": true,
  "state_mutated": false,
  "session_id": "dashboard-operator-1",
  "scenario_id": "demo",
  "user_transcript": "AEGIS, what's our current situation?",
  "intent": "incident_status",
  "spoken_text": "Simulation status for scenario demo...",
  "display_text": "Simulation status for scenario demo...",
  "delivery_mode": "full_speech",
  "audio_base64": "//uQxAA...",
  "audio_mime_type": "audio/mpeg",
  "tts_cached": false,
  "referenced_entities": {
    "responder_id": "responder-001",
    "casualty_id": "casualty-001",
    "proposal_id": "sim-proposal-0001",
    "assignment_id": null
  },
  "trace_fields": [
    "state.scenario_id",
    "state.scene_revision",
    "state.scenario.casualties",
    "state.scenario.responders",
    "state.tracks",
    "state.current_proposal_id",
    "state.proposals",
    "state.assignments"
  ],
  "warnings": [],
  "error_category": null
}
```

#### 4. `POST /api/v1/voice/session/{session_id}/reset`
Clears only the voice conversation context for `{session_id}` (never resets the simulation).

### Desktop & Mobile Browser Considerations

1. **HTTPS / Localhost Requirement**: Modern browsers expose `navigator.mediaDevices.getUserMedia` only on `http://localhost`, `http://127.0.0.1`, or `https://` origins. When accessing from a mobile phone over LAN, use an HTTPS tunnel or local TLS proxy; otherwise the UI automatically offers the text input fallback.
2. **iOS Safari MediaRecorder**: iOS Safari records `audio/mp4` whereas Chrome/Firefox record `audio/webm;codecs=opus`. Both `voice/static/index.html` and `voice/audio.py` negotiate and accept both container formats automatically.
3. **Autoplay Unlock**: Mobile browsers require a user gesture (the push-to-talk tap or Send click) before playing `new Audio(...)`. Always stop any active `Audio` instance (`audio.pause()`) when the user presses Talk, Mute, or Stop Speaking.
4. **Wispr Flow Compatibility**: Operators running Wispr Flow on desktop can dictate directly into the text input field (`#textInput`) with zero custom integration required.
