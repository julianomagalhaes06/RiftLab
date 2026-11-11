import type { DecisionSummary, LearningGamification, LearningGoal } from "../../shared/api/client";
import { useGamification } from "./useGamification";
import { Link } from "../../shared/router/Link";
import { GameIcon, type GameIconName } from "../../shared/ui/GameIcon";
import "./gamification.css";

/** Peças visuais da gamificação do estudo (dados em useGamification). */
/** Anel de XP do nível atual (decorativo: os números ficam no texto acessível). */
function XpRing({ value, target, size = 30 }: { value: number; target: number; size?: number }) {
  const radius = size / 2 - 3;
  const length = 2 * Math.PI * radius;
  const share = target ? Math.min(1, value / target) : 0;
  return (
    <svg className="xp-ring" viewBox={`0 0 ${size} ${size}`} width={size} height={size} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={radius} className="xp-ring__track" />
      <circle cx={size / 2} cy={size / 2} r={radius} className="xp-ring__fill" strokeDasharray={`${share * length} ${length}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
    </svg>
  );
}

/** Selo do topo: nível com anel de XP e a sequência; leva a Minha evolução. */
export function LevelChip() {
  const game = useGamification();
  if (!game) return null;
  const label = `Nível ${game.level}, ${game.levelXp} de ${game.levelTarget} XP para o próximo; sequência de ${game.streak.current} ${game.streak.current === 1 ? "dia" : "dias"}`;
  return (
    <Link to={{ name: "myEvolution" }} className="level-chip" aria-label={label} title={label}>
      <span className="level-chip__ring">
        <XpRing value={game.levelXp} target={game.levelTarget} />
        <span className="level-chip__level" aria-hidden="true">{game.level}</span>
      </span>
      <span className={game.streak.current ? "level-chip__streak level-chip__streak--on" : "level-chip__streak"} aria-hidden="true">
        <GameIcon name="flame" size={14} />{game.streak.current}
      </span>
    </Link>
  );
}

export function XpBar({ game }: { game: LearningGamification }) {
  const share = game.levelTarget ? Math.min(100, (game.levelXp / game.levelTarget) * 100) : 0;
  return (
    <div className="xp-bar">
      <div className="xp-bar__labels">
        <span className="xp-bar__level">Nível {game.level}</span>
        <span className="xp-bar__count">{game.levelXp} / {game.levelTarget} XP</span>
      </div>
      <div className="xp-bar__track" aria-hidden="true"><span style={{ width: `${share}%` }} /></div>
      <p className="xp-bar__total">{game.xp} XP no total · {game.studiedDecisions} {game.studiedDecisions === 1 ? "decisão estudada" : "decisões estudadas"}</p>
    </div>
  );
}

export function StreakBadge({ game }: { game: LearningGamification }) {
  const { current, best, studiedToday } = game.streak;
  return (
    <div className={current ? "streak-badge streak-badge--on" : "streak-badge"}>
      <GameIcon name="flame" size={22} />
      <div>
        <p className="streak-badge__value"><strong>{current}</strong> {current === 1 ? "dia seguido" : "dias seguidos"}</p>
        <p className="streak-badge__hint">
          {current === 0 ? "Estude uma decisão hoje para começar." : studiedToday ? `Hoje já conta. Recorde: ${best}.` : `Estude hoje para não perder. Recorde: ${best}.`}
        </p>
      </div>
    </div>
  );
}

function GoalProgress({ goal }: { goal: LearningGoal }) {
  const done = goal.completed ?? goal.unlocked ?? false;
  return (
    <div className="goal-progress" aria-hidden="true">
      <span style={{ width: `${Math.min(100, (goal.progress / goal.target) * 100)}%` }} className={done ? "is-done" : undefined} />
    </div>
  );
}

export function MissionList({ missions }: { missions: LearningGoal[] }) {
  return (
    <ul className="mission-list" aria-label="Missões da semana">
      {missions.map((mission) => (
        <li key={mission.key} className={mission.completed ? "mission mission--done" : "mission"}>
          <span className="mission__icon"><GameIcon name={mission.completed ? "star" : "target"} size={16} /></span>
          <div className="mission__body">
            <p className="mission__title">{mission.title}</p>
            <p className="mission__desc">{mission.description}</p>
            <GoalProgress goal={mission} />
          </div>
          <span className="mission__count">{mission.completed ? "Concluída" : `${mission.progress}/${mission.target}`}</span>
        </li>
      ))}
    </ul>
  );
}

const ACHIEVEMENT_ICON: Record<string, GameIconName> = {
  first_study: "target", first_reflection: "book", study_10: "chart", study_25: "chart", study_50: "trophy",
  no_hint_5: "crosshair", streak_3: "flame", streak_7: "flame", full_match: "map", learn_from_loss: "sword",
};

export function AchievementGrid({ achievements }: { achievements: LearningGoal[] }) {
  return (
    <ul className="achievement-grid" aria-label="Conquistas">
      {achievements.map((achievement) => (
        <li key={achievement.key} className={achievement.unlocked ? "achievement achievement--unlocked" : "achievement"}>
          <span className="achievement__medal"><GameIcon name={ACHIEVEMENT_ICON[achievement.key] ?? "star"} size={22} /></span>
          <p className="achievement__title">{achievement.title}</p>
          <p className="achievement__desc">{achievement.description}</p>
          {achievement.unlocked ? <p className="achievement__state">Conquistada</p> : (
            <>
              <GoalProgress goal={achievement} />
              <p className="achievement__state">{achievement.progress}/{achievement.target}</p>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Barra de estudo da partida na análise: quantas decisões analisáveis deste participante você
 * já estudou e o XP feito nelas; selo quando estudou todas. As decisões vêm da aba Compras (a
 * mesma lista já carregada), sem uma segunda busca. */
export function MatchStudyBar({ matchId, decisions }: { matchId: string; decisions: DecisionSummary[] | null }) {
  const game = useGamification(matchId);
  if (!game || !decisions) return null;
  const eligible = decisions.filter((decision) => decision.eligible);
  if (!eligible.length) return null;
  const studiedIds = new Set(game.match?.studiedDecisionIds ?? []);
  const studied = eligible.filter((decision) => studiedIds.has(decision.decisionId)).length;
  const complete = studied === eligible.length;
  return (
    <section className={complete ? "match-study match-study--complete" : "match-study"} aria-label="Seu estudo desta partida">
      <GameIcon name={complete ? "trophy" : "book"} size={20} />
      <div className="match-study__body">
        <p className="match-study__title">
          {complete ? "Partida concluída" : "Partida estudada"}: <strong>{studied} de {eligible.length}</strong> {eligible.length === 1 ? "decisão analisável" : "decisões analisáveis"}
          {game.match?.xp ? <span className="match-study__xp"> · +{game.match.xp} XP</span> : null}
        </p>
        <div className="match-study__track" aria-hidden="true"><span style={{ width: `${(studied / eligible.length) * 100}%` }} /></div>
      </div>
      <Link to={{ name: "myEvolution" }} className="match-study__level">Nível {game.level}</Link>
    </section>
  );
}
