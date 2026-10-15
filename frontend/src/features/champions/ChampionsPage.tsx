import { useEffect, useState } from "react";
import { api, type ChampionPatterns } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";
import { ChampionBadge } from "../matches/ChampionBadge";
import "./champions.css";

const PHASES = { sequence: "1º → 2º item", first: "Primeiro item", second: "Segundo item" } as const;

function ItemPortrait({ item }: { item: NonNullable<ChampionPatterns["patterns"]>[number]["items"][number] }) {
  const [failed, setFailed] = useState(false);
  return <span className="pattern-item">
    {!failed && <img src={`https://ddragon.leagueoflegends.com/cdn/${item.dataDragonVersion}/img/item/${item.itemId}.png`}
      alt="" width={40} height={40} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />}
    <span>{item.name || `Item ${item.itemId}`}</span>
  </span>;
}

export function ChampionsPage() {
  const { href, navigate } = useRouter();
  const search = new URLSearchParams(href.split("?")[1]);
  const rawChampion = search.get("champion");
  const championId = rawChampion && /^[1-9]\d*$/.test(rawChampion) ? Number(rawChampion) : undefined;
  const patch = search.get("patch") || undefined;
  const rawPhase = search.get("phase");
  const phase = rawPhase === "first" || rawPhase === "second" ? rawPhase : "sequence";
  const queryKey = `${championId ?? ""}|${patch ?? ""}|${phase}`;
  const [result, setResult] = useState<{ key: string; data: ChampionPatterns } | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api.getChampionPatterns({ championId, patch, phase }, controller.signal).then((data) => {
      if (!controller.signal.aborted) setResult({ key: queryKey, data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && !isAbortError(error)) setErrorKey(queryKey);
    });
    return () => controller.abort();
  }, [championId, patch, phase, queryKey, retry]);
  const data = result?.data ?? null;
  const loading = result?.key !== queryKey;
  const failed = loading && errorKey === queryKey;
  const chosen = data?.champions.find((champion) => champion.championId === data.championId);
  function update(name: string, value: string) {
    const next = new URLSearchParams(href.split("?")[1]);
    next.set(name, value);
    navigate(`/campeoes?${next.toString()}`);
  }
  return <div className="area-page champions-page">
    <div className="page-intro">
      <p className="page-intro__kicker">Campeões · estudo pós-partida</p>
      <h2>Uma compra ganha sentido no contexto.</h2>
      <p>Explore trajetos observados nas partidas importadas e volte ao momento da decisão para estudar suas alternativas.</p>
    </div>
    {failed ? <section className="quiet-surface" role="alert">
      <h3>Não foi possível carregar os campeões</h3>
      <p>Confira a conexão com o servidor e tente novamente.</p>
      <button type="button" onClick={() => { setErrorKey(null); setRetry(retry + 1); }}>Tentar novamente</button>
    </section> : !data ? <p role="status">Carregando campeões e amostra local…</p> : <>
      <div className="champions-filters" aria-label="Filtros dos padrões observados">
        <label>Patch<select value={patch ?? data.patch ?? ""} onChange={(event) => update("patch", event.target.value)}>
          {data.patches.map((value) => <option key={value}>{value}</option>)}
          {data.patch && !data.patches.includes(data.patch) && <option>{data.patch}</option>}
        </select></label>
        <label>Função<select value="BOTTOM" disabled><option value="BOTTOM">Atirador · rota inferior</option></select></label>
        <label>Etapa da build<select value={phase} onChange={(event) => update("phase", event.target.value)}>
          {Object.entries(PHASES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <p>Origem: partidas reais importadas. Até {data.sampleLimit} participações recentes por campeão.</p>
      </div>
      <div className="champions-layout">
        <nav className="champions-roster" aria-label="Selecionar campeão">
          {data.champions.map((champion) => <button key={champion.championId} type="button"
            aria-pressed={champion.championId === (championId ?? data.championId)}
            onClick={() => update("champion", String(champion.championId))}>
            <ChampionBadge participant={champion} size={40} />
            <small>{loading ? "Carregando…" : `${champion.matches} partidas`}</small>
          </button>)}
        </nav>
        <div className="champions-content" aria-live="polite" aria-busy={loading}>
          {loading ? <p role="status">Carregando padrões deste filtro…</p> : <>
          <section className="champion-overview quiet-surface">
            <p className="page-intro__kicker">BOTTOM · patch {data.patch ?? "não detectado"}</p>
            <h3>{chosen ? <ChampionBadge participant={chosen} size={56} /> : "Campeão fora do recorte"}</h3>
            <dl className="champion-stats">
              <div><dt>Participações examinadas</dt><dd>{data.sampledParticipations} de {data.totalParticipations}</dd></div>
              <div><dt>Decisões elegíveis na etapa</dt><dd>{data.eligibleDecisions} de {data.observedDecisions}</dd></div>
              <div><dt>Trajetos completos na etapa</dt><dd>{data.patternDenominator}</dd></div>
            </dl>
            <p className="muted">Cobertura sobre compras principais reconstruídas na etapa selecionada. Ausência de dados não significa ausência de compras.</p>
          </section>
          <section className="quiet-surface champion-patterns" aria-labelledby="patterns-heading">
            <div className="champion-section-heading"><h3 id="patterns-heading">Trajeto de itens</h3><span className="champion-label">Padrão observado</span></div>
            <p>Frequência descreve o que aconteceu. Não é uma build ideal nem uma avaliação da qualidade da decisão.</p>
            <p className="muted">Até seis trajetos mais frequentes; as demais sequências permanecem no denominador.</p>
            {data.status === "AVAILABLE" ? <ol className="pattern-list" aria-label="Trajetos observados">
              {(data.patterns ?? []).map((pattern, index) => <li key={index}>
                <div className="pattern-sequence">{pattern.items.map((item, itemIndex) => <span key={`${itemIndex}-${item.itemId}-${item.dataDragonVersion}`}>
                  {itemIndex > 0 && <span className="pattern-arrow" aria-hidden="true">→</span>}
                  <ItemPortrait item={item} />
                </span>)}</div>
                <strong>{pattern.count} de {pattern.denominator} <small>({Math.round(100 * pattern.count / pattern.denominator)}%)</small></strong>
              </li>)}
            </ol> : <div className="champion-empty" role="status">
              <h4>{data.status === "SMALL_SAMPLE" ? "Amostra insuficiente para publicar padrões" : data.status === "OUT_OF_SCOPE" ? "Seleção fora do recorte validado" : "Ainda não há trajetos elegíveis neste filtro"}</h4>
              <p>São necessárias pelo menos {data.minimumSample} participações com a etapa completa e elegível. Nenhum padrão é preenchido por estimativa.</p>
            </div>}
            <p className="champion-limit">Sem ranking de itens validado para este guia. O baseline de popularidade usado no estudo é separado desta amostra local.</p>
          </section>
          <section className="quiet-surface" aria-labelledby="champion-study-heading">
            <h3 id="champion-study-heading">Do padrão à sua decisão</h3>
            <p>Antes de revelar a comparação, observe o inventário, o ouro aproximado e os adversários naquela situação.</p>
            <ul className="champion-questions">
              <li>Quais componentes já estavam no inventário e quais alternativas os aproveitam?</li>
              <li>Que ameaças a composição adversária apresentava naquele momento?</li>
              <li>O objetivo pede mais dano, sobrevivência ou utilidade? Que informação ainda falta?</li>
            </ul>
            {(data.situations ?? []).length > 0 ? <ul className="champion-situations" aria-label="Situações para estudar">
              {(data.situations ?? []).map((situation) => <li key={situation.decisionId}>
                <div><strong>{situation.matchId}</strong><span>Participante {situation.participantId} · {Math.floor(situation.timestampMs / 60000)} min</span></div>
                <div className="champion-situation-actions">
                  <Link to={`/decisions/${situation.decisionId}`}>Estudar decisão</Link>
                  <Link to={`/historico/${situation.matchId}?participant=${situation.participantId}`}>Abrir contexto</Link>
                </div>
              </li>)}
            </ul> : <Link to="/historico">Examinar partidas no Histórico</Link>}
          </section>
          </>}
        </div>
      </div>
    </>}
  </div>;
}
