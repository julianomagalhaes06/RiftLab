import type { HistorySyncStatus } from "../../shared/api/client";

export function clock(value?: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

export function active(status: HistorySyncStatus | null): boolean {
  return status?.state === "QUEUED" || status?.state === "RUNNING";
}

export function syncMessage(status: HistorySyncStatus | null, failedRequest: boolean): string {
  if (failedRequest) return "Não foi possível pedir a atualização do histórico agora.";
  if (!status) return "Verificando partidas novas na Riot…";
  const next = clock(status.nextAllowedAt);
  if (status.state === "UNAVAILABLE") {
    return status.reason === "ACCOUNT_NOT_CONFIGURED"
      ? "Atualização automática indisponível: a conta própria não está configurada no servidor."
      : "Atualização automática indisponível: o servidor está sem chave da Riot. Importe partidas pelo ID.";
  }
  if (active(status)) return "Atualizando o histórico com a Riot…";
  if (status.state === "FAILED") {
    const retry = next ? ` Nova tentativa a partir das ${next}.` : "";
    if (status.reason === "RIOT_AUTH_FAILED") {
      return `A chave da Riot foi recusada (a chave de desenvolvimento expira em 24 h). Renove-a e reinicie o servidor para atualizar o histórico.${retry}`;
    }
    if (status.reason === "RIOT_RATE_LIMITED") {
      return `Limite de requisições da Riot atingido.${retry}`;
    }
    return `Não foi possível atualizar o histórico agora.${retry}`;
  }
  if (status.state === "SUCCEEDED") {
    const found = status.newEnqueued ?? 0;
    const when = clock(status.finishedAt);
    const later = status.reason === "COOLDOWN" && next ? ` Próxima verificação a partir das ${next}.` : "";
    const news = `${found} ${found === 1 ? "partida nova encontrada" : "partidas novas encontradas"}`;
    if (!found) return `Histórico em dia: nenhuma partida nova${when ? ` desde a verificação das ${when}` : ""}.${later}`;
    // Visita dentro do intervalo mínimo: as partidas já vieram na verificação anterior.
    if (later) return `Na verificação das ${when ?? "última vez"}: ${news}.${later}`;
    return `${news}; ${found === 1 ? "aparece" : "aparecem"} aqui assim que a importação terminar.`;
  }
  return "Verificando partidas novas na Riot…";
}
