import { useEffect, useState } from "react";
import { api, type LearningAttemptSummary, type LearningProgress } from "../../shared/api/client";
import { isAbortError } from "../../shared/api/errors";
import { ErrorNotice } from "../../shared/ui/ErrorNotice";
import { count } from "../../shared/text/count";
import { weekLabel, weeklyBuckets, type WeekBucket } from "./evolution";
import "./study.css";
import { AchievementGrid, MissionList, StreakBadge, XpBar } from "./Gamification";
import { useGamification } from "./useGamification";
import { GameIcon } from "../../shared/ui/GameIcon";

const PAGE_SIZE = 50;
// Teto de segurança: a evolução é sobre tendência recente, não um relatório histórico
// completo — além disto, pede para abrir a Biblioteca de decisões para ver o resto.
const MAX_ATTEMPTS = 500;

function Bars({ buckets }: { buckets: WeekBucket[] }) {
  const max = Math.max(1, ...buckets.map((b) => b.attempts));
  return (
    <ul className="evolution-bars" aria-label="Tentativas por semana">
      {buckets.map((bucket) => (
        <li key={bucket.weekStart} className="evolution-bars__row">
          <span className="evolution-bars__label">{weekLabel(bucket.weekStart)}</span>
          <span className="evolution-bars__track" aria-hidden="true">
            <span className="evolution-bars__fill" style={{ width: `${(bucket.attempts / max) * 100}%` }} />
            {bucket.reflections > 0 ? (
              <span
                className="evolution-bars__fill evolution-bars__fill--reflection"
                style={{ width: `${(bucket.reflections / max) * 100}%` }}
              />
            ) : null}
          </span>
          <span className="evolution-bars__value">
            {count(bucket.attempts, "tentativa", "tentativas")}
            {bucket.reflections > 0 ? ` · ${count(bucket.reflections, "reflexão", "reflexões")}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Minha evolução: a mesma lista da Biblioteca de decisões, agrupada por semana — sem
 * endpoint novo além do de listagem já usado ali. */
export function MyEvolutionPage() {
  const [progress, setProgress] = useState<LearningProgress | null>(null);
  const [attempts, setAttempts] = useState<LearningAttemptSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api.getProgress(controller.signal).then(setProgress).catch(() => undefined);

    async function loadAttempts() {
      const collected: LearningAttemptSummary[] = [];
      let cursor: string | null = null;
      do {
        const page = await api.listAttempts({ cursor, limit: PAGE_SIZE }, controller.signal);
        collected.push(...page.items);
        cursor = page.nextCursor ?? null;
      } while (cursor !== null && collected.length < MAX_ATTEMPTS);
      if (!controller.signal.aborted) {
        setAttempts(collected);
        setError(null);
      }
    }
    loadAttempts().catch((caught: unknown) => {
      if (!controller.signal.aborted && !isAbortError(caught)) {
        setAttempts(null);
        setError(caught);
      }
    });
    return () => controller.abort();
  }, [retry]);

  const game = useGamification();
  const totals = progress ? progress.study.attempts + progress.transfer.attempts : null;
  const totalReflections = progress ? progress.study.reflections + progress.transfer.reflections : null;
  const buckets = attempts ? weeklyBuckets(attempts, 12) : null;

  return (
    <div className="area-page study-page">
      <header className="page-head">
        <p className="page-head__context">Modo estudo</p>
        <h2>Minha evolução</h2>
        <p className="page-head__lead">
          Decisões revistas e reflexões escritas ao longo do tempo — contagem local, não é nota
          nem estimativa de habilidade.
        </p>
      </header>

      {game ? (
        <section className="evolution-game" aria-label="Nível, sequência e missões">
          <div className="surface surface--pad evolution-game__level">
            <XpBar game={game} />
            <StreakBadge game={game} />
          </div>
          <div className="surface surface--pad evolution-game__missions">
            <h3 className="evolution-game__title"><GameIcon name="target" size={16} /> Missões da semana</h3>
            <MissionList missions={game.missions} />
          </div>
          <div className="surface surface--pad evolution-game__achievements">
            <h3 className="evolution-game__title"><GameIcon name="trophy" size={16} /> Conquistas <span className="muted">{game.achievements.filter((a) => a.unlocked).length}/{game.achievements.length}</span></h3>
            <AchievementGrid achievements={game.achievements} />
          </div>
        </section>
      ) : null}

      {totals !== null ? (
        <div className="panel-metrics__row">
          <div className="metric">
            <p className="metric__label">Tentativas no total</p>
            <p className="metric__value">{totals}</p>
          </div>
          <div className="metric">
            <p className="metric__label">Com reflexão</p>
            <p className="metric__value">{totalReflections}</p>
          </div>
        </div>
      ) : null}

      {error ? <ErrorNotice error={error} onRetry={() => setRetry((n) => n + 1)} retryLabel="Tentar novamente" /> : null}
      {!error && buckets === null ? (
        <div className="study-state" role="status" aria-busy="true">Carregando…</div>
      ) : null}
      {!error && buckets !== null ? (
        buckets.length ? (
          <section className="surface surface--pad" aria-labelledby="evolution-heading">
            <h3 id="evolution-heading" className="sr-only">Tentativas por semana</h3>
            <Bars buckets={buckets} />
          </section>
        ) : (
          <div className="empty-state">
            <h4>Nada ainda nesta janela</h4>
            <p>Suas tentativas recentes do modo estudo aparecem aqui, por semana.</p>
          </div>
        )
      ) : null}
    </div>
  );
}
