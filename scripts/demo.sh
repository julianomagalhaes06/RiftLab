#!/usr/bin/env bash
# Demonstração local reproduzível do RiftLab (CC-605).
#
# Tudo roda no projeto Docker próprio "riftlab-demo", com volumes separados: o reset
# apaga só a demonstração, nunca o banco de desenvolvimento nem dados reais. Sem chave da
# Riot e sem segredo (compose.demo.yaml): nada consulta a Riot nem o Data Dragon.
#
#   scripts/demo.sh up              constrói, sobe, migra e semeia a demonstração
#   scripts/demo.sh parser-offline  reinicia a API com o interpretador local sem serviço
#   scripts/demo.sh parser-rules    volta ao interpretador por regras (padrão)
#   scripts/demo.sh reset           apaga a demonstração (só ela) e sobe de novo
#   scripts/demo.sh down            derruba a demonstração e apaga seus volumes
#   scripts/demo.sh status          mostra os serviços da demonstração
set -euo pipefail

PROJECT="riftlab-demo"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -p "$PROJECT" -f "$ROOT/compose.yaml" -f "$ROOT/compose.demo.yaml")

if [ -n "${COMPOSE_PROJECT_NAME:-}" ] && [ "${COMPOSE_PROJECT_NAME}" != "$PROJECT" ]; then
  echo "Recusado: COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME} não é a demonstração." >&2
  exit 1
fi

ports_free() {
  for port in 8000 5173; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      if ! "${COMPOSE[@]}" ps --status running -q | grep -q .; then
        echo "A porta $port já está em uso (outra stack do RiftLab?). Pare-a antes: docker compose down" >&2
        exit 1
      fi
    fi
  done
}

up() {
  ports_free
  "${COMPOSE[@]}" build
  "${COMPOSE[@]}" up -d --wait
  "${COMPOSE[@]}" run --rm api db upgrade
  python3 "$ROOT/scripts/demo_seed.py" --base-url http://127.0.0.1:8000
  "${COMPOSE[@]}" exec -T api cc ranked demo-seed
  echo
  echo "Demonstração pronta em http://127.0.0.1:5173 (API em http://127.0.0.1:8000)."
}

restart_api() {
  RIFTLAB_OBJECTIVE_PARSER="$1" RIFTLAB_OBJECTIVE_MODEL_URL="${2:-}" \
    RIFTLAB_OBJECTIVE_MODEL_VERSION="${3:-local_model_unversioned}" \
    "${COMPOSE[@]}" up -d --wait --no-deps api
}

case "${1:-}" in
  up) up ;;
  parser-offline)
    # Porta sem serviço no próprio contêiner: o interpretador local "cai" e a tela
    # devolve ao formulário com o motivo.
    restart_api local_model http://127.0.0.1:9/interpret qwen2.5-1.5b-lora-v1
    echo "Interpretador local configurado, mas sem serviço: o texto cai no formulário."
    ;;
  parser-rules) restart_api rules && echo "Interpretador por regras (padrão)." ;;
  reset)
    "${COMPOSE[@]}" down -v
    up
    ;;
  down) "${COMPOSE[@]}" down -v ;;
  status) "${COMPOSE[@]}" ps ;;
  *)
    sed -n '2,15p' "$0"
    exit 1
    ;;
esac
