# THIS FORK of the stock wrapper: serve several modules of one base in a single
# process. The stock app.py picks matched[0] and warns that the rest "need separate
# models"; on the pyannote base that would drop all but one of vad / diar /
# speaker_embed.
#
# Design notes (measured against the stock wrapper):
# - All three loaders are small and run in daemon threads; nothing here needs the
#   main thread (no vLLM).
# - The stock build_app() already calls contract.register() and tasks.mount(); we
#   strip every reserved route from all sub-apps and call register() exactly ONCE
#   on the merged app, so /api/engine-spec advertises the full served set (Router
#   enforces that list) and the task API exists exactly once.
# - diarization is soft-fail: the HF repo can be gated (503 with the reason on
#   /v1/audio/diarization) and must never exit(70) the container; the watchdog
#   only covers vad + embedding.
import logging
import os
import threading

from fastapi import FastAPI

from . import contract, gpu, watchdog
from .caps import diar as diar_mod
from .caps import embed as embed_mod
from .caps import vad as vad_mod

log = logging.getLogger("audio-pyannote-multi")

# Everything build_app()/register()/tasks.mount() add. Stripped from every sub-app
# and re-added exactly once via the final register(task_api=True) + mount_metrics.
RESERVED = {
    "/v1/models", "/api/engine-spec", "/health", "/healthz", "/readyz", "/metrics",
    "/v1/tasks", "/v1/tasks/{tid}", "/v1/tasks/{tid}/result",
    "/v1/audio/tasks", "/v1/audio/tasks/{tid}", "/v1/audio/tasks/{tid}/result",
}

_MODULES = {"vad": vad_mod, "diar": diar_mod, "embed": embed_mod}


def _sources():
    raw = (os.environ.get("MODEL_SOURCE") or "").strip()
    return [s.strip() for s in raw.split(",") if s.strip()]


def _strip_hf(source):
    return source[5:] if source.startswith("hf://") else source


def _split(sub):
    keep = [r for r in sub.routes if getattr(r, "path", None) not in RESERVED]
    for r in list(sub.routes):
        if getattr(r, "path", None) in RESERVED:
            sub.routes.remove(r)
    return keep


def run(matched, supports):
    chosen = {}
    for caps, mod in matched:
        chosen.setdefault(mod, []).extend(c for c in caps if c in supports)

    # MODEL_SOURCE index 0 is the diarization pipeline, index 1 the embedding model
    # (caps/diar.py, caps/embed.py read their module globals inside _load()).
    srcs = _sources()
    if "diar" in chosen:
        if not srcs:
            log.error("MODEL_SOURCE lists no diarization repo; diar cannot load")
            raise SystemExit(1)
        diar_mod.MODEL_REPO = _strip_hf(srcs[0])
        diar_mod.MODEL_NAME = diar_mod.MODEL_REPO
    if "embed" in chosen:
        if len(srcs) < 2:
            log.error("MODEL_SOURCE lists no embedding repo as its second entry")
            raise SystemExit(1)
        embed_mod.MODEL_REPO = _strip_hf(srcs[1])
        embed_mod.MODEL_NAME = embed_mod.MODEL_REPO

    loaders = [(name, _MODULES[name]) for name in ("vad", "embed", "diar") if name in chosen]
    for name, mod in loaders:
        threading.Thread(target=mod._load, name="%s-load" % name, daemon=True).start()

    # Watchdog on the fast modules (vad + embedding) whenever they are served; a
    # gated or slow diarization only ever answers 503 with its own reason.
    fast = [mod for name, mod in loaders if name in ("vad", "embed")]
    if fast:
        watchdog.arm(
            lambda: all(m._state.get("ready") for m in fast),
            lambda: next((m._state.get("error") for m in fast if m._state.get("error")), None),
            "vad+embed",
        )
        ready_states = [m._state for m in fast]
    else:
        watchdog.arm(lambda: diar_mod._state.get("ready"),
                     lambda: diar_mod._state.get("error"), "diar")
        ready_states = [diar_mod._state]

    served = [c for c in supports if any(c in caps for caps, _ in matched)]

    app = FastAPI(title="audio-pyannote-multi (VAD + Diarization + Speaker embeddings)")
    for name, mod in loaders:
        app.router.routes.extend(_split(mod.build_app(chosen[name])))

    # Exactly one register(): card, spec and task API in one place.
    gpu.mount_metrics(app)
    contract.register(
        app,
        model_name=(os.environ.get("MODEL_NAME") or diar_mod.MODEL_NAME or embed_mod.MODEL_NAME),
        module="pyannote_multi",
        served=served,
        is_ready=lambda: all(s.get("ready") for s in ready_states),
        error=lambda: next((s.get("error") for s in ready_states if s.get("error")), None),
        repo=(diar_mod.MODEL_REPO or embed_mod.MODEL_REPO),
        task_api=True,
    )

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("ENGINE_PORT", "8000")),
        log_level=(os.environ.get("LOG_LEVEL") or "info").lower(),
    )
