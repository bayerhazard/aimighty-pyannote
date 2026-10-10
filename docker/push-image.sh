#!/bin/sh
# Build and publish the AIM engine fork WITHOUT a Docker daemon, using crane
# (go-containerregistry: https://github.com/google/go-containerregistry).
#
# Why not the Gitea/GitHub Actions workflow (removed in this commit): the
# olares-runner executes jobs inside containers that get no docker socket, so
# docker/setup-buildx-action and docker/build-push-action fail with
# "Cannot connect to the Docker daemon at unix:///var/run/docker.sock".
#
# What this does: appends docker/wrapper/ as ONE extra layer on top of the stock
# beclab image (same paths, so it replaces /app/wrapper) and pushes the result.
# The appended layer also carries opaque whiteouts for the stale __pycache__
# directories of the base image.
#
# Usage:
#   docker/push-image.sh [destination] [base-image]
# Defaults: destination ghcr.io/bayerhazard/audio-pyannote:multi-v1,
#           base docker.io/beclab/audio-pyannote:v1.1.13 (the version the chart pins).
#
# Requirements: crane on PATH and an authenticated session for the destination
# registry (e.g. `crane auth login ghcr.io -u <user>`). NOTE: a freshly pushed
# ghcr package is PRIVATE — set its visibility to public in the GitHub UI
# (profile -> Packages -> <package> -> Package settings), otherwise the Olares
# node cannot pull it (anonymous token -> 401).
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEST=${1:-ghcr.io/bayerhazard/audio-pyannote:multi-v1}
BASE=${2:-docker.io/beclab/audio-pyannote:v1.1.13}

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/app"
cp -r "$HERE/wrapper" "$STAGE/app/wrapper"
find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
mkdir -p "$STAGE/app/wrapper/__pycache__" "$STAGE/app/wrapper/caps/__pycache__"
: > "$STAGE/app/wrapper/__pycache__/.wh..wh..opq"
: > "$STAGE/app/wrapper/caps/__pycache__/.wh..wh..opq"

tar cf "$STAGE/layer.tar" -C "$STAGE" app
crane append -b "$BASE" -f "$STAGE/layer.tar" -t "$DEST"
echo "pushed $DEST (layer on top of $BASE)"
