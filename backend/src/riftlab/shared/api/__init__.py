"""Schemas Pydantic dos contratos de aplicacao (docs/contracts.md), CC-106.

Nenhum modulo aqui monta rotas FastAPI: sao os contratos de entrada/saida dos
endpoints previstos para sprints futuras (S2-S6), usados para validar
exemplos e travar o formato antes da implementacao real dos servicos. Os
unicos endpoints HTTP montados hoje sao os de saude
(riftlab.api.routes.health) e nao dependem destes schemas. Import deste
pacote nao deve, portanto, ser interpretado como "endpoint existe" -- ver
docs/contracts.md para a matriz de estado por rota.
"""
