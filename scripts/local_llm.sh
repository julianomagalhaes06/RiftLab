#!/usr/bin/env bash
# LLM experimental com a base normal existente. Não semeia, restaura ou migra bancos.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export RIFTLAB_LOCAL_MODEL_SOCKET_DIR="${RIFTLAB_LOCAL_MODEL_SOCKET_DIR:-$ROOT/.private/local-runtime/docker-socket}"
export RIFTLAB_OBJECTIVE_PARSER=rules
export RIFTLAB_OBJECTIVE_MODEL_URL=""
export RIFTLAB_OBJECTIVE_MODEL_VERSION=rules_v1
BASE=(docker compose -p riftlab -f "$ROOT/compose.yaml")
override="${RIFTLAB_NORMAL_COMPOSE_OVERRIDE:-$ROOT/.private/compose.normal.local.yaml}"
if [ -n "${RIFTLAB_NORMAL_COMPOSE_OVERRIDE:-}" ] && [ ! -f "$override" ]; then
  echo "Override informado não encontrado: $override" >&2
  exit 1
fi
if [ -f "$override" ]; then BASE+=(-f "$override"); fi
if [ -f "$ROOT/.private/compose.account.yaml" ]; then BASE+=(-f "$ROOT/.private/compose.account.yaml"); fi
MODEL=("${BASE[@]}" -f "$ROOT/compose.local-llm.yaml")
case "${1:-}" in
  config)
    "${MODEL[@]}" config --quiet
    echo "Configurações válidas; nenhum serviço foi iniciado."
    ;;
  build) "${BASE[@]}" build ;;
  model)
    exec python3 "$ROOT/ml/objectives/local/run.py" \
      --manifest "${RIFTLAB_LOCAL_MODEL_MANIFEST:-$ROOT/.private/experiments/objective-local-v3/runtime-manifest-windows.json}" \
      --output "$ROOT/.private/local-runtime/normal-$(date +%Y%m%d-%H%M%S)-$$" \
      --unix-socket "$RIFTLAB_LOCAL_MODEL_SOCKET_DIR/interpret.sock" \
      --duration "${RIFTLAB_LOCAL_MODEL_DURATION:-3600}"
    ;;
  up)
    if [ ! -S "$RIFTLAB_LOCAL_MODEL_SOCKET_DIR/interpret.sock" ]; then
      echo "Inicie scripts/local_llm.sh model em outro terminal e aguarde o modelo ficar pronto." >&2
      exit 1
    fi
    "${MODEL[@]}" up -d --no-build --wait
    ;;
  check)
    python3 - <<'PY'
import json, urllib.request
request = urllib.request.Request("http://127.0.0.1:8000/api/v1/objectives/interpret",
    json.dumps({"text": "quero sobreviver mais"}).encode(), {"Content-Type": "application/json"})
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=20) as response:
    body = json.load(response)
if body.get("provider") != "LOCAL_MODEL" or (body.get("objective") or {}).get("priority") != "SURVIVAL":
    raise SystemExit("Modelo indisponível ou resposta inesperada; conferir logs e memória")
print(json.dumps(body, ensure_ascii=False))
PY
    ;;
  rules) "${BASE[@]}" up -d --no-build --wait --no-deps api ;;
  status) "${MODEL[@]}" ps ;;
  down) "${MODEL[@]}" down ;;
  *) echo "Uso: $0 {config|build|model|up|check|rules|status|down}"; exit 1 ;;
esac
