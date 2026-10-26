#!/usr/bin/env bash
# Move para "Em andamento" os cards do board citados com "refs #N" nas mensagens de commit.
# Uso: board-progress.sh <arquivo com as mensagens>. Precisa de GH_TOKEN com o escopo `project`.
# Variáveis: PROJECT_OWNER, PROJECT_NUMBER, REPO (dono/nome), STATUS_FIELD, TARGET_STATUS,
# DONE_STATUS e DRY_RUN=1 para só mostrar o que faria.
set -euo pipefail

messages_file="$1"
owner="${PROJECT_OWNER:?}"
number="${PROJECT_NUMBER:?}"
repo="${REPO:?}"
field_name="${STATUS_FIELD:-Status}"
target="${TARGET_STATUS:-Em andamento}"
done_status="${DONE_STATUS:-Concluído}"

# "refs #6", "Refs: #6", "ref #6 #7" → 6 e 7.
issues=$(grep -oiE '\brefs?:?[[:space:]]+(#[0-9]+[[:space:],]*)+' "$messages_file" | grep -oE '#[0-9]+' | tr -d '#' | sort -un || true)
if [ -z "$issues" ]; then
  echo "Nenhum 'refs #N' nos commits."
  exit 0
fi

project=$(gh api graphql -f owner="$owner" -F number="$number" -f field="$field_name" -f query='
  query($owner: String!, $number: Int!, $field: String!) {
    user(login: $owner) { projectV2(number: $number) {
      id
      field(name: $field) { ... on ProjectV2SingleSelectField { id options { id name } } }
    } }
  }')
project_id=$(jq -r '.data.user.projectV2.id' <<<"$project")
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
  if [ -z "$node" ]; then
    echo "#$issue não está no board; ignorado."
    continue
  fi
  status=$(jq -r '.fieldValueByName.name // ""' <<<"$node")
  if [ "$status" = "$target" ] || [ "$status" = "$done_status" ]; then
    echo "#$issue já está em '$status'; mantido."
    continue
  fi
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "#$issue: '$status' → '$target' (simulação)"
    continue
  fi
  gh api graphql -f project="$project_id" -f item="$(jq -r '.id' <<<"$node")" -f field="$field_id" -f option="$option_id" -f query='
    mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $project, itemId: $item, fieldId: $field, value: { singleSelectOptionId: $option }
      }) { projectV2Item { id } }
    }' >/dev/null
  echo "#$issue: '$status' → '$target'"
done
