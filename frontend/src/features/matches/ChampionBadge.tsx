import { useState } from "react";
import type { MatchParticipant } from "../../shared/api/client";
import { championName, championPortraitUrl } from "./format";

/** Retrato quadrado oficial + nome do campeão (CC-208).
 *
 * A caixa do retrato tem tamanho fixo mesmo sem imagem: se o campeão não foi
 * resolvido (DEMO, patch sem mapping, catálogo ausente) ou se a imagem falhar ao
 * carregar, a caixa fica vazia e o texto continua no lugar — sem salto de layout.
 * O nome é o texto acessível; a imagem é decorativa (alt vazio) para o leitor de
 * tela não repetir o nome. */
export function ChampionBadge({
  participant,
  size = 32,
}: {
  participant: Pick<MatchParticipant, "championId" | "champion">;
  size?: number;
}) {
  const url = championPortraitUrl(participant.champion);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const showImage = url !== null && failedUrl !== url;
  return (
    <span className="champion">
      <span
        className="champion__portrait"
        style={{ width: size, height: size }}
        data-testid="champion-portrait"
        data-state={showImage ? "image" : "fallback"}
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
        ) : null}
      </span>
      <span className="champion__name">{championName(participant)}</span>
    </span>
  );
}
