import { useEffect, useId, useState } from "react";
import { api, type HistorySyncStatus, type RankedMe, type RankedQueue } from "../../shared/api/client";
import { active } from "../history/syncMessage";
import { useAuth } from "../auth/authContext";
import "./ranked.css";
import { preferredQueue } from "./preferredQueue";
import { RankCard, type RankCardChange } from "./RankCard";
import { TIER_NAMES, TIER_ORDER, emblemSrc, isTier, type Tier } from "./tiers";
import { count } from "../../shared/text/count";

const reasonNames: Record<string, string> = {
  ACCOUNT_OR_SECRET_MISSING: "Configure a conta local e o segredo de vínculo para consultar sua posição.",
  ACCOUNT_NOT_LINKED: "Vincule sua conta Riot em Minha conta (no seu nome, no topo) para ver sua posição.",
  NO_OBSERVATION: "Ainda não há observação desta fila. Com a chave da Riot configurada, ela é consultada ao abrir esta tela.",
  RIOT_KEY_MISSING: "A chave da Riot está ausente. A última posição observada continua visível.",
  OBSERVATION_OLD: "Esta observação tem mais de 24 horas.",
  RIOT_AUTH_FAILED: "A atualização falhou por autenticação na Riot. A posição anterior foi preservada.",
  RIOT_RATE_LIMITED: "A Riot limitou a atualização. A posição anterior foi preservada.",
  SCHEMA_UNSUPPORTED: "A resposta da Riot não pôde ser validada. A posição anterior foi preservada.",
  UPSTREAM_UNAVAILABLE: "A Riot não respondeu. A posição anterior foi preservada.",
};
function ChangeLine({ data }: { data: RankedMe["change"] }) {
  if (!data) return <p>Uma segunda observação da mesma temporada permitirá comparar a evolução.</p>;
  const since = new Date(data.since).toLocaleString("pt-BR");
  const until = new Date(data.until).toLocaleString("pt-BR");
  let text = "Variação não comparável entre estas observações.";
  if (data.kind === "CORRECTION") text = "Os contadores foram corrigidos; variação não comparável.";
  if (data.kind === "TRANSITION") text = "Houve mudança de elo ou divisão; PDL bruto não é comparável.";
  if (data.kind === "SAME_DIVISION") {
    const value = data.leaguePointsDelta ?? 0;
    text = `${value > 0 ? "+" : ""}${value} PDL desde a observação anterior, na mesma divisão.`;
  }
  const association = data.ownMatchAssociation === "ONE"
    ? "Uma partida própria concluída da fila foi encontrada nesse intervalo; a variação não é atribuída à partida."
    : "A variação cobre o intervalo de atualizações, sem atribuição a uma partida.";
  return <div className="my-rank__change"><p className="my-rank__delta">{text}</p><p>{association}</p><small>De {since} até {until}.</small></div>;
}

/** Regras do PDL e a escada de elos, com o elo atual marcado (texto, não só cor). */
function LpRules({ current }: { current: Tier | null }) {
  return (
    <section className="my-rank__aside surface surface--pad" aria-labelledby="my-rank-rules">
      <h3 id="my-rank-rules">Como funciona o PDL</h3>
      <ul className="my-rank__rules">
        <li>De Ferro a Diamante, cada divisão vai de 0 a 100 PDL, de IV a I.</li>
        <li>Ao chegar a 100 PDL, a promoção acontece e o excedente segue para a divisão seguinte.</li>
        <li>Mestre, Grão-Mestre e Desafiante não têm divisões, e o PDL não tem teto.</li>
        <li>A posição é consultada na Riot ao abrir esta tela, o Painel ou o Histórico, com um intervalo mínimo entre consultas.</li>
        <li>A variação entre observações não é atribuída a uma partida importada.</li>
      </ul>
      <ol className="tier-ladder" aria-label="Elos, do menor ao maior">
        {TIER_ORDER.map((tier) => (
          <li key={tier} className={tier === current ? "tier-ladder__step is-current" : "tier-ladder__step"} data-tier={tier === current ? tier : undefined} aria-current={tier === current ? "true" : undefined}>
            <img src={emblemSrc(tier)} alt="" width="44" height="44" />
            <span>{TIER_NAMES[tier]}</span>
            {tier === current ? <small>seu elo</small> : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

/** Faixa de variação do card (CC-1203): ganho, perda ou o motivo de não comparar. */
function changeStrip(data: RankedMe["change"]): RankCardChange {
  if (!data) return { tone: "neutral", text: "Uma segunda observação permitirá comparar" };
  if (data.kind === "SAME_DIVISION") {
    const value = data.leaguePointsDelta ?? 0;
    if (value > 0) return { tone: "win", text: `+${value} PDL desde a observação anterior` };
    if (value < 0) return { tone: "loss", text: `${value} PDL desde a observação anterior` };
    return { tone: "neutral", text: "Sem variação de PDL desde a observação anterior" };
  }
  if (data.kind === "TRANSITION") return { tone: "neutral", text: "Mudou de elo ou divisão desde a observação anterior" };
  return { tone: "neutral", text: "Variação não comparável com a observação anterior" };
}

const observedAt = (iso: string) => new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });

const SYNC_POLL_MS = 2000;

/**
 * Ao abrir, pede a sincronização da conta própria (CC-901). A mesma execução do worker consulta
 * o elo na Riot; o servidor respeita o intervalo mínimo e a execução ativa. `round` muda quando
 * uma execução acompanhada termina, para a posição ser lida de novo do banco.
 */
function useRankRefresh(enabled: boolean): { updating: boolean; round: number } {
  const [updating, setUpdating] = useState(false);
  const [round, setRound] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let timer: number | undefined;
    let followed = false;
    const stop = () => {
      if (!controller.signal.aborted) setUpdating(false);
    };
    const follow = (status: HistorySyncStatus) => {
      if (controller.signal.aborted) return;
      if (active(status)) {
        followed = true;
        setUpdating(true);
        timer = window.setTimeout(() => {
          api.getHistorySync(controller.signal).then(follow).catch(stop);
        }, SYNC_POLL_MS);
        return;
      }
      setUpdating(false);
      if (followed) setRound((value) => value + 1);
    };
    api.requestHistorySync(controller.signal).then(follow).catch(stop);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [enabled]);
  return { updating, round };
}

export function MyRank({ compact = false }: { compact?: boolean }) {
  const [selection, setSelection] = useState<RankedQueue | null>(null);
  const [results, setResults] = useState<Partial<Record<RankedQueue, { data?: RankedMe; failed?: boolean }>>>({});
  const queueId = useId();
  // Visitante (Sprint 2, #14): sem conta Riot, nada a sincronizar.
  const guest = useAuth().user?.guest === true;
  const { updating, round } = useRankRefresh(!guest);
  useEffect(() => {
    const controller = new AbortController();
    for (const queue of ["RANKED_SOLO_5x5", "RANKED_FLEX_SR"] as const) {
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]);
      api.getRankedMe(signal, queue).then((data) => {
        if (data.position && data.position.queue !== queue) throw new Error("Fila da resposta divergente");
        if (!controller.signal.aborted) setResults((current) => ({ ...current, [queue]: { data } }));
      }).catch(() => {
        if (!controller.signal.aborted) setResults((current) => ({ ...current, [queue]: { failed: true } }));
      });
    }
    return () => controller.abort();
  }, [round]);
  const queue = selection ?? preferredQueue(results.RANKED_SOLO_5x5?.data, results.RANKED_FLEX_SR?.data);
  const data = results[queue]?.data ?? null;
  const error = results[queue]?.failed === true;
  const queueLabel = queue === "RANKED_SOLO_5x5" ? "Solo/Duo" : "Flex";
  const position = data?.position;
  const tier = position?.state === "READY" && isTier(position.tier) ? position.tier : null;
  const headingId = `my-rank-heading-${compact ? "panel" : "page"}`;
  return (
    <div className={`my-rank ${compact ? "my-rank--compact" : ""}`}>
      <RankCard
        size={compact ? "compact" : "large"}
        heading={<h3 id={headingId}>Minha posição</h3>}
        controls={
          <div className="rank-card__controls">
            <label className="sr-only" htmlFor={queueId}>Fila ranqueada</label>
            <select id={queueId} aria-describedby={`${queueId}-hint`} value={queue} onChange={(event) => setSelection(event.target.value as RankedQueue)}>
              <option value="RANKED_SOLO_5x5">Solo/Duo</option>
              <option value="RANKED_FLEX_SR">Flex</option>
            </select>
            <small id={`${queueId}-hint`}>{selection === null ? "Seleção automática · melhor classificação disponível" : "Seleção manual · válida nesta página"}</small>
          </div>
        }
        rank={tier && position ? { tier, division: position.division ?? null, leaguePoints: position.leaguePoints ?? 0 } : null}
        caption={position ? `Fila ${queueLabel} · observado em ${observedAt(position.observedAt)}` : undefined}
        change={tier ? changeStrip(data?.change ?? null) : null}
      >
        {!data && !error && <p className="rank-card__state" role="status">Carregando posição observada…</p>}
        {error && <p className="rank-card__state" role="alert">Não foi possível consultar a posição armazenada.</p>}
        {data?.status === "UNAVAILABLE" && <p className="rank-card__state" role="status">{guest
          ? "Visitantes não têm posição ranqueada. Crie uma conta e vincule sua conta Riot para ver a sua."
          : reasonNames[data.reason ?? ""] ?? "Posição indisponível para esta conta."}</p>}
        {position?.state === "UNRANKED" && <p className="rank-card__state" role="status">Não classificado na {queueLabel} nesta observação válida.</p>}
        {updating && <p className="rank-card__note" role="status" aria-busy="true">Atualizando a posição com a Riot…</p>}
        {data?.status === "STALE" && !updating && <p className="rank-card__note" role="status">Última posição observada. {reasonNames[data.reason ?? ""] ?? "Atualização indisponível."}</p>}
        {position?.origin === "DEMO_FICTITIOUS" && <p className="rank-card__note" role="note">Elo fictício de demonstração: não foi consultado na Riot e não pertence a nenhuma conta.</p>}
      </RankCard>
      {!compact ? (
        <div className="my-rank__side">
          {position ? (
            <section className="my-rank__aside surface surface--pad" aria-labelledby="my-rank-observation">
              <h3 id="my-rank-observation">Observação</h3>
              <dl className="my-rank__facts">
                <div><dt>Fila</dt><dd>{queueLabel}</dd></div>
                <div><dt>Temporada</dt><dd>{position.season}</dd></div>
                <div><dt>Origem</dt><dd>{position.origin === "DEMO_FICTITIOUS" ? "Demonstração (fictício)" : "Riot League-V4"}</dd></div>
                <div><dt>Horário</dt><dd>{new Date(position.observedAt).toLocaleString("pt-BR")}</dd></div>
                {tier && position.wins != null && position.losses != null ? (
                  <div><dt>Partidas</dt><dd className="my-rank__record">{count(position.wins, "vitória", "vitórias")} · {count(position.losses, "derrota", "derrotas")} na fila</dd></div>
                ) : null}
              </dl>
            </section>
          ) : null}
          {tier ? (
            <section className="my-rank__aside surface surface--pad" aria-labelledby="my-rank-change">
              <h3 id="my-rank-change">Variação</h3>
              <ChangeLine data={data?.change ?? null} />
            </section>
          ) : null}
          <LpRules current={tier} />
        </div>
      ) : null}
    </div>
  );
}
