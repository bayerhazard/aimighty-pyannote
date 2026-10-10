# AIM fork of the stock Olares pyannote audio engine (docker.io/beclab/audio-pyannote).
# Same base image, wrapper replaced by docker/wrapper (multi-module: vad + diar +
# speaker_embed in one CPU-only process). Build context = this directory:
#   docker buildx build --platform linux/amd64 -t ghcr.io/bayerhazard/audio-pyannote:multi-v1 --push .
FROM docker.io/beclab/audio-pyannote:v1.1.13
COPY wrapper/ /app/wrapper/
