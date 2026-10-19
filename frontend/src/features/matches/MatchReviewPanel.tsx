import { useEffect, useState } from "react";
import { api, type MatchParticipant, type MatchReview, type ReviewPurchase } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";
import { useItemName } from "../../shared/hooks/useItemName";
import { Link } from "../../shared/router/Link";
import { ItemIcon } from "../../shared/ui/ItemIcon";
import { count } from "../../shared/text/count";
import { championName, formatGameTime, roleLabel } from "./format";
import { queueLabel } from "../history/presentation";

const LABEL_TEXT: Record<NonNullable<ReviewPurchase["label"]>, string> = {
  COMMON: "Comum",
  ALTERNATIVE: "Alternativa",
  UNCOMMON: "Incomum",
  NO_REFERENCE: "Sem referência",
};
const SKILL_TEXT = {
  MOST_COMMON: "a mais comum",
  ALTERNATIVE: "uma alternativa frequente",
  UNCOMMON: "pouco comum",
  NO_REFERENCE: "sem referência suficiente",
} as const;
const MOMENT_TEXT: Record<string, string> = {
  DEATH: "você morreu",
  KILL: "você abateu",
  ASSIST: "você participou de um abate",
  CHAMPION_KILL: "abate",
  DRAGON: "dragão",
  BARON: "Barão",
  HERALD: "Arauto",
  VOIDGRUB: "Vastilarvas",
  ATAKHAN: "Atakhan",
  TOWER: "torre",
  INHIBITOR: "inibidor",
};

function percent(value: number | null | undefined): string {
  return value == null ? "–" : `${Math.round(value * 100)}%`;
}

function useReview(matchId: string, participantId: number) {
  const [state, setState] = useState<{ key: string; review: MatchReview | null; failed: boolean } | null>(null);
  const key = `${matchId}#${participantId}`;
  useEffect(() => {
    const controller = new AbortController();
    api
      .getMatchReview(matchId, participantId, controller.signal)
      .then((review) => setState({ key, review, failed: false }))
      .catch((error: unknown) => {
        if (!isAbortError(error)) setState({ key, review: null, failed: true });
      });
    return () => controller.abort();
  }, [matchId, participantId, key]);
  return state?.key === key ? state : null;
}

function PurchaseRow({ purchase }: { purchase: ReviewPurchase }) {
  const itemName = useItemName();
  const name = itemName({ itemId: purchase.itemId });
  const ready = purchase.status === "READY";
  const tone = ready ? (purchase.label ?? "NO_REFERENCE").toLowerCase() : "not-ready";
  const top = (purchase.referenceTop ?? []).filter((item) => item.itemId !== purchase.itemId);
  return (
    <li className={`review-purchase review-purchase--${tone}`}>
      <span className="review-purchase__order">{purchase.ordinal}º item</span>
      <span className="review-purchase__item">
        <ItemIcon itemId={purchase.itemId} />
        <span>
          <strong>{name}</strong>
          <small> aos {formatGameTime(purchase.timestampMs)}</small>
        </span>
      </span>
      <span className="review-purchase__label">
        {ready ? LABEL_TEXT[purchase.label ?? "NO_REFERENCE"] : "Fora da análise"}
      </span>
      <span className="review-purchase__detail">
        {!ready
          ? "O inventário antes desta compra não pôde ser confirmado."
          : purchase.label === "NO_REFERENCE" || purchase.label == null
            ? "Poucos jogadores deste campeão e rota chegaram a esta etapa na referência."
            : `Escolhida por ${percent(purchase.share)} de ${count(purchase.support ?? 0, "jogador", "jogadores")} nesta etapa${purchase.rank ? ` (${purchase.rank}ª mais comum)` : ""}.`}
        {ready && top.length ? (
          <span className="review-purchase__top">
            {" "}Mais comuns: {top.map((item) => `${itemName({ itemId: item.itemId })} ${percent(item.share)}`).join(", ")}.
          </span>
        ) : null}
      </span>
      <Link to={{ name: "decision", decisionId: purchase.decisionId }} className="review-purchase__open">
        Ver decisão
      </Link>
    </li>
  );
}

function WinChart({ review }: { review: MatchReview }) {
  const win = review.winProbability;
  if (!win || win.points.length < 2) return null;
  const width = 640;
  const height = 180;
  const pad = { top: 10, right: 10, bottom: 24, left: 36 };
  const last = win.points.at(-1)!.timestampMs;
  const x = (ms: number) => pad.left + (ms / last) * (width - pad.left - pad.right);
  const y = (p: number) => pad.top + (1 - p) * (height - pad.top - pad.bottom);
  const line = win.points.map((p, i) => `${i ? "L" : "M"}${x(p.timestampMs).toFixed(1)},${y(p.probability).toFixed(1)}`).join(" ");
  const finalProbability = win.points.at(-1)!.probability;
  return (
    <figure className="review-win">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Chance estimada de vitória do seu time ao longo da partida; no fim, ${percent(finalProbability)}.`}
      >
        <line x1={pad.left} x2={width - pad.right} y1={y(0.5)} y2={y(0.5)} className="review-win__half" />
        {[0, 0.5, 1].map((tick) => (
          <text key={tick} x={pad.left - 6} y={y(tick) + 4} textAnchor="end" className="review-win__tick">
            {percent(tick)}
          </text>
        ))}
        <path d={line} className="review-win__line" />
        {win.keyMoments.map((moment) => (
          <circle
            key={moment.minute}
            cx={x(moment.minute * 60_000)}
            cy={y(moment.probabilityAfter)}
            r={4}
            className={moment.delta < 0 ? "review-win__moment review-win__moment--down" : "review-win__moment"}
          />
        ))}
      </svg>
      <figcaption className="muted">
        Estimativa a partir do ouro, XP, abates e objetivos de partidas parecidas do 16.19. Mostra quando a
        chance mudou, não o porquê.
      </figcaption>
    </figure>
  );
}

const PLURAL: Record<string, string> = { abate: "abates", "dragão": "dragões", torre: "torres", "inibidor": "inibidores" };

function momentText(review: MatchReview, moment: NonNullable<MatchReview["winProbability"]>["keyMoments"][number]): string {
  const ownTeam = review.winProbability?.team;
  const counts = new Map<string, number>();
  for (const event of moment.events) {
    const base = MOMENT_TEXT[event.kind] ?? event.kind.toLowerCase();
    const personal = event.kind === "KILL" || event.kind === "ASSIST" || event.kind === "DEATH";
    const key = personal || event.team == null ? base : `${base}|${event.team === ownTeam ? "do seu time" : "do adversário"}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const parts = [...counts.entries()].map(([key, n]) => {
    const [base, side] = key.split("|");
    const noun = n > 1 ? `${n} ${PLURAL[base] ?? base}` : base;
    return side ? `${noun} ${side}` : n > 1 && !PLURAL[base] ? `${base} (${n}×)` : noun;
  });
  return parts.length ? parts.join(", ") : "sem abate ou objetivo registrado no minuto";
}

export function MatchReviewPanel({
  matchId,
  participant,
  participants,
}: {
  matchId: string;
  participant: MatchParticipant;
  participants: MatchParticipant[];
}) {
  const state = useReview(matchId, participant.participantId);
  if (state === null) {
    return (
      <section className="match-panel review" aria-busy="true" aria-labelledby="review-heading">
        <h3 id="review-heading">Revisão da partida</h3>
        <p className="muted">Carregando a revisão…</p>
      </section>
    );
  }
  if (state.failed || state.review === null) {
    return (
      <section className="match-panel review" aria-labelledby="review-heading">
        <h3 id="review-heading">Revisão da partida</h3>
        <p className="muted">A revisão não pôde ser carregada agora.</p>
      </section>
    );
  }
  const review = state.review;
  const opponent = review.lane ? participants.find((p) => p.participantId === review.lane?.opponentId) : null;
  const { counts } = review;
  return (
    <section className="match-panel review" aria-labelledby="review-heading">
      <h3 id="review-heading">Revisão da partida</h3>
      <p className="review__lead">
        {championName(participant)} ({roleLabel(review.role)}) comparado com jogadores do mesmo campeão e rota.
        {review.referenceVersion
          ? review.referenceInherited
            ? ` Referência do 16.19 Solo/Duo; esta partida é do ${review.patch} (${queueLabel(review.queue)}).`
            : " Referência do 16.19 Solo/Duo."
          : " A referência ainda não está disponível neste ambiente."}{" "}
        Comum não quer dizer certo: mostra o que jogadores parecidos costumam fazer.
      </p>

      <h4>Compras</h4>
      {review.purchases.length ? (
        <>
          <p className="review__summary">
            {count(counts.ready, "compra analisada", "compras analisadas")} de {counts.purchases}:{" "}
            {count(counts.common, "comum", "comuns")}, {count(counts.alternative, "alternativa", "alternativas")},{" "}
            {count(counts.uncommon, "incomum", "incomuns")}
            {counts.noReference ? `, ${counts.noReference} sem referência` : ""}.
          </p>
          <ul className="review-purchases">
            {review.purchases.map((purchase) => (
              <PurchaseRow key={purchase.decisionId} purchase={purchase} />
            ))}
          </ul>
        </>
      ) : (
        <p className="muted">Nenhuma compra de item principal até o 5º item nesta partida.</p>
      )}

      <h4>Habilidades</h4>
      {review.skills?.priority ? (
        <p>
          Prioridade de nível máximo: <strong>{review.skills.priority.replaceAll(">", " › ")}</strong>,{" "}
          {SKILL_TEXT[review.skills.label]}
          {review.skills.share != null ? ` (${percent(review.skills.share)} dos jogadores)` : ""}
          {review.skills.label !== "MOST_COMMON" && review.skills.mostCommon?.[0]
            ? `. A mais comum é ${review.skills.mostCommon[0].priority.replaceAll(">", " › ")} (${percent(review.skills.mostCommon[0].share)})`
            : ""}
          .
        </p>
      ) : (
        <p className="muted">A partida acabou antes de uma habilidade chegar ao nível máximo.</p>
      )}

      <h4>Fase de rotas</h4>
      {review.lane && review.lane.minutes.length && opponent ? (
        <table className="review-lane">
          <caption className="sr-only">Você contra {championName(opponent)} aos 10 e 15 minutos</caption>
          <thead>
            <tr>
              <th scope="col"><abbr title="Minuto">Min.</abbr></th>
              <th scope="col">Ouro</th>
              <th scope="col">XP</th>
              <th scope="col">Farm</th>
            </tr>
          </thead>
          <tbody>
            {review.lane.minutes.map((minute) => (
              <tr key={minute.minute}>
                <th scope="row">{minute.minute} min</th>
                {(["gold", "xp", "cs"] as const).map((field) => {
                  const diff = minute.own[field] - minute.opponent[field];
                  return (
                    <td key={field} className={diff > 0 ? "review-lane--ahead" : diff < 0 ? "review-lane--behind" : undefined}>
                      {minute.own[field].toLocaleString("pt-BR")}{" "}
                      <small>
                        ({diff > 0 ? "+" : ""}
                        {diff.toLocaleString("pt-BR")} vs {championName(opponent)})
                      </small>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">
          {review.lane === null
            ? "Sem oponente direto identificado ou sem quadros de XP e farm (prepare a partida de novo)."
            : "A partida não chegou aos 10 minutos."}
        </p>
      )}

      <h4>Chance de vitória</h4>
      {review.winProbability ? (
        <>
          <WinChart review={review} />
          {review.winProbability.keyMoments.length ? (
            <ol className="review-moments">
              {review.winProbability.keyMoments.map((moment) => (
                <li key={moment.minute}>
                  <strong>{moment.minute} min</strong>: {moment.delta > 0 ? "+" : "−"}
                  {Math.abs(Math.round(moment.delta * 100))} pontos percentuais ({momentText(review, moment)}).
                </li>
              ))}
            </ol>
          ) : (
            <p className="muted">Nenhuma virada grande de um minuto para o outro.</p>
          )}
        </>
      ) : (
        <p className="muted">
          {review.winProbabilityUnavailable === "FRAMES_WITHOUT_XP"
            ? "O gráfico precisa dos quadros com XP: prepare a partida de novo."
            : "O gráfico de chance de vitória não está disponível neste ambiente."}
        </p>
      )}
    </section>
  );
}
