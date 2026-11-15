"""Local development CLI for AEGIS Voice V1.

Supports:
- Single text query (`--query "AEGIS, what's our current situation?"`)
- Audio file transcription + response (`--audio path/to/question.wav`)
- Saving spoken MP3 output (`--speak --save-audio out.mp3`)
- Verifying ElevenLabs subscription & remaining credits (`--check-elevenlabs`)
- Interactive multi-turn REPL (`uv run python -m voice.cli`)
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys

from voice.config import VoiceConfig
from voice.elevenlabs import check_elevenlabs_subscription
from voice.service import VoiceAssistantService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AEGIS Voice V1 — Read-only simulation situational-awareness CLI"
    )
    parser.add_argument(
        "--backend-url",
        default=None,
        help="Override AEGIS_BACKEND_URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--scenario-id",
        default=None,
        help="Simulation scenario ID (default: demo)",
    )
    parser.add_argument(
        "--session-id",
        default="cli-session",
        help="Voice conversation session ID (default: cli-session)",
    )
    parser.add_argument(
        "--query",
        "-q",
        default=None,
        help="Run a single text question and exit",
    )
    parser.add_argument(
        "--audio",
        default=None,
        help="Transcribe an audio file (.wav, .mp3, .webm, .m4a) and answer",
    )
    parser.add_argument(
        "--speak",
        action="store_true",
        help="Request ElevenLabs TTS synthesis for the response",
    )
    parser.add_argument(
        "--save-audio",
        default=None,
        help="Write synthesized MP3 response to this file path",
    )
    parser.add_argument(
        "--check-elevenlabs",
        action="store_true",
        help="Check ElevenLabs API key validity and remaining character quota",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON output",
    )
    args = parser.parse_args(argv)

    overrides: dict[str, str] = {}
    if args.backend_url:
        overrides["AEGIS_BACKEND_URL"] = args.backend_url
    if args.scenario_id:
        overrides["AEGIS_SCENARIO_ID"] = args.scenario_id

    config = VoiceConfig.from_env(overrides=overrides or None)
    service = VoiceAssistantService(config=config)

    if args.check_elevenlabs:
        status = check_elevenlabs_subscription(config)
        print(json.dumps(status, indent=2))
        return 0 if status.get("valid") else 1

    if args.audio:
        audio_path = Path(args.audio)
        if not audio_path.is_file():
            print(f"Error: audio file not found: {audio_path}", file=sys.stderr)
            return 1
        turn = service.handle_audio_turn(
            audio_path.read_bytes(),
            filename=audio_path.name,
            session_id=args.session_id,
            scenario_id=args.scenario_id,
            speak=args.speak or bool(args.save_audio),
        )
        _output_turn(turn, as_json=args.json, save_audio=args.save_audio)
        return 0

    if args.query is not None:
        turn = service.handle_text_query(
            args.query,
            session_id=args.session_id,
            scenario_id=args.scenario_id,
            speak=args.speak or bool(args.save_audio),
        )
        _output_turn(turn, as_json=args.json, save_audio=args.save_audio)
        return 0

    # Interactive REPL
    print(
        f"AEGIS Voice V1 Interactive CLI (mode=simulation, read_only=True, "
        f"speech={'configured' if config.has_elevenlabs_key else 'text_only'})"
    )
    print("Type a question, 'reset' to clear conversation context, or 'exit' to quit.\n")
    while True:
        try:
            line = input("Operator> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in {"exit", "quit", ":q"}:
            break
        if line.lower() == "reset":
            service.sessions.reset(args.session_id)
            print("AEGIS> Session context cleared (simulation state untouched).\n")
            continue
        turn = service.handle_text_query(
            line,
            session_id=args.session_id,
            scenario_id=args.scenario_id,
            speak=args.speak,
        )
        _output_turn(turn, as_json=args.json, save_audio=args.save_audio)
    return 0


def _output_turn(turn, *, as_json: bool, save_audio: str | None) -> None:
    if save_audio and turn.audio_base64:
        out_path = Path(save_audio)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(turn.audio_base64))
    if as_json:
        print(json.dumps(turn.to_dict(), indent=2))
        return
    if turn.user_transcript:
        print(f"Transcript: {turn.user_transcript}")
    print(f"AEGIS [{turn.intent} | {turn.delivery_mode}]: {turn.display_text}")
    print(f"Trace: {', '.join(turn.trace_fields)}")
    if turn.warnings:
        print(f"Warnings: {'; '.join(turn.warnings)}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
