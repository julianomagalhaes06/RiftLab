# Frontend (React + TypeScript + Vite)

Interface do RiftLab: importar uma partida concluída, acompanhar a preparação e revisar as
decisões de compra. Fala só com a API do RiftLab (`/api/v1/*`, `/health/*`), por proxy do
servidor de desenvolvimento/`preview` (`RIFTLAB_API_PROXY_TARGET`, padrão `http://127.0.0.1:8000`).

## Estrutura

- `src/shared/api/`: cliente único da API. Os tipos vêm do OpenAPI (`types.ts`, gerado por
  `npm run generate:api-types` a partir de `../backend/openapi.json`; `check:api-types` acusa
  divergência). `errors.ts` converte respostas `application/problem+json` em `ApiError
  {status, code, requestId}` e `messages.ts` é a tabela única código → texto em português,
  sem segredo nem corpo remoto.
- `src/shared/router/`: roteamento por History API (sem dependência): `/`, `/imports/:jobId`,
  `/matches/:matchId?participant=N`, `/decisions/:decisionId`, `/prototipo`. O servidor devolve
  `index.html` para esses caminhos (`appType: "spa"` em `vite.config.ts`), então recarregar e
  abrir o endereço direto funcionam.
- `src/shared/hooks/`: polling com backoff, teto, `retryAt` e `AbortController`
  (`poller.ts`, `usePolling.ts`) e lista paginada por cursor (`usePagedList.ts`).
- `src/features/imports/` (entrada, lista de partidas, progresso da importação),
  `src/features/matches/` (partida, participantes, decisões, detalhe) e `src/features/review/`
  (protótipo ilustrativo de baixa fidelidade do CC-102, com dados fictícios, em `/prototipo`).

## Scripts

```sh
npm run dev               # servidor de desenvolvimento (http://127.0.0.1:5173)
npm run typecheck         # aplicação + specs e2e
npm run lint
npm test                  # Vitest (jsdom; fetch mockado)
npm run build             # tsc -b && vite build
npm run test:e2e          # Playwright com API simulada (sobe `vite preview` sozinho)
E2E_BASE_URL=http://127.0.0.1:5173 npm run test:e2e:stack   # Playwright contra a stack Docker real
```

O projeto `chromium` do Playwright cobre falhas recuperáveis, 360 px sem rolagem horizontal,
fluxo só com teclado, o health e o protótipo. O projeto `real-stack` percorre a demonstração
inteira (importar, preparar, participante, decisões, detalhe, recarregar e voltar) contra a
stack `docker compose` com as migrations aplicadas; ele exige `E2E_BASE_URL` e falha sem ela.
