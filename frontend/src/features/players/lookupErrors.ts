/** Mensagens dos erros da busca por Riot ID (CC-1102), também usadas na partida ao vivo (CC-1211). */
export const LOOKUP_ERRORS: Record<string, string> = {
  PLAYER_NOT_FOUND: "Nenhuma conta Riot com esse Riot ID. Confira o nome e a TAG.",
  PLAYER_NOT_ON_PLATFORM: "A conta existe, mas não joga League of Legends no servidor BR.",
  INVALID_RIOT_ID: "Use o formato Nome#TAG (a TAG tem de 3 a 5 letras ou números).",
  RIOT_KEY_MISSING: "Busca indisponível: o servidor está sem chave da Riot.",
  SECRET_MISSING: "Busca indisponível: o servidor está sem o segredo local de identificação.",
  RIOT_AUTH_FAILED: "A chave da Riot foi recusada (a de desenvolvimento expira em 24 h).",
  RIOT_RATE_LIMITED: "Muitas consultas à Riot agora. Tente de novo em instantes.",
};
