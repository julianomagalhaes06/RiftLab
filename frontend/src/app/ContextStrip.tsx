import { useContext, useEffect, useState } from "react";
import { api, type CatalogCurrent } from "../shared/api/client";
import { DemoNoticeContext } from "./demoNotice";

function InfoIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" />
    </svg>
  );
}

/** Faixa de contexto abaixo do topo (CC-1202): aviso de demonstração quando a tela mostra DEMO
 * (CC-903), senão o escopo do produto; à direita o patch e o Data Dragon do catálogo. */
export function ContextStrip() {
  const { visible } = useContext(DemoNoticeContext);
  const [catalog, setCatalog] = useState<CatalogCurrent | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.getCatalogCurrent(controller.signal).then(setCatalog).catch(() => undefined);
    return () => controller.abort();
  }, []);
  return (
    <div className="context-strip">
      <div className="context-strip__inner">
        {visible ? (
          <p className="context-strip__note" role="note">
            <InfoIcon />
            <span><strong>Modo demonstração</strong> · Partidas DEMO são sintéticas e identificadas: não vêm da Riot nem representam jogadores reais.</span>
          </p>
        ) : (
          <p className="context-strip__note">
            <InfoIcon />
            <span>Servidor BR1 · revisão de partidas concluídas, sem acompanhamento ao vivo.</span>
          </p>
        )}
        {catalog?.gamePatch ? (
          <p className="context-strip__patch">Patch {catalog.gamePatch} · Data Dragon {catalog.dataDragonVersion}</p>
        ) : null}
      </div>
    </div>
  );
}
