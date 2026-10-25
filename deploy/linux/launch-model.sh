#!/bin/sh
set -eu
printf '%s  /models/model.gguf\n' "$MODEL_SHA256" | sha256sum -c -
echo MODEL_SHA256_VERIFIED
exec /app/llama-server --model /models/model.gguf --alias bitcoin-qwen-0.5b --host 0.0.0.0 --port 8080 --ctx-size 2048 --threads 1 --threads-batch 1 --parallel 1 --batch-size 128 --ubatch-size 128 --n-predict 256 --no-webui
