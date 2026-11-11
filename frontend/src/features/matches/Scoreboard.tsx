import { useState } from "react";
import type { MatchDetail, MatchParticipant, MatchTimeline } from "../../shared/api/client";
import { useGameAssets, type GameAsset, type GameAssets } from "../../shared/ui/gameAssets";
import { isRealMatchId, routeToPath } from "../../shared/router/routes";
import { useItemName } from "../../shared/hooks/useItemName";
import { Link } from "../../shared/router/Link";
import { ItemIcon } from "../../shared/ui/ItemIcon";
import { ChampionAvatar } from "../history/ChampionAvatar";
import { decimal, percent, roleShort } from "../history/presentation";
import { championName } from "./format";
import { BLUE, completedBuild, teamTotals } from "./matchView";

const ROLE_ORDER: Readonly<Record<string, number>> = { TOP: 0, JUNGLE: 1, MIDDLE: 2, BOTTOM: 3, UTILITY: 4 };

type DetailParticipant = MatchDetail["participants"][number];

function ordered(members: DetailParticipant[]): DetailParticipant[] {
  const rank = (p: DetailParticipant) => (p.role && p.role in ROLE_ORDER ? ROLE_ORDER[p.role] : 9);
  return [...members].sort((a, b) => rank(a) - rank(b) || a.participantId - b.participantId);
}

/** Itens em slots (grade 3 × 2 com casas vazias marcadas) e o amuleto redondo ao lado, como nos
 * sites de estatísticas; sem inventário oficial, a build estimada pela linha do tempo. */
function Build({ items, participant }: { items: MatchTimeline["participants"][number]["items"]; participant: MatchParticipant }) {
  const itemName = useItemName();
  const official = participant.finalItems != null;
  const build = official
    ? participant.finalItems!.map((itemId) => ({ itemId }))
    : completedBuild(items ?? []);
  const trinket = official ? participant.finalTrinketItemId : null;
  const source = official ? "Inventário final oficial" : "Estimado pela linha do tempo";
  if (!build.length && !trinket) {
    return <span className="scoreboard__build" role="group" aria-label={source} title={source}><span className="muted">—</span></span>;
  }
  const slots = [...build.slice(0, 6), ...Array.from({ length: Math.max(0, 6 - build.length) }, () => null)];
  return (
    <span className="scoreboard__build" role="group" aria-label={source} title={source}>
      <span className="scoreboard__slots">
        {slots.map((item, index) => item ? (
          <span key={`${item.itemId}-${index}`} title={itemName({ itemId: item.itemId })}>
            <ItemIcon itemId={item.itemId} />
            <span className="sr-only">{itemName({ itemId: item.itemId })}</span>
          </span>
        ) : <span key={`empty-${index}`} className="scoreboard__slot-empty" aria-hidden="true" />)}
      </span>
      {trinket ? (
        <span className="scoreboard__trinket" role="group" aria-label="Amuleto final oficial">
          <span title={`Amuleto: ${itemName({ itemId: trinket })}`}>
            <ItemIcon itemId={trinket} />
            <span className="sr-only">{itemName({ itemId: trinket })}</span>
          </span>
        </span>
      ) : null}
    </span>
  );
}

/** Ícone de feitiço ou runa do Data Dragon; sem catálogo, casa vazia com o rótulo genérico. */
function AssetIcon({ asset, label, className }: { asset: GameAsset | undefined; label: string; className: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <span className={className} title={asset?.name ?? label}>
      {asset && !failed ? <img src={asset.url} alt="" width={20} height={20} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(true)} /> : null}
      <span className="sr-only">{asset?.name ?? label}</span>
    </span>
  );
}

/** Feitiços (dois, empilhados) e runas (runa-chave e estilo secundário), como no u.gg. */
function Loadout({ participant, assets }: { participant: DetailParticipant; assets: GameAssets }) {
  const spells = participant.summonerSpells;
  const runes = participant.runes;
  if (!spells && !runes) return null;
  return (
    <span className="scoreboard__loadout">
      {spells ? (
        <span className="scoreboard__spells" role="group" aria-label="Feitiços">
          {spells.map((id, index) => <AssetIcon key={`${id}-${index}`} asset={assets.spells[id]} label="Feitiço" className="scoreboard__spell" />)}
        </span>
      ) : null}
      {runes ? (
        <span className="scoreboard__runes" role="group" aria-label="Runas">
          <AssetIcon asset={assets.runes[runes.keystone]} label="Runa-chave" className="scoreboard__keystone" />
          {runes.secondaryStyle ? <AssetIcon asset={assets.runes[runes.secondaryStyle]} label="Estilo secundário" className="scoreboard__rune-style" /> : null}
        </span>
      ) : null}
    </span>
  );
}

const DETAILED_KEY = "riftlab:placar-detalhado";

/** Preferência do placar detalhado, só neste navegador (conveniência; sem armazenamento, compacto). */
function readDetailed(): boolean {
  try {
    return window.localStorage.getItem(DETAILED_KEY) === "1";
  } catch {
    return false;
  }
}

function saveDetailed(value: boolean): void {
  try {
    window.localStorage.setItem(DETAILED_KEY, value ? "1" : "0");
  } catch {
    // Armazenamento bloqueado: vale até recarregar a página.
  }
}

function thousands(value: number): string {
  return value.toLocaleString("pt-BR");
}

function Row({
  match,
  participant,
  teamKills,
  timeline,
  selected,
  localId,
  fromHistory,
  assets,
  maxDamage,
  stayOnScoreboard,
}: {
  match: MatchDetail;
  participant: DetailParticipant;
  teamKills: number | null;
  timeline: MatchTimeline | null;
  selected: boolean;
  localId: number | null;
  fromHistory: boolean;
  assets: GameAssets;
  maxDamage: number;
  stayOnScoreboard: boolean;
}) {
  const { kills, deaths, assists } = participant;
  const scored = kills != null && deaths != null && assists != null;
  const cs =
    participant.totalMinionsKilled != null || participant.neutralMinionsKilled != null
      ? (participant.totalMinionsKilled ?? 0) + (participant.neutralMinionsKilled ?? 0)
      : null;
  const minutes = match.durationSeconds / 60;
  const role = roleShort(participant.role);
  const items = timeline?.participants.find((p) => p.participantId === participant.participantId)?.items ?? [];
  return (
    <tr className={`scoreboard__row${selected ? " scoreboard__row--selected" : ""}${participant.participantId === localId ? " scoreboard__row--local" : ""}`}>
      <th scope="row" className="scoreboard__champion">
        <div className="scoreboard__who">
        {/* Com as abas da análise, escolher um jogador mantém o Placar aberto (o foco não some);
            Revisão e Compras passam a mostrar o escolhido. */}
        <Link
          to={`${routeToPath({ name: fromHistory ? "historyMatch" : "match", matchId: match.matchId, participantId: participant.participantId })}${stayOnScoreboard ? "&aba=placar" : ""}`}
          className="scoreboard__link"
          aria-label={`Participante ${participant.participantId} — ${championName(participant)}`}
          aria-current={selected ? "true" : undefined}
        >
          <span className="scoreboard__portrait">
            <ChampionAvatar participant={participant} size={40} />
            {participant.champLevel != null ? <span className="scoreboard__level" aria-label={`Nível ${participant.champLevel}`}>{participant.champLevel}</span> : null}
          </span>
          <span className="scoreboard__name">
            <strong>{championName(participant)}</strong>
            <small>
              {role ?? "Função não informada"}
              {participant.participantId === localId ? <span className="scoreboard__you">Você</span> : null}
            </small>
          </span>
        </Link>
        <Loadout participant={participant} assets={assets} />
        {/* CC-1210: Riot ID como estava na partida, com link para o perfil (busca só no clique). */}
        {participant.gameName && participant.tagLine ? (
          <Link
            to={routeToPath({
              name: "player",
              gameName: participant.gameName,
              tagLine: participant.tagLine,
              // O Riot ID da partida pode ter mudado: o perfil é achado pela conta.
              ...(isRealMatchId(match.matchId) ? { fromMatch: { matchId: match.matchId, participantId: participant.participantId } } : {}),
            })}
            className="scoreboard__riot-id"
            aria-label={`Perfil de ${participant.gameName}#${participant.tagLine}`}
          >
            {participant.gameName}<span>#{participant.tagLine}</span>
          </Link>
        ) : null}
        </div>
      </th>
      <td className="scoreboard__kda">
        {scored ? <>{kills} / <span className="scoreboard__deaths">{deaths}</span> / {assists}</> : "—"}
        <small>{scored ? `${decimal((kills + assists) / Math.max(1, deaths), 2)} KDA` : ""}</small>
      </td>
      <td className="scoreboard__damage" data-label="Dano">
        {participant.damageToChampions != null ? <>
          {thousands(participant.damageToChampions)}
          <span className="scoreboard__bar" aria-hidden="true"><span style={{ width: `${maxDamage ? (participant.damageToChampions / maxDamage) * 100 : 0}%` }} /></span>
        </> : "—"}
      </td>
      <td className="scoreboard__gold" data-label="Ouro">{participant.goldEarned != null ? `${decimal(participant.goldEarned / 1000)} mil` : "—"}</td>
      <td className="scoreboard__cs" data-label="Farm">
        {cs ?? "—"}
        <small>{cs != null && minutes > 0 ? `${decimal(cs / minutes)}/min` : ""}</small>
      </td>
      <td className="scoreboard__vision">
        {participant.wardsPlaced ?? "—"}
        <small>{participant.visionScore != null ? `${participant.visionScore} de visão` : ""}</small>
      </td>
      <td className="scoreboard__kp">{scored && teamKills ? percent(Math.min(1, (kills + assists) / teamKills)) : "—"}</td>
      <td className="scoreboard__items"><Build items={items} participant={participant} /></td>
    </tr>
  );
}

export function Scoreboard({
  match,
  timeline,
  selected,
  localId,
  fromHistory,
  stayOnScoreboard = false,
}: {
  match: MatchDetail;
  timeline: MatchTimeline | null;
  selected: number | null;
  localId: number | null;
  fromHistory: boolean;
  stayOnScoreboard?: boolean;
}) {
  const teams = [...new Set(match.participants.map((p) => p.team))].sort((a, b) => a - b);
  const assets = useGameAssets(match.participants.find((p) => p.champion?.dataDragonVersion)?.champion?.dataDragonVersion);
  const maxDamage = Math.max(0, ...match.participants.map((p) => p.damageToChampions ?? 0));
  const [detailed, setDetailed] = useState(readDetailed);
  if (!teams.length) return <p className="muted">Esta partida não tem participantes registrados.</p>;
  return (
    <div className={detailed ? "scoreboard" : "scoreboard scoreboard--compact"}>
      <div className="scoreboard__toolbar">
        <button type="button" className="scoreboard__toggle" aria-pressed={detailed} onClick={() => { setDetailed(!detailed); saveDetailed(!detailed); }}>
          {detailed ? "Ocultar ouro, sentinelas e participação" : "Mostrar ouro, sentinelas e participação"}
        </button>
      </div>
      {teams.map((team) => {
        const totals = teamTotals(match.participants, team);
        const tone = team === BLUE ? "blue" : "red";
        return (
          <table key={team} className={`scoreboard__team scoreboard__team--${tone}`}>
            <caption>
              <span className="scoreboard__caption">
              <span className="scoreboard__team-name">
                <i className={`team-key team-key--${tone}`} aria-hidden="true" />
                {team === BLUE ? "Time azul" : "Time vermelho"}
              </span>
              {totals.win != null ? <span className={`scoreboard__result scoreboard__result--${totals.win ? "win" : "loss"}`}>{totals.win ? "Vitória" : "Derrota"}</span> : null}
              {totals.kills != null ? <span className="muted">{totals.kills} / {totals.deaths} / {totals.assists}</span> : null}
              </span>
            </caption>
            <thead>
              <tr>
                <th scope="col">Campeão</th>
                <th scope="col">K / D / A</th>
                <th scope="col" className="scoreboard__damage"><abbr title="Dano causado a campeões">Dano</abbr></th>
                <th scope="col" className="scoreboard__gold">Ouro</th>
                <th scope="col" className="scoreboard__cs">Farm</th>
                <th scope="col" className="scoreboard__vision"><abbr title="Sentinelas colocadas e placar de visão">Sentinelas</abbr></th>
                <th scope="col" className="scoreboard__kp"><abbr title="Participação em abates do time">Part. abates</abbr></th>
                <th scope="col">Itens finais</th>
              </tr>
            </thead>
            <tbody>
              {ordered(match.participants.filter((p) => p.team === team)).map((participant) => (
                <Row
                  key={participant.participantId}
                  match={match}
                  participant={participant}
                  teamKills={totals.kills}
                  timeline={timeline}
                  selected={participant.participantId === selected}
                  localId={localId}
                  fromHistory={fromHistory}
                  assets={assets}
                  maxDamage={maxDamage}
                  stayOnScoreboard={stayOnScoreboard}
                />
              ))}
            </tbody>
          </table>
        );
      })}
      {match.participants.some((p) => p.gameName) ? (
        <p className="scoreboard__note">Riot ID como estava na partida; o jogador pode ter trocado de nome depois.</p>
      ) : null}
    </div>
  );
}
