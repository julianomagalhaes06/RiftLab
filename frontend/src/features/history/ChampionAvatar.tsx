import { useState } from "react";
import type { MatchParticipant } from "../../shared/api/client";
import { championName, championPortraitUrl } from "../matches/format";

/** Retrato do Data Dragon com caixa de tamanho fixo; sem imagem, mostra as iniciais.
 * Decorativo por padrão: o nome acompanha em texto ao lado. */
export function ChampionAvatar({
  participant,
  size,
  className = "",
}: {
  participant: Pick<MatchParticipant, "championId" | "champion">;
  size: number;
  className?: string;
}) {
  const url = championPortraitUrl(participant.champion);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const showImage = url !== null && failedUrl !== url;
  const initials = championName(participant).replace(/^Campeão #/, "#").slice(0, 2);
  return (
    <span
      className={`champion-avatar ${className}`.trim()}
      style={{ width: size, height: size }}
      data-state={showImage ? "image" : "fallback"}
      data-testid="champion-portrait"
      aria-hidden="true"
    >
      {showImage ? (
        <img
          src={url}
          alt=""
          width={size}
          height={size}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailedUrl(url)}
        />
      ) : (
        <span className="champion-avatar__initials">{initials}</span>
      )}
    </span>
  );
}
