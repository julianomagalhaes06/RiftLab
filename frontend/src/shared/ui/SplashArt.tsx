import { useState } from "react";

/** Splash art decorativa de um `.splash-banner`: entra com fade quando carrega e some se falhar
 * (fica o fundo escuro). Use `key={src}` para recomeçar ao trocar de arte. */
export function SplashArt({ src }: { src: string }) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  if (state === "failed") return null;
  return <img className="splash-banner__art" data-state={state} src={src} alt="" decoding="async"
    referrerPolicy="no-referrer" onLoad={() => setState("ready")} onError={() => setState("failed")} />;
}
