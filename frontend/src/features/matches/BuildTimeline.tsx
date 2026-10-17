import type { DecisionSummary, MatchTimeline } from "../../shared/api/client";
import { useItemName } from "../../shared/hooks/useItemName";
import { Link } from "../../shared/router/Link";
import { ItemIcon } from "../../shared/ui/ItemIcon";
import { formatGameTime, ordinalLabel, reasonText } from "./format";
import { decisionsInVisit, isCompletedItem, maxOrder, shopVisits, SKILL_KEYS, skillOrder } from "./matchView";

type ParticipantTimeline = MatchTimeline["participants"][number];

export function DecisionChip({ decision }: { decision: DecisionSummary }) {
  const ordinal = ordinalLabel(decision.mainItemOrdinal);
  const why = (decision.reasonCodes ?? []).map(reasonText).join(" ");
  return (
    <Link
      to={{ name: "decision", decisionId: decision.decisionId }}
      className={`decision-chip decision-chip--${decision.eligible ? "eligible" : "blocked"}`}
      aria-label={`Decisão em ${formatGameTime(decision.timestampMs)}`}
      title={decision.eligible ? undefined : why}
    >
      <span className="decision-chip__time">{formatGameTime(decision.timestampMs)}</span>
      <span className="decision-chip__state">{decision.eligible ? "Analisar" : "Bloqueada"}</span>
      {ordinal ? <span>{ordinal}</span> : null}
    </Link>
  );
}

/** Compras na ordem do tempo, agrupadas por volta à loja, com as decisões marcadas. */
export function BuildTimeline({
  participant,
  decisions,
}: {
  participant: ParticipantTimeline | null;
  decisions: DecisionSummary[];
}) {
  const itemName = useItemName();
  const visits = shopVisits(participant?.items ?? []);
  const placed = new Set<string>();

  if (!visits.length) {
    return <p className="muted">Nenhuma compra registrada para este participante.</p>;
  }

  return (
    <>
      <ol className="build-path" aria-label="Compras por volta à loja">
        {visits.map((visit) => {
          const inVisit = decisionsInVisit(visit, decisions);
          inVisit.forEach((d) => placed.add(d.decisionId));
          const completed = visit.items.some((item) => item.kind === "PURCHASE" && isCompletedItem(item));
          return (
            <li key={`${visit.startMs}-${visit.items[0].itemId}`} className={`build-path__visit${completed ? " build-path__visit--key" : ""}`}>
              <time className="build-path__time">{formatGameTime(visit.startMs)}</time>
              <ul className="build-path__items">
                {visit.items.map((item, index) => (
                  <li
                    key={`${item.timestampMs}-${item.itemId}-${index}`}
                    className={`build-item${isCompletedItem(item) ? " build-item--completed" : ""}${item.kind === "SOLD" ? " build-item--sold" : ""}`}
                    title={`${itemName({ itemId: item.itemId })}${item.kind === "SOLD" ? " (vendido)" : ""} · ${formatGameTime(item.timestampMs)}`}
                  >
                    <ItemIcon itemId={item.itemId} />
                    <span className="sr-only">
                      {item.kind === "SOLD" ? "Vendeu " : ""}
                      {itemName({ itemId: item.itemId })}
                    </span>
                  </li>
                ))}
              </ul>
              {inVisit.length ? (
                <div className="build-path__decisions">
                  {inVisit.map((decision) => <DecisionChip key={decision.decisionId} decision={decision} />)}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
      {decisions.some((d) => !placed.has(d.decisionId)) ? (
        <div className="build-path__orphans">
          <span className="muted">Outras decisões:</span>
          {decisions.filter((d) => !placed.has(d.decisionId)).map((decision) => (
            <DecisionChip key={decision.decisionId} decision={decision} />
          ))}
        </div>
      ) : null}
    </>
  );
}

/** Grade nível × habilidade, como nos rastreadores (Q/W/E/R por nível). */
export function SkillOrder({ participant }: { participant: ParticipantTimeline | null }) {
  const order = skillOrder(participant?.skills ?? []);
  if (!order.length) return <p className="muted">Sem subidas de habilidade registradas.</p>;
  const priority = maxOrder(order);
  return (
    <div className="skill-order">
      {priority.length ? (
        <p className="skill-order__priority">
          Maximizou{" "}
          {priority.map((slot, index) => (
            <span key={slot}>
              {index ? <span aria-hidden="true"> › </span> : null}
              <kbd>{SKILL_KEYS[slot - 1]}</kbd>
            </span>
          ))}
        </p>
      ) : null}
      <table className="skill-order__grid">
        <caption className="sr-only">Habilidade aprendida em cada nível</caption>
        <thead>
          <tr>
            <th scope="col"><span className="sr-only">Habilidade</span></th>
            {order.map((_, index) => <th key={index} scope="col">{index + 1}</th>)}
          </tr>
        </thead>
        <tbody>
          {SKILL_KEYS.map((key, row) => (
            <tr key={key}>
              <th scope="row">{key}</th>
              {order.map((slot, index) => (
                <td key={index} className={slot === row + 1 ? `skill-order__cell skill-order__cell--${key.toLowerCase()}` : "skill-order__cell"}>
                  {slot === row + 1 ? <span>{index + 1}</span> : null}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
