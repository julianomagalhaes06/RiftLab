# Referências técnicas

Consultadas em 14/09/2026. Revalidar políticas, schemas e versões ao iniciar integração.

- [League of Legends: APIs, roteamento e Data Dragon](https://developer.riotgames.com/docs/lol).
- [Portal Riot: acesso e limites](https://developer.riotgames.com/docs/portal).
- [Referência dos endpoints Riot](https://developer.riotgames.com/apis/).
- [Recomendação contextual de itens em MOBA](https://arxiv.org/abs/2007.15236).
- [CatBoost: objetivos e métricas de ranking](https://catboost.ai/docs/en/concepts/loss-functions-ranking).
- [PEFT: quantização e ajuste com adaptadores](https://huggingface.co/docs/peft/developer_guides/quantization).
- [AMD: matrizes de compatibilidade](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibility.html).
- [llama.cpp: backends de inferência](https://github.com/ggml-org/llama.cpp).

## Métodos usados nos resultados (13/10/2026)

- Prokhorenkova, L. et al. *CatBoost: unbiased boosting with categorical features*. NeurIPS, 2018. [arXiv:1706.09516](https://arxiv.org/abs/1706.09516).
- Hu, E. J. et al. *LoRA: Low-Rank Adaptation of Large Language Models*. 2021. [arXiv:2106.09685](https://arxiv.org/abs/2106.09685).
- Qwen Team. *Qwen2.5 Technical Report*. 2024. [arXiv:2412.15115](https://arxiv.org/abs/2412.15115).
- Efron, B.; Tibshirani, R. *An Introduction to the Bootstrap*. Chapman & Hall, 1993.
- Cohen, J. Weighted kappa: nominal scale agreement with provision for scaled disagreement or partial credit. *Psychological Bulletin*, 70(4), 1968.
- McNemar, Q. Note on the sampling error of the difference between correlated proportions or percentages. *Psychometrika*, 12(2), 1947.
- Rousseeuw, P. J. Silhouettes: a graphical aid to the interpretation and validation of cluster analysis. *Journal of Computational and Applied Mathematics*, 20, 1987.

## Trabalhos relacionados

- [LoL Math — Item OP](https://lolmath.net/) (consultado em 30/09/2026; metodologia em [About](https://lolmath.net/info/about/)). Otimizador teórico de builds pré-partida: testa combinações de itens e ordena por custo-eficiência a partir das fórmulas do jogo, com nota ponderada de dano (DPS 25%, burst 25%, all-out 50%), durabilidade (tempo esperado de sobrevivência contra a composição de dano inimiga) e utilidade de peso subjetivo definido pelo usuário. Entradas: campeão, runas, aliados/inimigos e patch; não usa partidas reais nem estado de jogo (inventário, ouro disponível, momento), e se declara ferramenta de theorycrafting, não preditor de desempenho. Contraste com o RiftLab: abordagem prescritiva e simulada da build completa versus revisão empírica e pós-partida de um ponto de decisão reconstruído, com custo restante, incerteza explícita e objetivo pedagógico. Candidato a inspiração para um baseline determinístico de custo-eficiência (Sprint 3), não a fonte de verdade. Fórmulas internas não são publicadas; a leitura foi limitada ao texto público do site.

Decisões de escopo, limiares e quantidades de amostras são propostas deste projeto, não recomendações atribuídas a essas fontes.
