/**
 * Rotas da aplicação (CC-205). Roteamento próprio sobre a History API, sem
 * dependência nova: URL é a fonte da verdade (recarregar, voltar, avançar e deep
 * link funcionam).
 *
 *   / (e /painel)                        painel
 *   /importar                            importar por ID ou abrir a demonstração
 *   /imports/:jobId                      progresso de uma importação
 *   /matches/:matchId?participant=N      partida (preparação, participantes, decisões)
 *   /decisions/:decisionId               detalhe de uma decisão
 *   /prototipo                           protótipo ilustrativo do CC-102 (dados fictícios)
 */
export type Route =
  | { name: "importEntry" }
  | { name: "panel" }
  | { name: "champions" }
  | { name: "position" }
  | { name: "history" }
  | { name: "historyMatch"; matchId: string; participantId: number | null }
  | { name: "import"; jobId: string }
  | { name: "match"; matchId: string; participantId: number | null }
  | { name: "decision"; decisionId: string }
  | { name: "prototype" }
  | { name: "notFound"; path: string };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const REAL_MATCH_ID_PATTERN = /^BR1_[0-9]{1,20}$/;
const DEMO_MATCH_ID_PATTERN = /^DEMO-[0-9]{4}$/;

export const MAX_PARTICIPANT_ID = 10;

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

export function parseRoute(pathname: string, search = ""): Route {
  const trimmed = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  // CC-903: a entrada é o Painel; /painel continua valendo para links antigos.
  if (trimmed === "/" || trimmed === "" || trimmed === "/painel") return { name: "panel" };
  if (trimmed === "/importar") return { name: "importEntry" };
  if (trimmed === "/campeoes") return { name: "champions" };
  if (trimmed === "/minha-posicao") return { name: "position" };
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
    if (head === "decisions" && isUuid(id)) {
      return { name: "decision", decisionId: id.toLowerCase() };
    }
  }
  if (head === "prototipo" && id === undefined) {
    return { name: "prototype" };
  }
  return { name: "notFound", path: trimmed };
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
    case "importEntry":
      return "/importar";
    case "panel":
      return "/";
    case "champions":
      return "/campeoes";
    case "position":
      return "/minha-posicao";
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
    case "prototype":
      return "/prototipo";
    case "notFound":
      return route.path;
  }
}
