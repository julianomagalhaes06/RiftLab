# Protótipo competitivo do RiftLab

Abra `index.html` diretamente no navegador. Para conferir em servidor local: `python3 -m http.server 8000 --directory docs/aps/prototipo-competitivo` e visite `http://127.0.0.1:8000/`. Não há build, backend, chave Riot, telemetria nem dependências JavaScript externas.

As abas **Painel**, **Campeões**, **Ranking** e **Histórico** navegam por hash. Cards de campeão selecionam o guia, filtros alteram listas, busca filtra histórico, botões de contexto abrem um diálogo e tooltips funcionam por mouse e teclado. Animação de entrada, traço do gráfico, barras de progresso, hovers e diálogo respeitam `prefers-reduced-motion`.

Números, nomes de perfis, partidas, ranking, builds e matchups são **ilustrativos**. O backend atual do RiftLab analisa partidas concluídas e decisões de compra; não produz ranking competitivo, taxa de vitória por perfil, guias validados ou replays. As telas demonstram uma direção de produto, sem afirmar essas funcionalidades como entregues.

Retratos de Kai'Sa, Caitlyn e Ezreal vieram do Data Dragon da Riot (`16.17.1`, imagens quadradas). Ao integrar no produto, resolver `championId` pelo `key` de `champion.json` para a versão fixada ao patch, com fallback textual. Os cards locais [CC-208](../../../.private/cards/CC-208.md) e [CC-307](../../../.private/cards/CC-307.md) descrevem essa integração e a análise por campeão.

Para personalizar, altere os tokens em `styles.css` e os dados sintéticos em `app.js`. Mantenha o aviso de simulação enquanto as métricas não vierem de APIs/experimentos validados. Não copie marca, textos ou layout dos arquivos de referência; a referência aqui foi hierarquia de informações, não aparência.

## Design escolhido

O design adotado para o projeto está em [`direcao-c-arena/`](direcao-c-arena/README.md): pranchas SVG de desktop e mobile geradas por `gerar.py` e a imagem de entrega (Painel desktop) em `direcao-c-arena/entrega/`. As pranchas das direções anteriores (A e B) foram removidas (estão na lixeira do sistema). Este protótipo interativo (`index.html`, `app.js`, `styles.css`) ainda usa o visual antigo e fica como referência de funcionamento até a nova interface ser implementada.
