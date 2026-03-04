#!/usr/bin/env bash
set -euo pipefail

IMAGE="cmaguranis/servarr-webhooks"
TAG="${1:-latest}"

# Ensure multiarch builder exists
if ! docker buildx inspect multiarch &>/dev/null; then
  docker buildx create --name multiarch --use
else
  docker buildx use multiarch
fi

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${TAG}" \
  --push \
  .

echo "Pushed ${IMAGE}:${TAG}"
