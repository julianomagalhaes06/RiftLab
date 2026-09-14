# riftlab (backend)

Monólito FastAPI do RiftLab. Consulte o README público na raiz do repositório para o
contexto do produto. Esta etapa cobre a fundação técnica (CC-101: configuração tipada,
health checks, worker mínimo, entrypoint `cc`) e a persistência inicial (CC-103:
migrations Alembic, fronteiras de domínio/repositórios para catálogo, partidas,
participantes, eventos, jobs de importação e estados de decisão). Importação real,
reconstrução temporal, ranking e análises chegam em sprints posteriores.

## Migrações

```sh
uv run alembic upgrade head        # aplica migrations pendentes (idempotente)
uv run cc db upgrade                # equivalente via entrypoint cc
uv run cc db current                # mostra a revisão atual do banco configurado
```

Requer `RIFTLAB_DATABASE_URL` apontando para um PostgreSQL real (SQLite não é
suportado). Em produção/dev via Compose: `docker compose run --rm api cc db upgrade`.
