#!/usr/bin/env python3
"""A/B a broadcast model against qwen3:4b on the real longform prompt.

Generates a multi-turn bulletin from the same NPR facts with each model,
reports latency, turn count, and two cheap quality signals:
  - repetition: fraction of content words that recur (lower is better;
    a model restating itself repeats words heavily)
  - coverage: how many distinct supplied facts appear in the output

Usage: python3 scripts/ab_model.py [model_a] [model_b]
"""
import json
import sys
import time
import urllib.request
import urllib.error
import re

OLLAMA = "http://localhost:11434/api/generate"

# A real multi-fact NPR-style story, as the compiler would supply it.
FACTS = [
    "Katie Zacharia worked for one of Trump's companies and raised her "
    "profile appearing on Fox News.",
    "She also amplified right-wing conspiracy theories on social media.",
    "Now she will be the president's chief spokesperson.",
    "The appointment takes effect at the start of next month.",
]

STOP = set("the a an and or but of to in on for with is are was were be been "
           "it its this that will now she he they as at by from has have had "
           "not no also into over after before about".split())


def words(text):
    return [w for w in re.findall(r"[a-z']+", text.lower())]


def repetition_ratio(text):
    ws = [w for w in words(text) if w not in STOP]
    if not ws:
        return 0.0
    return 1.0 - len(set(ws)) / len(ws)


def coverage(text):
    t = text.lower()
    hits = sum(1 for f in FACTS
               if any(k in t for k in words(f)[:3] if k not in STOP))
    return hits, len(FACTS)


def prompt_for():
    """The strict per-fact prompt, matching core/inference/runtime.py.

    This is the current pipeline: rewrite each numbered fact as one spoken
    turn, in order, adding nothing. Kept in sync with the runtime's longform
    branch so A/B measures what actually airs, not the discarded open-ended
    "compose a bulletin" prompt (which padded and invented — repetition
    0.26-0.45).
    """
    numbered = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(FACTS))
    return (
        "Rewrite each numbered fact below as one clear spoken sentence for a "
        "news broadcast.\n"
        "Rules: output exactly one turn per fact, in the same order. Use ONLY "
        "the information in that fact. Do not add names, roles, dates, or "
        "details that are not in the fact. Do not repeat a fact. Vary "
        "sentence openings so it reads as narration, not a list.\n\n"
        f"Facts:\n{numbered}\n\n"
        "Respond with JSON like this example (one turn per fact):\n"
        '{"turns":[{"sequence":1,"text":"<fact 1 rewritten>",'
        '"speech_act":"factual_report"},{"sequence":2,"text":"<fact 2 '
        'rewritten>","speech_act":"factual_report"}]}')


def run(model):
    payload = {
        "model": model,
        "prompt": prompt_for(),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_predict": 8192, "think": False},
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        OLLAMA, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"model": model, "error": f"HTTP {e.code}: {e.read()[:120]}"}
    except Exception as e:
        return {"model": model, "error": f"{type(e).__name__}: {e}"}
    latency = time.monotonic() - started

    text = data.get("response", "") or ""
    try:
        obj = json.loads(text)
        turns = obj.get("turns", [])
        full = " ".join(t.get("text", "") for t in turns)
    except Exception:
        turns, full = [], ""
    cov_hits, cov_total = coverage(full)
    return {
        "model": model,
        "latency_s": round(latency, 1),
        "turns": len(turns),
        "chars": len(full),
        "repetition": round(repetition_ratio(full), 3),
        "coverage": f"{cov_hits}/{cov_total}",
        "done_reason": data.get("done_reason"),
        "text": full,
    }


def main():
    a = sys.argv[1] if len(sys.argv) > 1 else "qwen3:4b"
    b = sys.argv[2] if len(sys.argv) > 2 else "qwen3:30b-a3b"
    for model in (a, b):
        print(f"\n=== {model} ===")
        r = run(model)
        if "error" in r:
            print("  ERROR:", r["error"])
            continue
        print(f"  latency {r['latency_s']}s | {r['turns']} turns | "
              f"{r['chars']} chars | repetition {r['repetition']} | "
              f"coverage {r['coverage']} | done={r['done_reason']}")
        print("  --- transcript ---")
        for line in r["text"].split(". "):
            if line.strip():
                print("   ", line.strip()[:140])


if __name__ == "__main__":
    main()
