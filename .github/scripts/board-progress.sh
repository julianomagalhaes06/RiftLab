#!/usr/bin/env bash
# Automação do board RiftLab. Dois modos:
#   board-progress.sh commits <arquivo com mensagens>  "refs #N" leva o card N para "Em andamento"
#                                                      (não mexe em quem já está lá ou concluído).
#   board-progress.sh issue <N> <opened|reopened|closed>  card da issue N vai para Backlog,
#                                                      "Em andamento" ou "Concluído"; issue nova
#                                                      entra no board, na sprint atual (campo
#                                                      "Sprint", pelas datas de cada iteração).
# Precisa de GH_TOKEN com os escopos `repo` e `project`. Variáveis: PROJECT_OWNER,
# PROJECT_NUMBER, REPO (dono/nome) e DRY_RUN=1 para só mostrar o que faria.
set -euo pipefail

mode="$1"
owner="${PROJECT_OWNER:?}"
number="${PROJECT_NUMBER:?}"
repo="${REPO:?}"
field_name="${STATUS_FIELD:-Status}"
target="Em andamento"
done_status="Concluído"
force=0
add_missing=0
set_sprint=0
sprint_field="${SPRINT_FIELD:-Sprint}"

case "$mode" in
  commits)
    # "refs #6", "Refs: #6", "ref #6 #7" → 6 e 7.
    issues=$(grep -oiE '\brefs?:?[[:space:]]+(#[0-9]+[[:space:],]*)+' "$2" | grep -oE '#[0-9]+' | tr -d '#' | sort -un || true)
    if [ -z "$issues" ]; then
      echo "Nenhum 'refs #N' nos commits."
      exit 0
    fi
    ;;
  issue)
    issues="$2"
    force=1
    case "$3" in
      opened) target="Backlog"; add_missing=1; set_sprint=1 ;;
      reopened) target="Em andamento"; add_missing=1 ;;
      closed) target="$done_status" ;;
      *) echo "Ação de issue sem regra: $3"; exit 0 ;;
    esac
    ;;
  *) echo "Modo desconhecido: $mode" >&2; exit 2 ;;
esac

project=$(gh api graphql -f owner="$owner" -F number="$number" -f field="$field_name" -f query='
  query($owner: String!, $number: Int!, $field: String!) {
    user(login: $owner) { projectV2(number: $number) {
      id
      field(name: $field) { ... on ProjectV2SingleSelectField { id options { id name } } }
    } }
  }')
project_id=$(jq -r '.data.user.projectV2.id' <<<"$project")
sprint_id=""
sprint_title=""
if [ "$set_sprint" = "1" ]; then
  sprints=$(gh api graphql -f owner="$owner" -F number="$number" -f field="$sprint_field" -f query='
    query($owner: String!, $number: Int!, $field: String!) {
      user(login: $owner) { projectV2(number: $number) {
        field(name: $field) { ... on ProjectV2IterationField {
          id configuration { iterations { id title startDate duration } }
        } }
      } }
    }')
  sprint_field_id=$(jq -r '.data.user.projectV2.field.id // empty' <<<"$sprints")
  # Sprint atual: a iteração cujo intervalo [início, início + duração) contém hoje (UTC).
  today=$(date -u +%Y-%m-%d)
  current=$(jq -c --arg today "$today" '[.data.user.projectV2.field.configuration.iterations[]?
      | select(.startDate <= $today and (((.startDate + "T00:00:00Z") | fromdateiso8601) + .duration * 86400 > ($today + "T00:00:00Z" | fromdateiso8601)))][0] // empty' <<<"$sprints")
  sprint_id=$(jq -r '.id // empty' <<<"${current:-null}")
  sprint_title=$(jq -r '.title // empty' <<<"${current:-null}")
  [ -n "$sprint_id" ] || echo "Nenhuma sprint em andamento hoje ($today); card fica sem sprint."
fi
field_id=$(jq -r '.data.user.projectV2.field.id' <<<"$project")
option_id=$(jq -r --arg name "$target" '.data.user.projectV2.field.options[] | select(.name == $name) | .id' <<<"$project")
if [ -z "$option_id" ]; then
  echo "A coluna '$target' não existe no board." >&2
  exit 1
fi

for issue in $issues; do
  item=$(gh api graphql -f owner="${repo%/*}" -f name="${repo#*/}" -F issue="$issue" -f query='
    query($owner: String!, $name: String!, $issue: Int!) {
      repository(owner: $owner, name: $name) { issue(number: $issue) {
        projectItems(first: 20) { nodes {
          id project { id }
          fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        } }
      } }
    }' 2>/dev/null || true)
  node=$(jq -c --arg project "$project_id" '.data.repository.issue.projectItems.nodes[]? | select(.project.id == $project)' <<<"$item" 2>/dev/null || true)
  if [ -z "$node" ] && [ "$add_missing" = "1" ]; then
    content=$(gh api "repos/$repo/issues/$issue" -q .node_id)
    if [ "${DRY_RUN:-0}" = "1" ]; then
      echo "#$issue: entraria no board (simulação)"
      node='{"id":"(novo)","fieldValueByName":null}'
    else
      new_id=$(gh api graphql -f project="$project_id" -f content="$content" -f query='
        mutation($project: ID!, $content: ID!) {
          addProjectV2ItemById(input: { projectId: $project, contentId: $content }) { item { id } }
        }' -q '.data.addProjectV2ItemById.item.id')
      node=$(jq -cn --arg id "$new_id" '{id: $id, fieldValueByName: null}')
      echo "#$issue entrou no board."
    fi
  fi
  if [ -z "$node" ]; then
    echo "#$issue não está no board; ignorado."
    continue
  fi
  status=$(jq -r '.fieldValueByName.name // ""' <<<"$node")
  if [ "$status" = "$target" ] || { [ "$force" = "0" ] && [ "$status" = "$done_status" ]; }; then
    echo "#$issue já está em '$status'; mantido."
    continue
  fi
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "#$issue: '$status' → '$target' (simulação)"
    [ -z "$sprint_id" ] || echo "#$issue: sprint '$sprint_title' (simulação)"
    continue
  fi
  gh api graphql -f project="$project_id" -f item="$(jq -r '.id' <<<"$node")" -f field="$field_id" -f option="$option_id" -f query='
    mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $project, itemId: $item, fieldId: $field, value: { singleSelectOptionId: $option }
      }) { projectV2Item { id } }
    }' >/dev/null
  echo "#$issue: '$status' → '$target'"
  if [ -n "$sprint_id" ]; then
    gh api graphql -f project="$project_id" -f item="$(jq -r '.id' <<<"$node")" -f field="$sprint_field_id" -f iteration="$sprint_id" -f query='
      mutation($project: ID!, $item: ID!, $field: ID!, $iteration: String!) {
        updateProjectV2ItemFieldValue(input: {
          projectId: $project, itemId: $item, fieldId: $field, value: { iterationId: $iteration }
        }) { projectV2Item { id } }
      }' >/dev/null
    echo "#$issue: sprint '$sprint_title'"
  fi
done
