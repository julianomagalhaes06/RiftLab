import { INVALID_RESPONSE, NETWORK_ERROR, isApiError } from "./errors";

/**
 * TABELA ÚNICA código → texto em português (CC-205). Toda mensagem de erro da
 * interface — erro HTTP (problem+json), `errorCode` de importação e de preparação —
 * sai daqui. Nunca inclui segredo, chave, caminho local, SQL nem corpo remoto; o
 * texto descreve o que aconteceu e o que fazer, sem afirmar o que não se sabe (um
 * 404 nunca vira "partida em andamento").
 */
export const ERROR_MESSAGES: Readonly<Record<string, string>> = {
  // --- importação: recusa da própria API ---
  // 422 de POST /imports no modo RIOT sem chave no servidor (código dedicado da API).
  RIOT_KEY_MISSING:
    "A importação de partidas reais está indisponível: este ambiente não tem chave da Riot configurada. Explore a demonstração ou configure a chave Development no ambiente local.",
  VALIDATION_ERROR:
    "Os dados enviados não são válidos. Confira o ID da partida (formato BR1_ seguido de números) e tente de novo.",
  IMPORT_ACTIVE:
    "Essa operação não é possível no estado atual da importação (ela ainda está em andamento ou não falhou).",
  RIOT_INVALID_MATCH_ID:
    "O ID informado não é aceito pela Riot. Use o formato BR1_ seguido de números.",

  // --- importação: falhas registradas no job (errorCode) ---
  RIOT_AUTH_FAILED:
    "A chave da Riot é inválida ou expirou; renove a chave Development no ambiente local.",
  RIOT_RATE_LIMITED:
    "A Riot limitou temporariamente as requisições. Aguarde alguns instantes; a importação tenta de novo sozinha.",
  RIOT_NOT_FOUND:
    "A Riot não encontrou essa partida. Confira o ID e a região (BR1); ela também pode não estar disponível para consulta.",
  UPSTREAM_UNAVAILABLE:
    "A Riot está indisponível ou não respondeu. Tente novamente em alguns minutos.",
  SCHEMA_UNSUPPORTED:
    "O formato dos dados dessa partida não é compatível com esta versão do RiftLab.",
  MATCH_NOT_COMPLETED:
    "Só partidas concluídas podem ser analisadas. O RiftLab não acompanha jogos em andamento.",
  MATCH_SOURCE_MISSING_FIELD:
    "Os dados recebidos dessa partida estão incompletos e não puderam ser importados.",
  MATCH_SOURCE_INVALID_JSON:
    "Os dados recebidos dessa partida estão corrompidos e não puderam ser importados.",
  MATCH_SOURCE_IDENTITY_MISMATCH:
    "Os dados recebidos não correspondem à partida solicitada e foram descartados.",
  MATCH_TIMELINE_IDENTITY_MISMATCH:
    "A linha do tempo recebida não corresponde à partida solicitada e foi descartada.",
  MATCH_SOURCE_NOT_FOUND: "Essa partida não está disponível na fonte de dados.",
  RETRY_LIMIT_EXCEEDED:
    "A operação falhou várias vezes seguidas e foi interrompida. Tente novamente mais tarde.",
  SUBJECT_HASH_SECRET_MISSING:
    "Configuração local incompleta: falta o segredo usado para anonimizar jogadores. Verifique o ambiente do servidor.",

  // --- estudo, avaliação e relatório (CC-304/505) ---
  ATTEMPT_LOCKED:
    "Esta tentativa já tem uma resposta (ou reflexão) registrada; para mudar, comece uma nova tentativa.",
  ATTEMPT_NOT_REVEALED: "A reflexão só pode ser registrada depois de ver a comparação.",
  HINTS_DISABLED: "No modo sem pistas (transferência) as pistas ficam desligadas.",
  FEEDBACK_EXISTS:
    "Este revisor já registrou outro julgamento para esta alternativa com esta rubrica; julgamentos não são alterados.",
  IDEMPOTENCY_CONFLICT:
    "Este envio repetiu uma chave já usada com outro conteúdo. Recarregue a página e tente de novo.",

  // --- preparação (catálogo → reconstrução → extração) ---
  RAW_MATCH_NOT_CACHED:
    "Os dados brutos dessa partida não estão no armazenamento local. Importe a partida novamente.",
  RAW_STORE_CORRUPTED:
    "Os dados brutos guardados localmente dessa partida estão corrompidos e exigem resolução manual.",
  TIMELINE_EVENT_MALFORMED:
    "Um evento da linha do tempo está malformado, então o inventário não pôde ser reconstruído com segurança.",
  CATALOG_MAPPING_FILE_MISSING:
    "O mapeamento de catálogo de itens não foi encontrado no servidor; a preparação não pode continuar.",
  PREPARATION_UNEXPECTED_ERROR:
    "A preparação falhou de forma inesperada. Tente preparar novamente.",
  PREPARATION_CONFLICT:
    "Outra preparação da mesma partida estava em andamento. Tente novamente.",
  MATCH_NOT_FOUND: "A partida não foi encontrada para preparação.",
  RECONSTRUCTION_REQUIRED:
    "A partida precisa ser reconstruída antes de extrair as decisões. Tente preparar novamente.",

  // --- decisões ---
  DECISIONS_NOT_PREPARED:
    "As decisões dessa partida ainda não foram preparadas. Aguarde a preparação terminar ou peça para preparar de novo.",
  INVALID_CURSOR:
    "A página solicitada não é mais válida. Recarregue a lista de decisões.",

  // --- genéricos da API ---
  RESOURCE_NOT_FOUND:
    "Não encontramos o que você procurou. Confira o endereço; ele pode estar incorreto ou o item não existir mais.",
  DATABASE_UNAVAILABLE:
    "O banco de dados está indisponível no momento. Tente novamente em instantes.",
  INTERNAL_ERROR: "Ocorreu um erro interno no servidor. Tente novamente.",
  [NETWORK_ERROR]:
    "Não foi possível falar com a API do RiftLab. Verifique se o serviço local está no ar e tente novamente.",
  [INVALID_RESPONSE]: "A API respondeu de um jeito inesperado. Tente novamente.",
};

export const GENERIC_ERROR_MESSAGE =
  "Não foi possível concluir a operação. Tente novamente; se continuar, verifique se o serviço local está no ar.";

/** Famílias de códigos sem entrada própria (o servidor pode acrescentar códigos novos). */
const PREFIX_MESSAGES: ReadonlyArray<readonly [string, string]> = [
  [
    "DATA_DRAGON_",
    "O catálogo público de itens está indisponível ou incompatível para este patch. Tente novamente mais tarde.",
  ],
  [
    "CATALOG_",
    "Houve um problema com o catálogo de itens deste patch, então a preparação não pôde continuar.",
  ],
  [
    "SCOPE_",
    "A configuração do recorte de análise está ausente ou inválida no servidor.",
  ],
  [
    "STATE_",
    "O estado de inventário reconstruído é incompatível ou está malformado. Tente preparar novamente.",
  ],
  [
    "TIMELINE_",
    "A linha do tempo da partida não pôde ser processada com segurança.",
  ],
];

/** Texto para um código (de erro HTTP ou de `errorCode` de job/preparação). */
export function messageForCode(code: string | null | undefined): string {
  if (!code) {
    return GENERIC_ERROR_MESSAGE;
  }
  const exact = ERROR_MESSAGES[code];
  if (exact) {
    return exact;
  }
  const family = PREFIX_MESSAGES.find(([prefix]) => code.startsWith(prefix));
  if (family) {
    return family[1];
  }
  if (/^HTTP_5\d\d$/.test(code)) {
    return ERROR_MESSAGES.INTERNAL_ERROR;
  }
  if (code === "HTTP_404") {
    return ERROR_MESSAGES.RESOURCE_NOT_FOUND;
  }
  return GENERIC_ERROR_MESSAGE;
}

/** Texto para qualquer valor lançado por uma chamada à API. */
export function describeError(error: unknown): string {
  if (isApiError(error)) {
    return messageForCode(error.code);
  }
  return GENERIC_ERROR_MESSAGE;
}
