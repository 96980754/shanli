#!/usr/bin/env bash
# 本地 GPU 推理服务：bge-m3 嵌入 + bge-reranker-v2-m3 精排（OpenAI 兼容）
# 用法: ./run.sh   （需先 build：docker build -t local-inference .）
set -euo pipefail

IMG=local-inference
NET=yuxi-know_app-network
HOST_PORT=19997
CACHE_DIR="$(cd "$(dirname "$0")/../../.." && pwd)/.hf_cache"

docker build -t "$IMG" "$(dirname "$0")"

mkdir -p "$CACHE_DIR"
docker rm -f local-inference >/dev/null 2>&1 || true

docker run -d --name local-inference \
  --gpus all \
  --network "$NET" \
  -p "$HOST_PORT:8000" \
  -v "$CACHE_DIR:/root/.cache/huggingface" \
  -e HF_ENDPOINT=https://hf-mirror.com \
  "$IMG"

echo "started local-inference on port $HOST_PORT"
docker logs -f local-inference
