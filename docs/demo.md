# Roteiro de defesa e demonstração

Duração-alvo: 15 a 20 minutos, com 5 de perguntas. Antes de começar, preparar a máquina:

```sh
docker compose down            # libera as portas, se a stack de desenvolvimento estiver no ar
scripts/demo.sh up             # ~1–2 min com cache; abre em http://127.0.0.1:5173
```

Tudo o que aparece na tela é sintético e marcado como DEMO. Nenhuma chamada sai da máquina.

## 1. Problema (2 min)

- Builds prontas descrevem o que é frequente, não o que a partida pedia. Para um iniciante, o
  difícil é **ler o contexto** antes de comprar: ameaças, componentes no inventário, ouro e
  etapa da build.
- Proposta: revisar, depois da partida, um ponto de compra reconstruído. A pessoa escolhe
  **antes** de ver a comparação, com pistas, e depois reflete.
- O que o sistema **não** promete: dizer a "melhor compra", prever vitória ou acompanhar partida
  ao vivo.

## 2. Arquitetura (3 min)

- Monólito modular em Python (FastAPI, PostgreSQL), worker com fila durável (importação,
  preparação, expurgo) e interface React. Diagrama em `architecture.md` → Componentes.
- Reconstrução temporal com proveniência:
  - o estado anterior à compra nunca inclui o item comprado;
  - a incerteza vira motivo explícito, nunca valor inventado.
- Candidatos só do catálogo versionado do patch. Critério identificado na tela: popularidade
  ou regras explícitas, com suporte e abstenção.
- Decisões que mudaram o plano (ADRs 0002–0004):
  - critério servido = popularidade;
  - interpretador por regras;
  - expurgo com lápide.

## 3. Demonstração (7 min)

1. **Painel → Histórico.** As partidas aparecem marcadas DEMO. **Minha posição** mostra o elo
   fictício, com o aviso de que não foi consultado na Riot.
2. **Contexto A (DEMO-0009).** Histórico → partida → participante 1 → decisão.
   - Mostrar o inventário (duas espadas sintéticas) e o orçamento aproximado.
   - Escolher o objetivo pelo formulário e responder "Não sei".
   - Revelar: aparecem Arco Veloz, Lâmina Crítica e Coroa. Cada justificativa cita o campo de
     origem, e a compra da partida aparece rotulada, sem ser gabarito.
3. **Contexto B (DEMO-0010).** O mesmo campeão, agora com colete e gema. Revelar: aparecem
   Couraça, Véu e Escudo. **O contexto mudou as alternativas** pela regra explícita de
   aproveitar componentes e custo restante.
4. **Abstenção (DEMO-0011).** A decisão aparece como "Inelegível para análise". O inventário
   ficou incerto porque o jogador destruiu um item que não tinha; não há alternativas
   inventadas.
5. **Objetivo por texto.** Digitar "não quero dano, quero aguentar mais", interpretar,
   confirmar e reanalisar: a nova análise fica separada da anterior.
   - Depois, rodar `scripts/demo.sh parser-offline`, digitar de novo e mostrar o "interpretador
     não respondeu", com o formulário seguindo.
   - Rodar `scripts/demo.sh parser-rules` para voltar.
6. **Reflexão, transferência e relatório.**
   - Registrar uma reflexão.
   - Baixar o HTML e rodar `python3 scripts/verify_report.py relatorio.html`.
   - Mostrar o relatório abrindo sem rede.
7. **Remoção.** Em DEMO-0008, "Remover esta partida": prévia, limites declarados e confirmação
   digitando o id.

## 4. Evidência (4 min)

Números em `results.md`, todos conferidos contra os manifestos:

- **Imitação:**
  - No instante da compra, o CatBoost chega a MRR 0,975, mas o ganho vem do ouro.
  - No planejamento, sem o ouro, ele empata com a popularidade (0,761 × 0,760; IC sobrepostos).
- **Clusters:** silhueta ~0,08, descartados.
- **Desempenho:** suporte com cobertura 1,0; ranking com p95 de 2,5 ms em CPU.
- **Interpretador:**
  - O LoRA do 1.5B chega a macro-F1 0,934, mas pede esclarecimento menos que as regras e seguiu
    uma instrução embutida. Reprovado no gate registrado antes; as regras (0,918) ficam como
    padrão.
  - Revisão humana às cegas dos rótulos.
- **Engenharia:**
  - CI com ~1.400 testes de backend, 177 de componentes e 66 de interface, além da stack real.
  - Testes impedidos de acessar rede.
  - Demonstração ensaiada a partir de um clone sem materiais privados.
- **Pertinência estratégica:** revisão de especialistas em andamento (CC-602). Até lá, o
  projeto afirma só imitação e desempenho técnico.

## 5. Limites (2 min)

- Amostra pequena e correlacionada por jogador, um patch, um recorte, coleta de conveniência.
- Imitação não é pertinência; o piloto pedagógico não foi feito.
- Regras empíricas de inventário são hipóteses confirmadas num patch, não semântica oficial.
- Remover dados não desfaz o aprendizado de modelos treinados.
- O projeto é acadêmico e local: nada é publicado a jogadores sem os registros e avisos da Riot.

## Perguntas prováveis

- **"Por que não usar o modelo, se ele tem MRR 0,975?"** Porque esse número vem do ouro do
  instante da compra. Sem ele, o modelo empata com a popularidade, e servir o modelo seria
  vender imitação do momento como estratégia.
- **"O interpretador ajustado é pior?"** Ele acerta mais classes, mas erra no caso perigoso:
  dá prioridade com confiança quando deveria pedir esclarecimento. Custa ~10 s e ~9 GB por frase
  na CPU de referência.
- **"Como saber se as alternativas são boas?"** Pela revisão cega de especialistas: kappa
  ponderado, nota por origem e controle sorteado. A ferramenta está pronta; os resultados entram
  em `results.md` §6 quando as notas chegarem.
- **"E a privacidade?"**
  - Identificadores de jogador ficam só como HMAC.
  - O relatório sai sem id de partida nem de jogador.
  - O expurgo deixa só uma lápide com HMAC.
  - Logs sem id; portas só em loopback.
