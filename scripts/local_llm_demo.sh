#!/usr/bin/env bash
# Demonstração isolada com interpretador local opcional; nenhum volume é apagado.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="riftlab-llm-demo"
export RIFTLAB_LOCAL_MODEL_SOCKET_DIR="${RIFTLAB_LOCAL_MODEL_SOCKET_DIR:-$ROOT/.private/local-runtime/docker-socket}"
# Afeta somente este processo. O override local habilita o modelo explicitamente.
export RIFTLAB_OBJECTIVE_PARSER=rules
export RIFTLAB_OBJECTIVE_MODEL_URL=""
export RIFTLAB_OBJECTIVE_MODEL_VERSION=rules_v1
BASE=(docker compose -p "$PROJECT" -f "$ROOT/compose.yaml" -f "$ROOT/compose.demo.yaml")
MODEL=("${BASE[@]}" -f "$ROOT/compose.local-llm.yaml")

require_socket() {
  local socket="$RIFTLAB_LOCAL_MODEL_SOCKET_DIR/interpret.sock"
  if [ ! -S "$socket" ]; then
    echo "Inicie primeiro o modelo: scripts/local_llm_demo.sh model" >&2
    exit 1
  fi
  if [ "$(stat -c %u "$socket")" != 1000 ]; then
    echo "O socket precisa pertencer ao UID 1000, usado pela API na imagem Docker." >&2
    exit 1
  fi
  python3 - "$socket" <<'PY'
import http.client
import json
import socket
import sys

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(2)
    connection.connect(sys.argv[1])
    connection.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
    response = http.client.HTTPResponse(connection)
    response.begin()
    body = json.loads(response.read(4096))
if response.status != 200 or body.get("modelVersion") != "qwen2.5-1.5b-lora-v3-q4_k_m":
    raise SystemExit("Socket não corresponde ao modelo experimental esperado; API preservada")
PY
}

ports_free() {
  for port in 8000 5173; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      if [ -z "$("${BASE[@]}" ps --status running -q)" ]; then
        echo "Porta $port ocupada. Não será encerrado nenhum serviço de outro projeto." >&2
        exit 1
      fi
    fi
  done
}

case "${1:-}" in
  config)
    "${BASE[@]}" config --quiet
    "${MODEL[@]}" config --quiet
    echo "Configurações válidas; nenhum serviço foi iniciado."
    ;;
  build) "${BASE[@]}" build ;;
  baseline)
    ports_free
    "${BASE[@]}" up -d --no-build --wait db
    "${BASE[@]}" run --rm --no-deps api db upgrade
    "${BASE[@]}" up -d --no-build --wait api worker frontend
    python3 "$ROOT/scripts/demo_seed.py" --base-url http://127.0.0.1:8000
    "${BASE[@]}" exec -T api cc ranked demo-seed
    echo "Demonstração com regras pronta: http://127.0.0.1:5173"
    ;;
  model)
    manifest="${RIFTLAB_LOCAL_MODEL_MANIFEST:-$ROOT/.private/experiments/objective-local-v3/runtime-manifest-windows.json}"
    if [ ! -f "$manifest" ]; then
      echo "Defina RIFTLAB_LOCAL_MODEL_MANIFEST com o manifesto local dos pesos preparados." >&2
      exit 1
    fi
    exec python3 "$ROOT/ml/objectives/local/run.py" \
      --manifest "$manifest" \
      --output "$ROOT/.private/local-runtime/session-$(date +%Y%m%d-%H%M%S)-$$" \
      --unix-socket "$RIFTLAB_LOCAL_MODEL_SOCKET_DIR/interpret.sock" \
      --duration "${RIFTLAB_LOCAL_MODEL_DURATION:-1800}"
    ;;
  enable)
    require_socket
    "${MODEL[@]}" up -d --no-build --wait --no-deps api
    echo "API configurada com modelo experimental via socket Unix."
    ;;
  disable)
    "${BASE[@]}" up -d --no-build --wait --no-deps api
    echo "API voltou às regras; encerre o modelo com Ctrl+C no terminal dele."
    ;;
  check)
    python3 - <<'PY'
import json
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8000/health/ready", timeout=5) as response:
    if response.status != 200:
        raise SystemExit("API não está pronta")
for text in ("quero causar mais dano", "preciso de mana", "quero uma build equilibrada"):
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/objectives/interpret",
        json.dumps({"text": text}).encode(), {"Content-Type": "application/json"},
    )
    with opener.open(request, timeout=20) as response:
        body = json.load(response)
    print(json.dumps(body, ensure_ascii=False))
    if body["provider"] != "LOCAL_MODEL" or body["parserVersion"] != "qwen2.5-1.5b-lora-v3-q4_k_m":
        raise SystemExit("API não está usando o modelo experimental; execute enable")
    if body.get("reasonCodes"):
        raise SystemExit("Revisar interpretação/fallback antes de seguir para a interface")
PY
    ;;
  status) "${BASE[@]}" ps ;;
  down) "${BASE[@]}" down ;;
  *)
    echo "Uso: $0 {config|build|baseline|model|enable|check|disable|status|down}"
    exit 1
    ;;
esac
