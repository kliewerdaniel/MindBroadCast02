"""A persistent local Qwen3-TTS HTTP server.

Loads the Qwen3-TTS MLX model ONCE and serves synthesis over HTTP, so the
audio path pays the model-load cost a single time instead of once per call.
This is the fix for the per-call subprocess reload that made the first real-TTS
integration too slow for continuous broadcasting.

Run with a Python that has a working mlx_audio (3.10+; verified on 3.14):

    /opt/homebrew/bin/python3.14 -m core.inference.tts_server --port 7861

Endpoints:
    GET  /health           -> {"ok": true, "model": "..."}
    POST /synthesize       -> 24kHz mono s16le WAV bytes

The server holds the model in-process across requests. It is MindBroadcast's
own audio path, independent of the user's vox server on :7860; both sit behind
the same TTSProvider protocol, so either can back the runtime.

Localhost-only: it binds 127.0.0.1 and refuses a non-local host.
"""

import argparse
import io
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")


def _refuse_non_local(host: str) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            f"refusing to bind non-local host {host!r}; this server is "
            f"localhost-only by design")


def _load_model(model_id: str):
    from mlx_audio.tts.utils import load_model
    return load_model(model_id)


def build_app(model_id: str):
    from flask import Flask, request, Response
    import numpy as np
    import soundfile as sf

    app = Flask(__name__)
    state = {"model": None, "model_id": model_id}

    def get_model():
        if state["model"] is None:
            state["model"] = _load_model(model_id)
        return state["model"]

    @app.get("/health")
    def health():
        # Prove the model is actually loaded, not just importable.
        try:
            get_model()
            return {"ok": True, "model": model_id}
        except Exception as error:
            return {"ok": False, "error": str(error)}, 503

    @app.post("/synthesize")
    def synthesize():
        payload = request.get_json(force=True, silent=True) or {}
        text = payload.get("text", "")
        if not text:
            return {"error": "text is required"}, 400
        speed = float(payload.get("speed", 1.0))
        ref_audio = payload.get("ref_audio")
        ref_text = payload.get("ref_text")
        # A natural-language voice/delivery instruction ("a deep, slow male
        # analyst"). Qwen3-TTS's `instruct` param shapes timbre and pacing from
        # the description — genuine voice variation with no reference audio,
        # unlike generate_voice_design which needs a -VoiceDesign checkpoint.
        instruct = payload.get("instruct")

        model = get_model()
        kwargs = {"text": text, "speed": speed, "verbose": False}
        if instruct:
            kwargs["instruct"] = instruct
        if ref_audio:
            kwargs["ref_audio"] = ref_audio
            if ref_text:
                kwargs["ref_text"] = ref_text

        parts, sr = [], 24000
        for result in model.generate(**kwargs):
            parts.append(np.asarray(result.audio, dtype=np.float32))
            sr = getattr(result, "sample_rate", sr)
        audio = parts[0] if len(parts) == 1 else np.concatenate(parts)

        buffer = io.BytesIO()
        sf.write(buffer, audio, sr, format="WAV", subtype="PCM_16")
        return Response(buffer.getvalue(), mimetype="audio/wav")

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="MindBroadcast Qwen3-TTS server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--model",
                        default="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit")
    args = parser.parse_args(argv)

    _refuse_non_local(args.host)
    app = build_app(args.model)
    print(f"Qwen3-TTS server on http://{args.host}:{args.port} "
          f"model={args.model}", flush=True)
    app.run(host=args.host, port=args.port, threaded=False)


if __name__ == "__main__":
    main()
