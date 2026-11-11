import { useEffect, useState } from "react";
import { api, type ChampionPatterns } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";
import { ChampionBadge } from "../matches/ChampionBadge";
import { ItemNames } from "../../shared/ui/ItemNames";
import { useItemName } from "../../shared/hooks/useItemName";
import "./champions.css";
import { count } from "../../shared/text/count";
import { championName, championSplashUrl } from "../matches/format";
import { GameIcon } from "../../shared/ui/GameIcon";

const PHASES = { sequence: "1º → 2º item", first: "Primeiro item", second: "Segundo item" } as const;

/** "16.22 (atual)" para o patch atual do jogo; os demais, só o número. */
function patchLabel(patch: string | null | undefined, current: string | null | undefined): string {
  if (!patch) return "não detectado";
  return patch === current ? `${patch} (atual)` : patch;
}

function ItemPortrait({ item }: { item: NonNullable<ChampionPatterns["patterns"]>[number]["items"][number] }) {
  return <ItemNames version={item.dataDragonVersion}><LocalizedItemPortrait item={item} /></ItemNames>;
}

function LocalizedItemPortrait({ item }: { item: NonNullable<ChampionPatterns["patterns"]>[number]["items"][number] }) {
  const name = useItemName();
  const [failed, setFailed] = useState(false);
  return <span className="pattern-item">
    <span className="item-slot">{!failed && <img src={`https://ddragon.leagueoflegends.com/cdn/${item.dataDragonVersion}/img/item/${item.itemId}.png`}
      alt="" width={52} height={52} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />}</span>
    <span>{name(item)}</span>
  </span>;
}

/** Arte decorativa atrás do cabeçalho; some se falhar (fica o fundo neutro). */
function ChampionSplash({ src }: { src: string }) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  if (state === "failed") return null;
  return <img className="champion-overview__art" data-state={state} src={src} alt="" decoding="async"
    referrerPolicy="no-referrer" onLoad={() => setState("ready")} onError={() => setState("failed")} />;
}

/** Anel de frequência do trajeto (decorativo: o número fica no texto ao lado). */
function ShareRing({ share }: { share: number }) {
  const radius = 20;
  const length = 2 * Math.PI * radius;
  return <svg className="share-ring" viewBox="0 0 52 52" width="52" height="52" aria-hidden="true">
    <circle cx="26" cy="26" r={radius} className="share-ring__track" />
    <circle cx="26" cy="26" r={radius} className="share-ring__fill" strokeDasharray={`${share * length} ${length}`} transform="rotate(-90 26 26)" />
    <text x="26" y="26" dominantBaseline="central" textAnchor="middle">{Math.round(share * 100)}%</text>
  </svg>;
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
  const selected = championId ?? data?.championId;
  const roster = (data?.champions ?? []).filter((c) => c.matches > 0 || c.championId === selected);
  const absent = (data?.champions ?? []).filter((c) => c.matches === 0 && c.championId !== selected);
  function update(name: string, value: string) {
    const next = new URLSearchParams(href.split("?")[1]);
    next.set(name, value);
    navigate(`/campeoes?${next.toString()}`);
  }
  const splash = championSplashUrl(chosen?.champion?.imageFile);
  const analysisPatches = data?.analysisPatches ?? [];
  const outsideWindow = data?.status === "OUT_OF_SCOPE" && !!data.patch && !analysisPatches.includes(data.patch);
  return <div className="area-page champions-page">
    <header className="page-head champions-head">
      <p className="page-head__context">Campeões · estudo pós-partida</p>
      <h2>Padrões de compra por campeão</h2>
    </header>
    {failed ? <section className="surface surface--pad" role="alert">
      <h3>Não foi possível carregar os campeões</h3>
      <p>Confira a conexão com o servidor e tente novamente.</p>
      <button type="button" onClick={() => { setErrorKey(null); setRetry(retry + 1); }}>Tentar novamente</button>
    </section> : !data ? <p role="status">Carregando campeões e amostra local…</p> : <>
      {/* Seleção como a do jogo: retratos grandes, escolhido com moldura e brilho roxos; campeões
          sem partida no filtro aparecem bloqueados (não clicáveis), como na seleção de campeões. */}
      <nav className="champions-roster" aria-labelledby="champions-roster-heading">
        <div className="champions-roster__head">
          <h3 id="champions-roster-heading"><GameIcon name="crosshair" /> Escolha o campeão</h3>
          <p className="champions-roster__patch">Partidas coletadas no patch {patchLabel(data.patch, data.currentPatch)}</p>
        </div>
        <ul className="champions-roster__list">
          {roster.map((champion) => {
            const pressed = champion.championId === (championId ?? data.championId);
            return <li key={champion.championId} className={pressed ? "champion-tile result-bar is-selected" : "champion-tile"}>
              <button type="button" aria-pressed={pressed} onClick={() => update("champion", String(champion.championId))}>
                <ChampionBadge participant={champion} size={72} />
                <small>{loading ? "Carregando…" : champion.matches ? count(champion.matches, "partida", "partidas") : "Sem partidas"}</small>
              </button>
            </li>;
          })}
          {absent.map((champion) => <li key={champion.championId} className="champion-tile champion-tile--locked" aria-hidden="true" title={`${championName(champion)}: sem partidas neste patch`}>
            <div>
              <ChampionBadge participant={champion} size={72} />
              <small><GameIcon name="lock" size={12} /> Sem partidas</small>
            </div>
          </li>)}
        </ul>
        {/* CC-904: campeões sem partida no filtro não são botões; a frase fica para leitores de tela. */}
        {absent.length ? <p className="sr-only">Sem partidas no patch {data.patch}: {absent.map((c) => championName(c)).join(", ")}.</p> : null}
      </nav>

      <div className="champions-toolbar" aria-label="Filtros dos padrões observados">
        <label>Patch<select value={patch ?? data.patch ?? ""} onChange={(event) => update("patch", event.target.value)}>
          {data.patches.map((value) => <option key={value} value={value}>{patchLabel(value, data.currentPatch)}</option>)}
          {data.patch && !data.patches.includes(data.patch) && <option value={data.patch}>{patchLabel(data.patch, data.currentPatch)}</option>}
        </select></label>
        <label>Etapa da build<select value={phase} onChange={(event) => update("phase", event.target.value)}>
          {Object.entries(PHASES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <details className="info-pop">
          <summary><GameIcon name="info" /> Como ler estes números</summary>
          <div className="info-pop__body">
            <p>Partidas reais importadas, até {data.sampleLimit} participações recentes por campeão. O guia cobre hoje os atiradores da rota inferior.</p>
            <p>Cobertura sobre compras principais reconstruídas na etapa selecionada. Ausência de dados não significa ausência de compras.</p>
            <p>Frequência descreve o que aconteceu, não uma build ideal nem uma nota para a decisão. Até seis trajetos mais frequentes; as demais sequências seguem no denominador.</p>
          </div>
        </details>
      </div>

      <div className="champions-content" aria-live="polite" aria-busy={loading}>
        {loading ? <p role="status">Carregando padrões deste filtro…</p> : <>
        <section className="champion-overview" data-art={splash ? "splash" : "none"}>
          {splash ? <ChampionSplash key={splash} src={splash} /> : null}
          <div className="champion-overview__inner">
            <div className="champion-overview__tags">
              <span className="hud-tag"><GameIcon name="crosshair" size={14} /> Atirador · rota inferior</span>
              <span className="hud-tag"><GameIcon name="map" size={14} /> Patch {patchLabel(data.patch, data.currentPatch)}</span>
            </div>
            <h3>{chosen ? <ChampionBadge participant={chosen} size={88} /> : "Campeão fora do recorte"}</h3>
            <dl className="champion-stats">
              <div><dt className="metric__label"><GameIcon name="users" size={14} /> Participações examinadas</dt><dd>{data.sampledParticipations} de {data.totalParticipations}</dd></div>
              <div><dt className="metric__label"><GameIcon name="target" size={14} /> Decisões elegíveis na etapa</dt><dd>{data.eligibleDecisions} de {data.observedDecisions}</dd></div>
              <div><dt className="metric__label"><GameIcon name="route" size={14} /> Trajetos completos na etapa</dt><dd>{data.patternDenominator}</dd></div>
            </dl>
          </div>
        </section>
        <div className="champions-columns">
        <section className="surface surface--pad champion-patterns" aria-labelledby="patterns-heading">
          <div className="section-head"><h3 id="patterns-heading"><GameIcon name="route" /> Trajeto de itens</h3><span className="chip">Padrão observado</span></div>
          {data.status === "AVAILABLE" ? <ol className="pattern-list" aria-label="Trajetos observados">
            {(data.patterns ?? []).map((pattern, index) => {
              const share = pattern.denominator ? pattern.count / pattern.denominator : 0;
              return <li key={index} className={index === 0 ? "pattern-list__top" : undefined}>
                <span className="pattern-rank" aria-hidden="true">{index + 1}º</span>
                <div className="pattern-sequence">{pattern.items.map((item, itemIndex) => <span key={`${itemIndex}-${item.itemId}-${item.dataDragonVersion}`}>
                  {itemIndex > 0 && <span className="pattern-arrow" aria-hidden="true"><GameIcon name="arrow" size={16} /></span>}
                  <ItemPortrait item={item} />
                </span>)}</div>
                <div className="pattern-share">
                  <ShareRing share={share} />
                  <strong>{pattern.count} de {pattern.denominator} <small className="sr-only">({Math.round(100 * share)}%)</small></strong>
                </div>
              </li>;
            })}
          </ol> : <div className="champion-empty" role="status">
            <span className="champion-empty__icon"><GameIcon name={data.status === "OUT_OF_SCOPE" ? "map" : "chart"} size={28} /></span>
            <div>
              <h4>{data.status === "SMALL_SAMPLE" ? "Amostra insuficiente para publicar padrões" : data.status === "OUT_OF_SCOPE" ? "Seleção fora do recorte validado" : "Ainda não há trajetos elegíveis neste filtro"}</h4>
              {outsideWindow
                ? <p>O patch {data.patch} ainda não está na janela de análise{analysisPatches.length ? ` (${analysisPatches.join(" e ")})` : ""}. A contagem de partidas continua visível.</p>
                : <p>São necessárias pelo menos {data.minimumSample} participações com a etapa completa e elegível. Nenhum padrão é preenchido por estimativa.</p>}
              {outsideWindow && analysisPatches.length ? <button type="button" className="button" onClick={() => update("patch", analysisPatches[0])}>
                Ver no patch {analysisPatches[0]}
              </button> : null}
            </div>
          </div>}
          <p className="champion-limit"><GameIcon name="info" size={14} /> Sem ranking de itens validado para este guia. O baseline de popularidade usado no estudo é separado desta amostra local.</p>
        </section>
        <section className="surface surface--pad champion-study" aria-labelledby="champion-study-heading">
          <div className="section-head"><h3 id="champion-study-heading"><GameIcon name="target" /> Desafios de decisão</h3></div>
          <p>Volte ao momento da compra: inventário, ouro e adversários antes de revelar a comparação.</p>
          {(data.situations ?? []).length > 0 ? <ul className="champion-situations" aria-label="Situações para estudar">
            {(data.situations ?? []).map((situation) => <li key={situation.decisionId}>
              <div className="champion-situation__head">
                <span className="champion-situation__minute"><GameIcon name="clock" size={14} />{Math.floor(situation.timestampMs / 60000)}<small>min</small></span>
                <div><strong>{situation.matchId}</strong><span>Participante {situation.participantId}</span></div>
              </div>
              <div className="champion-situation-actions">
                <Link className="button champion-situation__play" to={`/decisions/${situation.decisionId}`}>Analisar decisão</Link>
                <Link className="arrow-link" to={`/historico/${situation.matchId}?participant=${situation.participantId}`}>Abrir contexto</Link>
              </div>
            </li>)}
          </ul> : <div className="champion-study__empty">
            <p>Nenhum desafio com este campeão no filtro ainda.</p>
            <Link className="arrow-link" to="/historico">Examinar partidas no Histórico</Link>
          </div>}
          <details className="champion-questions">
            <summary>Perguntas para guiar o estudo</summary>
            <ul>
              <li>Quais componentes já estavam no inventário e quais alternativas os aproveitam?</li>
              <li>Que ameaças a composição adversária apresentava naquele momento?</li>
              <li>O objetivo pede mais dano, sobrevivência ou utilidade? Que informação ainda falta?</li>
            </ul>
          </details>
        </section>
        </div>
        </>}
      </div>
    </>}
  </div>;
}
