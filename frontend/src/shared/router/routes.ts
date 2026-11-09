/**
 * Rotas da aplicação (CC-205). Roteamento próprio sobre a History API, sem
 * dependência nova: URL é a fonte da verdade (recarregar, voltar, avançar e deep
 * link funcionam).
 *
 *   /                                    início público (busca de jogador e vitrine)
 *   /painel                              painel da conta própria
 *   /entrar?next=/caminho                entrar (volta ao caminho depois)
 *   /criar-conta?next=/caminho           criar conta
 *   /esqueci-senha?next=/caminho         redefinir a senha por código no e-mail
 *   /boas-vindas                         conta recém-criada pelo Google ou Discord
 *   /importar                            importar por ID ou abrir a demonstração
 *   /imports/:jobId                      progresso de uma importação
 *   /matches/:matchId?participant=N      partida (preparação, participantes, decisões)
 *   /decisions/:decisionId               detalhe de uma decisão
 *   /jogador/:nome-:tag                  perfil de um jogador pelo Riot ID (CC-1102)
 *     ?partida=BR1_…&participante=N      vindo do placar: o perfil é achado pela conta (o
 *                                        Riot ID da partida pode ter mudado)
 *     ?antes=Nome%23TAG                  Riot ID que o jogador tinha na partida
 *   /jogador/:nome-:tag/ao-vivo          partida ao vivo do jogador (CC-1211)
 *   /conta                              conta local e vínculo da conta Riot (Sprint 2, #12)
 *   /prototipo                           protótipo ilustrativo do CC-102 (dados fictícios)
 */
export type Route =
  | { name: "home" }
  | { name: "login"; next: string | null }
  | { name: "register"; next: string | null }
  | { name: "forgot"; next: string | null }
  | { name: "welcome" }
  | { name: "importEntry" }
  | { name: "panel" }
  | { name: "champions" }
  | { name: "position" }
  | { name: "account" }
  | { name: "history" }
  | { name: "historyMatch"; matchId: string; participantId: number | null }
  | { name: "import"; jobId: string }
  | { name: "match"; matchId: string; participantId: number | null }
  | { name: "decision"; decisionId: string }
  | {
      name: "player";
      gameName: string;
      tagLine: string;
      /** Vindo do placar: participante da partida, para achar a conta mesmo com nome novo. */
      fromMatch?: { matchId: string; participantId: number };
      /** Riot ID que o jogador usava na partida (mostrado quando mudou). */
      previousRiotId?: string;
    }
  | { name: "live"; gameName: string; tagLine: string }
  | { name: "prototype" }
  | { name: "notFound"; path: string };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const REAL_MATCH_ID_PATTERN = /^BR1_[0-9]{1,20}$/;
const DEMO_MATCH_ID_PATTERN = /^DEMO-[0-9]{4}$/;

export const MAX_PARTICIPANT_ID = 10;

/** "Nome#TAG" digitado → partes; TAG de 3 a 5 letras ou números (Riot ID). */
export function parseRiotId(value: string): { gameName: string; tagLine: string } | null {
  const text = value.trim();
  const hash = text.lastIndexOf("#");
  if (hash <= 0) return null;
  const gameName = text.slice(0, hash).trim();
  const tagLine = text.slice(hash + 1).trim();
  if (!gameName || gameName.length > 16 || !/^[\p{L}\p{N}]{3,5}$/u.test(tagLine)) return null;
  return { gameName, tagLine };
}

export function isUuid(value: string): boolean {
  return UUID_PATTERN.test(value);
}

/** ID de partida real (BR1_<números>), como o servidor valida em POST /imports (modo RIOT). */
export function isRealMatchId(value: string): boolean {
  return REAL_MATCH_ID_PATTERN.test(value);
}

/** Qualquer ID de partida aceito pelas rotas (real ou de demonstração). */
export function isMatchId(value: string): boolean {
  return REAL_MATCH_ID_PATTERN.test(value) || DEMO_MATCH_ID_PATTERN.test(value);
}

export function isDemoMatchId(value: string): boolean {
  return DEMO_MATCH_ID_PATTERN.test(value);
}

function parseParticipant(search: string): number | null {
  const raw = new URLSearchParams(search).get("participant");
  if (raw === null || !/^[0-9]{1,2}$/.test(raw)) {
    return null;
  }
  const value = Number(raw);
  return value >= 1 && value <= MAX_PARTICIPANT_ID ? value : null;
}

/** Caminho interno para voltar depois de entrar; recusa outro site ("//x", "https:"). */
export function safeNext(value: string | null): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return null;
  return value;
}

/** Entrar e voltar ao caminho atual (ou a `next`) depois do login. */
export function loginPath(next?: string | null, mode: "login" | "register" | "forgot" = "login"): string {
  const base = mode === "login" ? "/entrar" : mode === "register" ? "/criar-conta" : "/esqueci-senha";
  const target = safeNext(next ?? null);
  return target && target !== "/" ? `${base}?next=${encodeURIComponent(target)}` : base;
}

export function parseRoute(pathname: string, search = ""): Route {
  const trimmed = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  // Início público, como em sites de estatísticas; o Painel da conta fica em /painel.
  if (trimmed === "/" || trimmed === "") return { name: "home" };
  if (trimmed === "/painel") return { name: "panel" };
  if (trimmed === "/boas-vindas") return { name: "welcome" };
  if (trimmed === "/entrar" || trimmed === "/criar-conta" || trimmed === "/esqueci-senha") {
    const next = safeNext(new URLSearchParams(search).get("next"));
    return { name: trimmed === "/entrar" ? "login" : trimmed === "/criar-conta" ? "register" : "forgot", next };
  }
  if (trimmed === "/importar") return { name: "importEntry" };
  if (trimmed === "/campeoes") return { name: "champions" };
  if (trimmed === "/minha-posicao") return { name: "position" };
  if (trimmed === "/conta") return { name: "account" };
  if (trimmed === "/historico") return { name: "history" };
  const parts = trimmed.split("/").slice(1).map(safeDecode);
  const [head, id, ...rest] = parts;
  if (id !== undefined && id !== null && rest.length === 0) {
    if (head === "imports" && isUuid(id)) {
      return { name: "import", jobId: id.toLowerCase() };
    }
    if (head === "matches" && isMatchId(id)) {
      return { name: "match", matchId: id, participantId: parseParticipant(search) };
    }
    if (head === "historico" && isMatchId(id)) {
      return { name: "historyMatch", matchId: id, participantId: parseParticipant(search) };
    }
    if (head === "jogador") {
      // Nome-TAG, como em sites de estatísticas: a TAG não tem hífen, então vale o último.
      const dash = id.lastIndexOf("-");
      const riotId = dash > 0 ? parseRiotId(`${id.slice(0, dash)}#${id.slice(dash + 1)}`) : null;
      if (riotId) return { name: "player", ...riotId, ...playerContext(search) };
    }
    if (head === "decisions" && isUuid(id)) {
      return { name: "decision", decisionId: id.toLowerCase() };
    }
  }
  if (head === "jogador" && id && rest.length === 1 && rest[0] === "ao-vivo") {
    const dash = id.lastIndexOf("-");
    const riotId = dash > 0 ? parseRiotId(`${id.slice(0, dash)}#${id.slice(dash + 1)}`) : null;
    if (riotId) return { name: "live", ...riotId };
  }
  if (head === "prototipo" && id === undefined) {
    return { name: "prototype" };
  }
  return { name: "notFound", path: trimmed };
}

/** Contexto opcional do perfil na query: partida de origem e Riot ID antigo. */
function playerContext(search: string): { fromMatch?: { matchId: string; participantId: number }; previousRiotId?: string } {
  const params = new URLSearchParams(search);
  const context: { fromMatch?: { matchId: string; participantId: number }; previousRiotId?: string } = {};
  const matchId = params.get("partida");
  const participantId = parseParticipant(`?participant=${params.get("participante") ?? ""}`);
  if (matchId && isRealMatchId(matchId) && participantId !== null) context.fromMatch = { matchId, participantId };
  const previous = params.get("antes");
  if (previous && previous.length <= 40 && parseRiotId(previous)) context.previousRiotId = previous;
  return context;
}

function safeDecode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function routeToPath(route: Route): string {
  switch (route.name) {
    case "home":
      return "/";
    case "login":
      return loginPath(route.next, "login");
    case "register":
      return loginPath(route.next, "register");
    case "forgot":
      return loginPath(route.next, "forgot");
    case "welcome":
      return "/boas-vindas";
    case "importEntry":
      return "/importar";
    case "panel":
      return "/painel";
    case "champions":
      return "/campeoes";
    case "position":
      return "/minha-posicao";
    case "account":
      return "/conta";
    case "history":
      return "/historico";
    case "historyMatch": {
      const base = `/historico/${route.matchId}`;
      return route.participantId === null ? base : `${base}?participant=${route.participantId}`;
    }
    case "import":
      return `/imports/${route.jobId}`;
    case "match": {
      const base = `/matches/${route.matchId}`;
      return route.participantId === null ? base : `${base}?participant=${route.participantId}`;
    }
    case "decision":
      return `/decisions/${route.decisionId}`;
    case "player": {
      const base = `/jogador/${encodeURIComponent(route.gameName)}-${encodeURIComponent(route.tagLine)}`;
      const params = new URLSearchParams();
      if (route.fromMatch) {
        params.set("partida", route.fromMatch.matchId);
        params.set("participante", String(route.fromMatch.participantId));
      }
      if (route.previousRiotId) params.set("antes", route.previousRiotId);
      const query = params.toString();
      return query ? `${base}?${query}` : base;
    }
    case "live":
      return `/jogador/${encodeURIComponent(route.gameName)}-${encodeURIComponent(route.tagLine)}/ao-vivo`;
    case "prototype":
      return "/prototipo";
    case "notFound":
      return route.path;
  }
}
