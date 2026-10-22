import { useEffect, useState } from "react";
import { api, type PlayerLiveStatus } from "../../shared/api/client";
import { Link } from "../../shared/router/Link";
import { routeToPath } from "../../shared/router/routes";
import { elapsed, queueName } from "./liveAssets";

/** Botão "Partida ao vivo" com o aviso do op.gg (CC-1213): ao abrir o perfil, pergunta se o
 * jogador está em partida (2 requisições à Riot, 60 s em memória no servidor). Em partida, o
 * botão acende e o aviso mostra fila e tempo de jogo; fora dela ou em erro, fica neutro. */
export function LiveButton({ gameName, tagLine }: { gameName: string; tagLine: string }) {
  const [status, setStatus] = useState<PlayerLiveStatus | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const controller = new AbortController();
    api.getLiveStatus(gameName, tagLine, controller.signal).then(setStatus).catch(() => undefined);
    return () => controller.abort();
  }, [gameName, tagLine]);
  const playing = status?.state === "IN_GAME";
  useEffect(() => {
    if (!playing || !status?.startedAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [playing, status?.startedAt]);
  const clock = playing ? elapsed(status?.startedAt, now) : null;
  return (
    <div className={`live-button${playing ? " live-button--on" : ""}`}>
      <Link
        to={routeToPath({ name: "live", gameName, tagLine })}
        className={`button ${playing ? "" : "button--secondary "}live-button__link`}
        aria-describedby={playing ? "live-button-status" : undefined}
      >
        <span className="live-button__dot" aria-hidden="true" />
        {playing ? "Ao vivo agora" : "Partida ao vivo"}
      </Link>
      {playing && status ? (
        <p id="live-button-status" className="live-button__status" role="status">
          Este jogador está em partida neste momento · {queueName(status.queueId, status.gameMode)}
          {clock ? ` · ${clock} de jogo` : " · carregando"}
        </p>
      ) : null}
    </div>
  );
}
