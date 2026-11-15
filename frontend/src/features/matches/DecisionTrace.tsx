import { useEffect, useState } from "react";
import { api, type DecisionTrace as Trace, type TraceEvent } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";
import { useItemName } from "../../shared/hooks/useItemName";
import { ItemIcon } from "../../shared/ui/ItemIcon";
import { formatGameTime } from "./format";

/**
 * Da Riot até esta análise: o caminho do dado oficial até a decisão. O evento da linha do
 * tempo (Match-V5) como veio da API, sem o item comprado (modo Estudo); os eventos do jogador
 * nos 2 minutos anteriores; o quadro de ouro e nível; e quantos eventos refazem o inventário.
 */

const number = (value: number) => value.toLocaleString("pt-BR");

function clock(ms: number): string {
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
}

function EventLine({ event }: { event: TraceEvent }) {
  const itemName = useItemName();
  // Nome do Data Dragon ou do catálogo da decisão; sem nenhum dos dois, só o número do item.
  const resolved = event.itemId != null ? itemName({ itemId: event.itemId, name: event.itemName }) : null;
  const item = resolved && resolved.endsWith("nome indisponível") ? `o item ${event.itemId}` : resolved;
  const text =
    event.type === "ITEM_PURCHASED" ? `Comprou ${item ?? "um item"}`
      : event.type === "ITEM_SOLD" ? `Vendeu ${item ?? "um item"}`
        : event.type === "ITEM_DESTROYED" ? `${item ?? "Item"} saiu do inventário (consumido ou usado)`
          : event.type === "ITEM_UNDO" ? `Desfez a compra de ${item ?? "um item"}`
            : event.type === "LEVEL_UP" ? `Subiu para o nível ${event.level ?? "?"}`
              : event.type;
  return (
    <li className="trace-event">
      <span className="trace-event__time">{formatGameTime(event.timestampMs)}</span>
      <span className="trace-event__icon" aria-hidden="true">{event.itemId != null ? <ItemIcon itemId={event.itemId} /> : null}</span>
      <span className="trace-event__text">{text}</span>
      <code className="trace-event__type">{event.type}</code>
    </li>
  );
}

function rawJson(event: TraceEvent): string {
  const fields = Object.entries(event.raw).map(([key, value]) => `  "${key}": ${JSON.stringify(value)}`);
  for (const key of event.hidden ?? []) fields.push(`  "${key}": /* oculto até a resposta */`);
  return `{\n${fields.join(",\n")}\n}`;
}

export function DecisionTrace({ decisionId }: { decisionId: string }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api
      .getDecisionTrace(decisionId, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return;
        // Complemento da tela: resposta fora do formato esconde a seção, nunca derruba a decisão.
        if (Array.isArray(next?.nearby) && typeof next.timelineEvents === "number") setTrace(next);
        else setFailed(true);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && !isAbortError(error)) setFailed(true);
      });
    return () => controller.abort();
  }, [decisionId]);

  if (failed) return null; // complemento: a decisão continua utilizável sem ele
  if (!trace) {
    return (
      <section className="decision-trace surface" aria-busy="true" aria-label="Da Riot até esta análise">
        <p className="muted" role="status">Buscando os dados oficiais desta compra…</p>
      </section>
    );
  }
  const event = trace.event;
  const frame = trace.frame;
  const age = frame && event ? event.timestampMs - frame.timestampMs : null;
  return (
    <section className="decision-trace surface" aria-labelledby="trace-heading">
      <p className="decision-trace__kicker">Como esta análise foi montada</p>
      <h3 id="trace-heading">Da Riot até esta análise</h3>
      <p className="decision-trace__lead">
        Tudo abaixo vem da linha do tempo oficial que a API da Riot devolve para a partida <code>{trace.matchId}</code>.
      </p>
      <ol className="decision-trace__steps">
        <li className="trace-step">
          <span className="trace-step__number" aria-hidden="true">1</span>
          <div className="trace-step__body">
            <h4>Linha do tempo oficial da partida</h4>
            <p className="trace-step__detail">
              <span className="chip">Match-V5</span> {number(trace.timelineEvents)} eventos registrados pela Riot nesta partida
              {trace.frameCount ? ` e ${number(trace.frameCount)} quadros, um por minuto` : ""}.
            </p>
          </div>
        </li>
        {event ? (
          <li className="trace-step">
            <span className="trace-step__number" aria-hidden="true">2</span>
            <div className="trace-step__body">
              <h4>O evento desta compra, como a Riot mandou</h4>
              <p className="trace-step__detail">
                Evento nº {number(event.sourceIndex)} da linha do tempo, aos {clock(event.timestampMs)} de jogo.
              </p>
              <pre className="trace-raw" aria-label="Evento bruto da Riot"><code>{rawJson(event)}</code></pre>
              {event.hidden?.length ? (
                <p className="trace-step__note">O item desta compra fica oculto até você responder, para a escolha ser sua.</p>
              ) : null}
            </div>
          </li>
        ) : null}
        <li className="trace-step">
          <span className="trace-step__number" aria-hidden="true">{event ? 3 : 2}</span>
          <div className="trace-step__body">
            <h4>O que aconteceu nos 2 minutos antes</h4>
            {trace.nearby.length ? (
              <ul className="trace-events">
                {trace.nearby.map((item) => <EventLine key={item.sourceIndex} event={item} />)}
              </ul>
            ) : (
              <p className="trace-step__detail">Nenhuma compra, venda ou subida de nível deste jogador nesse intervalo.</p>
            )}
          </div>
        </li>
        <li className="trace-step">
          <span className="trace-step__number" aria-hidden="true">{event ? 4 : 3}</span>
          <div className="trace-step__body">
            <h4>Ouro e nível no último quadro</h4>
            {frame ? (
              <p className="trace-step__detail">
                Quadro de {formatGameTime(frame.timestampMs)}
                {frame.gold != null ? ` · ${number(frame.gold)} de ouro acumulado` : ""}
                {frame.level != null ? ` · nível ${frame.level}` : ""}
                {frame.cs != null ? ` · ${number(frame.cs)} tropas` : ""}
                {age != null ? ` · ${number(Math.round(age / 1000))} s antes da compra` : ""}.
              </p>
            ) : (
              <p className="trace-step__detail">Os quadros desta partida ainda não foram lidos; o orçamento usa a melhor estimativa disponível.</p>
            )}
          </div>
        </li>
        <li className="trace-step">
          <span className="trace-step__number" aria-hidden="true">{event ? 5 : 4}</span>
          <div className="trace-step__body">
            <h4>Inventário refeito, evento por evento</h4>
            <p className="trace-step__detail">
              {number(trace.participantItemEvents)} {trace.participantItemEvents === 1 ? "evento de item" : "eventos de item"} deste jogador, do
              início da partida até aqui, refazem o inventário mostrado acima. As alternativas comparadas partem dele e desse ouro.
            </p>
          </div>
        </li>
      </ol>
    </section>
  );
}
