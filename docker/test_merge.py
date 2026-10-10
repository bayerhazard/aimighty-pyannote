# Offline smoke test for the multi-module merge (no torch, no model).
# Run:  pip install fastapi && python test_merge.py
import os
import sys

os.environ.setdefault("AUDIO_BASE", "pyannote")
os.environ.setdefault("MODEL_SUPPORTS", "supports_vad,supports_diar,supports_speaker_embed")
os.environ.setdefault("MODEL_NAME", "test/diar")
os.environ.setdefault("MODEL_SOURCE", "hf://test/diar,hf://test/embed")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

from wrapper import contract, gpu, multi  # noqa: E402
from wrapper.caps import diar as diar_mod  # noqa: E402
from wrapper.caps import embed as embed_mod  # noqa: E402
from wrapper.caps import vad as vad_mod  # noqa: E402


def method_paths(app):
    out = []
    for r in app.routes:
        path = getattr(r, "path", None)
        if path is None:
            continue
        if "WebSocket" in type(r).__name__:
            out.append(("WS", path))
        elif getattr(r, "methods", None):
            for m in sorted(r.methods):
                if m not in ("HEAD", "OPTIONS"):
                    out.append((m, path))
    return out


def main():
    diar_mod.MODEL_REPO = diar_mod.MODEL_NAME = "test/diar"
    embed_mod.MODEL_REPO = embed_mod.MODEL_NAME = "test/embed"

    sub_apps = [
        vad_mod.build_app(["vad"]),
        diar_mod.build_app(["diar"]),
        embed_mod.build_app(["speaker_embed"]),
    ]
    app = FastAPI(title="merge test")
    for sub in sub_apps:
        app.router.routes.extend(multi._split(sub))

    gpu.mount_metrics(app)
    contract.register(
        app,
        model_name="test/diar",
        module="pyannote_multi",
        served=["vad", "diar", "speaker_embed"],
        is_ready=lambda: False,
        error=lambda: None,
        repo="test/diar",
        task_api=True,
    )

    expected = [
        ("GET", "/v1/models"), ("GET", "/api/engine-spec"),
        ("GET", "/health"), ("GET", "/healthz"), ("GET", "/readyz"), ("GET", "/metrics"),
        ("GET", "/v1/tasks"), ("GET", "/v1/tasks/{tid}"), ("DELETE", "/v1/tasks/{tid}"),
        ("GET", "/v1/tasks/{tid}/result"),
        ("GET", "/v1/audio/tasks"), ("GET", "/v1/audio/tasks/{tid}"), ("DELETE", "/v1/audio/tasks/{tid}"),
        ("GET", "/v1/audio/tasks/{tid}/result"),
        ("POST", "/v1/audio/vad"), ("POST", "/v1/audio/diarization"),
        ("POST", "/v1/audio/embeddings"),
    ]
    pairs = method_paths(app)
    failures = []
    for mp in expected:
        n = pairs.count(mp)
        if n != 1:
            failures.append("%s %s: expected exactly 1 route, found %d" % (mp[0], mp[1], n))

    payload = None
    for r in app.routes:
        if isinstance(r, APIRoute) and getattr(r, "path", "") == "/api/engine-spec":
            payload = r.endpoint()
            break
    if payload is None:
        failures.append("engine-spec route not found")
        payload = {}
    served = payload.get("serves")
    if served != ["vad", "diar", "speaker_embed"]:
        failures.append("engine-spec serves=%r" % served)
    for cap_path in ("/v1/audio/vad", "/v1/audio/diarization", "/v1/audio/embeddings"):
        rows = [e for e in payload.get("endpoints", []) if e.get("path") == cap_path]
        if not rows or not rows[0].get("available"):
            failures.append("%s not advertised as available" % cap_path)

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print("OK: routes merged exactly once, engine-spec serves", served)


if __name__ == "__main__":
    main()
