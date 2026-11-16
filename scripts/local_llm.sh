#!/usr/bin/env bash
# LLM experimental com a base normal existente. Não semeia, restaura ou migra bancos.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export RIFTLAB_LOCAL_MODEL_SOCKET_DIR="${RIFTLAB_LOCAL_MODEL_SOCKET_DIR:-$ROOT/.private/local-runtime/docker-socket}"
# A API usa o UID de quem inicia o modelo: o socket é 0600 desse usuário.
export RIFTLAB_LOCAL_MODEL_UID="${RIFTLAB_LOCAL_MODEL_UID:-$(id -u)}"
export RIFTLAB_LOCAL_MODEL_GID="${RIFTLAB_LOCAL_MODEL_GID:-$(id -g)}"
# WSL usa o modelo no Windows (RX 7600); Linux nativo, o llama-server na CPU do host.
if grep -qi microsoft /proc/version 2>/dev/null; then
  DEFAULT_MANIFEST="$ROOT/.private/experiments/objective-local-v3/runtime-manifest-windows.json"
else
  DEFAULT_MANIFEST="$ROOT/.private/experiments/objective-local-v3/runtime-manifest-linux.json"
fi
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
      --manifest "${RIFTLAB_LOCAL_MODEL_MANIFEST:-$DEFAULT_MANIFEST}" \
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
    # Dentro do contêiner da API, com a mesma montagem da rota POST /objectives/interpret (que
    # exige conta desde as contas locais): exercita o socket montado, o sidecar e o modelo.
    "${MODEL[@]}" exec -T api python - <<'PY'
import asyncio, json
from riftlab.config import Settings
from riftlab.objectives.application import build_parser, interpret_objective
settings = Settings()
parser = build_parser(settings.objective_parser, settings.objective_model_url,
    settings.objective_model_version, settings.objective_model_socket_path)
result = asyncio.run(interpret_objective(parser, "quero sobreviver mais",
    settings.objective_parser_timeout_seconds))
body = result.as_payload()
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
