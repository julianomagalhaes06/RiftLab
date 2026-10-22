import { useEffect, useState } from "react";
import { api, type PlayerProfile } from "../../shared/api/client";
import { Link } from "../../shared/router/Link";
import { routeToPath } from "../../shared/router/routes";
import { betterRank, emblemSrc, isTier, profileIconUrl, rankTitle } from "../ranked/tiers";

/** Chip do perfil da conta configurada no topo (CC-1202): selo BR1, ícone de invocador, Riot ID e
 * elo na cor do elo; leva à página do jogador. Sem conta, sem chave ou com erro, fica o selo. */
export function ProfileChip() {
  const [profile, setProfile] = useState<PlayerProfile | null>(null);
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api.getOwnPlayer(controller.signal).then(setProfile).catch(() => undefined);
    return () => controller.abort();
  }, []);
  const rank = profile ? betterRank(profile.solo, profile.flex) : null;
  const tier = rank?.state === "READY" && isTier(rank.tier) ? rank.tier : null;
  const rankText = tier ? `${rankTitle(tier, rank?.division)} · ${rank?.leaguePoints ?? 0} PDL` : rank ? "Sem classificação" : "Elo não observado";
  const icon = profile ? profileIconUrl(profile) : null;
  return (
    <div className="profile-chip">
      <span className="chip profile-chip__server"><span className="sr-only">Servidor </span>BR1</span>
      {profile ? (
        <Link
          to={routeToPath({ name: "player", gameName: profile.gameName, tagLine: profile.tagLine })}
          className="profile-chip__link"
          aria-label={`Seu perfil: ${profile.gameName}#${profile.tagLine}, ${rankText}`}
        >
          <span className="portrait profile-chip__icon">
            {icon && !broken ? <img src={icon} alt="" width="32" height="32" onError={() => setBroken(true)} /> : null}
          </span>
          <span className="profile-chip__text">
            <span className="profile-chip__name">{profile.gameName}</span>
            <span className="profile-chip__rank" data-tier={tier ?? "NONE"}>
              {tier ? <img src={emblemSrc(tier)} alt="" width="16" height="16" /> : null}
              {rankText}
            </span>
          </span>
        </Link>
      ) : null}
    </div>
  );
}
