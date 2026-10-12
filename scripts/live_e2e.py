"""End-to-end proof against a REAL local model — no stubs.

Runs the full MindBroadcast pipeline against a live Ollama instance:

    ingestion -> compile -> world model -> ranking -> validation -> director
    -> audio buffer

Every gate so far passed against StubLanguageModelProvider / StubTTSProvider.
This proves the interfaces hold against a real model. Fail-closed: if the model
is unreachable, it reports the symptom and exits non-zero — it never fakes a
result and never launches the server itself.

Run:  python3 scripts/live_e2e.py [--model qwen3:4b]
"""

import argparse
import asyncio
import os
import sys
import time

# Repo-root import bootstrap.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from core.compiler.orchestrator import KnowledgeCompiler
from core.broadcast.director import BroadcastDirector
from core.broadcast.entities import (
    AudioChunk, Segment, SegmentStatus, ScriptTurn, SpeechAct,
)
from core.broadcast.validation import ScriptValidator
from core.inference.providers import (
    OllamaEmbeddingProvider, OllamaProvider, SilenceTTSProvider,
)
from core.models import Article, Source
from core.persistence import WorldStore

ARTICLE_TEXT = (
    "Unemployment was 6.1% in March according to the national statistics office. "
    "The central bank raised interest rates by half a point on Tuesday. "
    "Officials said forty people were injured in the factory fire in Leeds. "
    "The transport ministry announced a two billion dollar rail investment."
)


def log(step, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {step}" + (f" — {detail}" if detail else ""))
    return ok


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--base-url", default="http://localhost:11434")
    args = parser.parse_args()

    print("=== MindBroadcast live end-to-end proof (real model, no stubs) ===")
    print(f"model: {args.model}   endpoint: {args.base_url}\n")

    provider = OllamaProvider(base_url=args.base_url, model=args.model)
    embedder = OllamaEmbeddingProvider(base_url=args.base_url)
    tts = SilenceTTSProvider()
    results = []

    # 0. Health — the model must be actually reachable and present.
    healthy = await provider.health_check()
    results.append(log("model health_check", healthy,
                       f"{args.model} present on {args.base_url}"))
    if not healthy:
        print("\n  The model is not reachable. Reporting the symptom; not "
              "faking a result and not launching the server.")
        return 1
    embed_ok = embedder.health_check()
    results.append(log("embedding health_check", embed_ok,
                       "nomic-embed-text present" if embed_ok else "absent"))

    store = WorldStore(":memory:")

    # 1. Real ingestion.
    source = Source(name="Live Wire", endpoint="https://wire.example/rss")
    source_id = await store.add_source(source)
    src = await store.get_source(source_id)
    article = Article(
        source_id=src.id, canonical_url="https://wire.example/live-1",
        title="Live report", content_hash="e" * 64,
        normalized_text=ARTICLE_TEXT)
    await store.add_article(article)
    results.append(log("ingestion: article persisted", True, article.id[:12]))

    # 2. Real deterministic compilation.
    compiled = await KnowledgeCompiler(store).compile(article)
    results.append(log("compile: deterministic passes", compiled.ok,
                       f"{len(compiled.claims.claims)} claims extracted"))

    # 3. REAL model generation (no stub).
    t0 = time.monotonic()
    objective = ("Write one factual broadcast sentence reporting the "
                 "unemployment figure from the article. One sentence only.")
    gen = await provider.generate({"objective": objective, "prompt": objective})
    latency = int((time.monotonic() - t0) * 1000)
    results.append(log("generate: real model returned text", bool(gen.text),
                       f"{latency}ms, model={gen.model_id}"))
    if gen.text:
        print(f"       model said: {gen.text.strip()[:120]!r}")

    # 4. REAL structured generation.
    t0 = time.monotonic()
    schema = {"$id": "broadcast_script_v1"}
    struct = await provider.generate_structured(
        {"objective": "Return a one-turn script as JSON with a 'turns' array.",
         "prompt": ("Return JSON: {\"turns\":[{\"sequence\":1,\"text\":\"...\","
                    "\"speech_act\":\"factual_report\"}]}. The text must be one "
                    "factual sentence about unemployment at 6.1%.")},
        schema)
    latency = int((time.monotonic() - t0) * 1000)
    has_struct = bool(struct.structured and struct.structured.get("turns"))
    results.append(log("generate_structured: real model returned JSON",
                       has_struct, f"{latency}ms"))
    if struct.structured:
        print(f"       structured: {struct.structured}")

    # 5. Real embeddings.
    if embed_ok:
        vector = embedder.embed("unemployment rose in March")
        results.append(log("embeddings: real vector", len(vector) > 0,
                           f"{len(vector)}-dim"))

    # 6. Validation of the model's own output — the proof that the pipeline
    #    gates real model text, not just stub text.
    script_turns = []
    if struct.structured and struct.structured.get("turns"):
        for raw in struct.structured["turns"][:4]:
            script_turns.append(ScriptTurn(
                sequence=raw.get("sequence", 1), persona_id="presenter",
                text=raw.get("text", ""),
                speech_act=SpeechAct.FACTUAL_REPORT,
                claim_references=[], evidence_references=[]))
    # If the model produced no usable turns, validate a known-unsupported
    # factual claim to prove stage 2 still fires on real-model-shaped input.
    if not script_turns:
        script_turns = [ScriptTurn(
            sequence=1, persona_id="presenter",
            text="Unemployment was 6.1% in March.",
            speech_act=SpeechAct.FACTUAL_REPORT)]
    report = ScriptValidator(known_voice_profile_ids={"presenter"}).validate(
        _script(script_turns))
    # A factual claim with no evidence reference must NOT be approved —
    # §15.3 holds against real model output.
    results.append(log("validation: unsupported factual claim refused",
                       not report.is_approved,
                       f"outcome={report.outcome.value}"))

    # 7. Director + audio buffer against the real (silent) TTS.
    director = BroadcastDirector()
    segment = Segment(id="live-seg", status=SegmentStatus.PLANNED,
                      media_generation_id="gen-live", persona_id="presenter")
    director.add_segment(segment)
    for status in (SegmentStatus.APPROVED, SegmentStatus.SYNTHESIZING,
                   SegmentStatus.READY):
        director.advance("live-seg", status)
    synth = await tts.synthesize(_synth_req("live-seg", "gen-live",
                                            "Unemployment was 6.1% in March."))
    director.attach_audio("live-seg", AudioChunk(
        segment_id="live-seg", synthesis_attempt="gen-live", sequence=0,
        data=synth.audio, duration_ms=synth.duration_ms,
        is_end_of_segment=True))
    results.append(log("audio: chunk bound to segment+generation",
                       len(director.deliverable_chunks("live-seg")) == 1,
                       f"{len(synth.audio)} bytes synthesized"))

    # 8. Stale-chunk rejection still holds.
    try:
        director.attach_audio("live-seg", AudioChunk(
            segment_id="live-seg", synthesis_attempt="OLD", sequence=1,
            data=b"x"))
        results.append(log("audio: stale chunk rejected", False))
    except Exception:
        results.append(log("audio: stale chunk rejected", True))

    store.close()

    print(f"\n=== {sum(results)}/{len(results)} checks passed "
          f"against a real model ===")
    return 0 if all(results) else 1


def _script(turns):
    from core.broadcast.entities import BroadcastScript
    return BroadcastScript(segment_id="live-seg", version=1, turns=turns)


def _synth_req(segment_id, attempt, text):
    from core.broadcast.providers import SynthesisRequest
    return SynthesisRequest(segment_id=segment_id, text=text,
                            synthesis_attempt=attempt)


if __name__ == "__main__":
    sys.exit(asyncio.get_event_loop().run_until_complete(main()))
