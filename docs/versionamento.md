# Versionamento e portabilidade

## O que acompanha o clone

Código, configurações de exemplo, testes e documentação técnica ficam no Git. Também
acompanham o clone os protótipos acadêmicos em `docs/aps`, métricas/modelos auditados
em `artifacts/` e `.private/artifacts/`, além dos notebooks, runners e retornos
experimentais agregados auditados em `.private/colab/`. O nome `.private` não
torna esses dois subdiretórios sigilosos: somente os materiais aprovados para
versionamento são publicados ali.

Os pacotes de entrada do Colab continuam privados: mesmo com IDs pseudonimizados,
contêm observações individuais de partidas de terceiros, não apenas métricas
agregadas. Para repetir o treinamento é necessário fornecer o pacote privado de
entrada, preservando suas identidades de conteúdo. Não é necessário transportar
ZIPs incrementais para obter o código, documentação, modelos e retornos auditados.
Relatórios antigos descrevem a etapa em que foram produzidos; o estado
atual deve ser conferido no código, README e resultados consolidados.

## Exclusões mantidas

| Caminho ou categoria | Motivo |
| --- | --- |
| `.env`, `.env.*`, contas `riot-account*.json`, `.aws`, chaves privadas e certificados | Credenciais, identidade da conta e segredos de vínculo; configurar por máquina. `.env.example` continua público. |
| `.private/collection/`, `data/`, `.private/preview-real/` | Respostas brutas, partidas e dados de jogadores; podem conter PUUID, Riot ID e outros identificadores. |
| `.private/dataset/`, pacotes `riftlab-*-colab.zip` e mapas `bundle-map*.json` | Dados individuais de partidas, referências originais ou mapas reversíveis. Pseudonimização para treinamento remoto não é autorização para publicação do dataset no Git. |
| Demais arquivos de `.private/`, incluindo execução, conversas, configuração de conta, capturas e backups de transferência | Informações pessoais, histórico operacional e configuração da máquina; não são documentação pública de engenharia. |
| `.agents/`, `.codex/`, `.claude/`, AGENTS/CLAUDE/RTK locais e configurações do editor | Ferramentas, permissões e caminhos locais; não são necessários para executar o produto e podem expor configurações pessoais. |
| `node_modules`, `.venv`, caches, builds e logs | Gerados novamente por instalação/build; incluí-los aumenta conflitos e pode misturar plataformas ou expor mensagens de execução. |
| Páginas HTML baixadas e pastas `*_files` na raiz de `docs/` | Cópias de sites externos, potencialmente com estado do navegador; não são fontes próprias do projeto. Referências científicas e documentação autoral permanecem. |
| Diretórios extraídos dos pacotes Colab | Dados de entrada individuais e duplicatas regeneráveis, incluindo JSONs acima do limite de tamanho do GitHub. Retornos agregados/modelos auditados permanecem versionados. |
| Volumes PostgreSQL/Docker | Estado de execução, não arquivos do checkout; podem conter dados pessoais e precisam de backup/restauração separados quando necessários. |

Git não é sincronização do banco. Em outra máquina, é necessário configurar as
credenciais locais, aplicar migrações e importar dados autorizados, ou restaurar um
backup privado do banco. Arquivos excluídos não foram apagados desta máquina.

## Revisão antes de publicar

```bash
git add <arquivos-do-projeto>
python3 scripts/check_repository_content.py
git diff --cached --check
```

A auditoria lê o conteúdo staged, inclusive o conteúdo dos ZIPs, sem imprimir os
valores encontrados. Bloqueia padrões de tokens/chaves, valores sensíveis do `.env`
local, identificadores estruturados de jogadores/partidas e arquivos acima de
100 MiB. Não é uma prova de anonimização nem detecta todo segredo possível;
revisão semântica e de dados continua necessária. Não usar `git add -f` para
contornar as exclusões.

O GitHub [bloqueia arquivos maiores que 100 MiB](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
Arquivos grandes exigem uma estratégia própria; Git LFS não remove a necessidade
de revisão de privacidade e pode ter cotas/custos. Não há contratação de storage
externo nesta configuração.

Apagar um arquivo em um commit posterior ou adicioná-lo ao `.gitignore` não apaga
seu histórico. Material sensível nunca deve entrar no Git temporariamente; uma
exposição exige remoção apropriada do histórico e revogação dos segredos atingidos.
