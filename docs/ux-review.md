# Revisão de UX — jornadas e protótipo (CC-102)

Consolida UC01–06 ([requisitos](requirements.md)) em relação ao protótipo navegável de
`frontend/src/features/review/`. Vínculos: rotas reais futuras em
[arquitetura](architecture.md); regras do modo Estudo em [learning-design](learning-design.md).
Documento técnico local, não copiado ao README público.

## Legenda de fidelidade

- **Prototipado**: existe como tela navegável com dados ilustrativos, sem chamada real a
  Riot/API/modelo.
- **Apenas especificado**: comportamento descrito neste documento e/ou no card, sem tela
  interativa nesta etapa (esquema textual quando ajuda a navegar a jornada).
- **Funcional**: ligado a dado/API real. Nenhum item desta lista é funcional ainda —
  fica para CC-2xx/3xx/5xx.

## Matriz por caso de uso

| UC | RF | Fidelidade nesta etapa | Tela(s) | Estados cobertos |
| --- | --- | --- | --- | --- |
| UC01 Importar partida | RF01–03 | Prototipado (simulado) | EntradaScreen, ProgressoScreen | campo vazio, ID inválido, carregando, falha recuperável com "tentar novamente", sucesso, partida em andamento recusada (RF17) |
| UC02 Revisar compra | RF04–08 | Prototipado (simulado) | DecisoesScreen, ContextoScreen, ComparacaoScreen | lista vazia, cenário fora do recorte (OUT_OF_SCOPE), inventário incerto (INVENTORY_UNCERTAIN), ouro desconhecido/estimado/desatualizado (STALE_FRAME), suporte limitado (SMALL_CONTEXT_SAMPLE), sem recomendação disponível |
| UC03 Informar objetivo | RF09 | Prototipado parcialmente (só formulário fechado) | ContextoScreen | 4 opções fechadas (BALANCED/SURVIVAL/DAMAGE/UTILITY); texto livre + confirmação de interpretação (NEEDS_INPUT) ficam para CC-503/504 |
| UC04 Avaliar e exportar | RF10 | Apenas especificado | AvaliacaoEsquemaScreen (esquema não interativo) | lista textual de estados (vazio, erro, retorno); nenhuma interação real, sem formulário de nota nem exportação real |
| UC05 Operar e remover dados | RF11–12 | Apenas especificado | OperacaoEsquemaScreen (esquema não interativo) | lista textual de estados (vazio, confirmação de expurgo, erro); nenhuma interação real |
| UC06 Estudar decisão | RF13–16 | Prototipado | ContextoScreen, EscolhaScreen, ComparacaoScreen, TransferenciaScreen | ordem neutra estável por tentativa, glossário, duas pistas graduais (ameaças; custo/atributos), "não sei" sem penalidade, reveal explícito e monotônico, revisão direta (reveal sem responder), comparação sem gabarito universal, transferência independente sem pistas |

## Checklist de clareza por etapa

| Etapa | Estado mostrado | Limitação comunicada em texto | Próxima ação | Retorno |
| --- | --- | --- | --- | --- |
| Entrada | vazio / ID inválido / partida em andamento | sim (aviso `QualityNotice`, nunca só cor) | abrir demo, simular cenário, ver esquemas | — |
| Progresso | carregando / sucesso / falha | sim (`UPSTREAM_UNAVAILABLE`, ilustrativo) | continuar, tentar novamente | voltar à entrada |
| Decisões | lista normal / vazia / cenário especial | sim (`OUT_OF_SCOPE`, `INVENTORY_UNCERTAIN`) | selecionar decisão | voltar à entrada |
| Contexto | orçamento UNKNOWN/FRAME_ESTIMATE/stale, suporte LIMITED | sim | escolher objetivo e continuar | voltar às decisões |
| Escolha | pistas fechadas por padrão | sim (rótulos fixos "Pista 1"/"Pista 2") | responder ou "não sei", ou revisão direta | voltar às decisões (perde a tentativa atual) |
| Comparação | AVAILABLE/FULL, LIMITED, UNSUPPORTED | sim, nunca trata compra histórica/top-1 como gabarito | nova tentativa, continuar para transferência | sem "voltar" (reveal é monotônico) |
| Transferência | sem pistas | sim | ver comentário breve | ver outras decisões, reiniciar protótipo |
| Avaliação/Operação (esquema) | não interativo | sim ("Esquema — não interativo nesta etapa") | — | voltar à entrada |

## Checklist de responsividade (360 px e 1280 px)

Capturado com Chromium via Playwright, servidor de desenvolvimento real
(`npm run dev`), sem mocks de CSS. Path: `.private/validation/sprint-1/CC-102/screens/{360,1280}/`.
25 estados capturados em cada largura (50 imagens); todas inspecionadas manualmente pelo
agente executor nesta etapa (não é teste de usabilidade com pessoas reais). Nenhum corte,
sobreposição ou overflow horizontal encontrado; grade de decisões/comparação muda de 1
coluna (360 px) para múltiplas colunas (≥720 px, breakpoint em `review.css`).

## Checklist de acessibilidade

- Percurso completo por teclado (Tab/Enter/Espaço) testado automaticamente:
  `frontend/tests/e2e/review-prototype.spec.ts` → describe "Navegação por teclado".
- Foco movido para o `<h2>` de cada nova etapa (`StepHeading`, `tabIndex=-1`), sem
  armadilha de foco.
- `fieldset`/`legend` em toda escolha de opção única; `<input type="radio">` nativo.
- `<details>/<summary>` nativos para pistas e glossário — operáveis por teclado sem JS de
  abrir/fechar.
- Estados nunca dependem só de cor: todo aviso (`QualityNotice`) tem prefixo textual fixo
  ("Informação:", "Atenção:", "Indisponível:") além da cor de fundo.
- Paleta reaproveita os tokens de contraste definidos em CC-101 (`App.css`); contraste não
  foi medido com ferramenta automatizada (axe/Lighthouse) nesta etapa — limite explícito
  abaixo.

## Limites explícitos

- Avaliação é inspeção técnica do próprio agente executor; não há teste de usabilidade com
  usuários reais nem piloto pedagógico (isso é CC-602, distinto desta entrega).
- Contraste WCAG AA não foi medido com ferramenta automatizada nesta etapa, só inspeção
  visual manual das capturas reais.
- Simulação client-side (banner "Protótipo") não é proteção de contrato/API; a barreira
  real de contrato do modo Estudo (rotas `learning-attempts`) é entregue em CC-304.
- Dados de apresentação (`data/examples.ts`) são inventados de propósito; não são
  catálogo validado, fixture do pipeline (CC-104) nem recorte real (CC-105).
- UC04/UC05 ficam apenas como esquema textual não interativo; nenhum botão simula
  submissão real de feedback ou expurgo.
