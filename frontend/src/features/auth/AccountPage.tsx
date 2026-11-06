import { useEffect, useId, useState, type FormEvent } from "react";
import { api, type RiotLinkState } from "../../shared/api/client";
import { isApiError } from "../../shared/api/errors";
import { messageForCode } from "../../shared/api/messages";
import { parseRiotId } from "../../shared/router/routes";
import { useAuth } from "./authContext";
import "./auth.css";

const iconUrl = (version: string | null, icon: number) =>
  version ? `https://ddragon.leagueoflegends.com/cdn/${version}/img/profileicon/${icon}.png` : null;

const clock = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "";

/** Conta local e vínculo da conta Riot pelo desafio do ícone (Sprint 2, #12). */
export function AccountPage() {
  const { user } = useAuth();
  const [link, setLink] = useState<RiotLinkState | null>(null);
  const [failed, setFailed] = useState(false);
  const [version, setVersion] = useState<string | null>(null);
  const [riotId, setRiotId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputId = useId();
  const errorId = useId();

  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    api.getRiotLink(controller.signal).then(setLink).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    api.getCatalogCurrent(controller.signal).then((catalog) => setVersion(catalog.dataDragonVersion ?? null)).catch(() => undefined);
    return () => controller.abort();
  }, [user]);

  async function run(action: () => Promise<RiotLinkState | void>) {
    setBusy(true);
    setError(null);
    try {
      const next = await action();
      setLink(next ?? { state: "NONE" });
    } catch (caught) {
      setError(isApiError(caught) ? messageForCode(caught.code) : "Não foi possível concluir agora. Tente de novo.");
    } finally {
      setBusy(false);
    }
  }

  function start(event: FormEvent) {
    event.preventDefault();
    const parsed = parseRiotId(riotId);
    if (!parsed) {
      setError("Informe o Riot ID no formato Nome#TAG.");
      return;
    }
    void run(() => api.startRiotLink(parsed.gameName, parsed.tagLine));
  }

  if (!user) {
    return (
      <section className="area-page account-page" aria-labelledby="account-heading">
        <h2 id="account-heading">Minha conta</h2>
        <p className="muted">Este ambiente não usa contas locais. A conta Riot vem da configuração da instalação.</p>
      </section>
    );
  }

  const icon = link?.state === "PENDING" && link.iconId != null ? link.iconId : null;
  const iconSrc = icon != null ? iconUrl(version, icon) : null;
  return (
    <section className="area-page account-page" aria-labelledby="account-heading">
      <header className="page-head">
        <p className="page-head__context">Conta local · {user.username}</p>
        <h2 id="account-heading">Minha conta</h2>
        <p className="page-head__lead">Vincule sua conta Riot para que Minha posição, Painel e Histórico usem as suas partidas.</p>
      </header>
      <div className="account-card surface surface--pad" aria-live="polite">
        {failed ? <p role="alert">Não foi possível consultar o vínculo agora.</p>
          : !link ? <p role="status">Carregando o vínculo…</p>
          : link.state === "LINKED" ? <>
            <h3>Conta Riot vinculada</h3>
            <p className="account-card__riot">{link.gameName}#{link.tagLine}</p>
            <p className="muted">Vinculada em {new Date(link.linkedAt ?? "").toLocaleString("pt-BR")}.</p>
            <button type="button" className="button button--secondary" disabled={busy} onClick={() => void run(async () => { await api.unlinkRiot(); })}>
              {busy ? "Desvinculando…" : "Desvincular conta"}
            </button>
          </> : link.state === "PENDING" && icon != null ? <>
            <h3>Comprove que a conta é sua</h3>
            <ol className="account-steps">
              <li>No cliente do League of Legends, troque o ícone de perfil de <strong>{link.gameName}#{link.tagLine}</strong> para o ícone abaixo.</li>
              <li>Volte aqui e selecione <strong>Verificar ícone</strong> até as {clock(link.expiresAt)}.</li>
            </ol>
            <figure className="account-icon">
              {iconSrc ? <img src={iconSrc} alt={`Ícone de perfil número ${icon}`} width={96} height={96} referrerPolicy="no-referrer" /> : null}
              <figcaption>Ícone inicial nº {icon}</figcaption>
            </figure>
            <div className="account-actions">
              <button type="button" className="button" disabled={busy} onClick={() => void run(() => api.verifyRiotLink())}>
                {busy ? "Verificando…" : "Verificar ícone"}
              </button>
              <button type="button" className="button button--secondary" disabled={busy}
                onClick={() => void run(() => api.startRiotLink(link.gameName ?? "", link.tagLine ?? ""))}>
                Pedir outro ícone
              </button>
            </div>
          </> : <>
            <h3>Vincular conta Riot</h3>
            <p className="muted">Sem o login da Riot, a posse da conta é comprovada pela troca temporária do ícone de perfil. Depois de vincular, você pode voltar ao ícone que preferir.</p>
            <form className="form" onSubmit={start} noValidate>
              <label htmlFor={inputId} className="form__label">Riot ID (Nome#TAG)</label>
              <input id={inputId} className="form__input" value={riotId} placeholder="Nome#BR1" autoComplete="off" spellCheck={false}
                onChange={(event) => setRiotId(event.target.value)} aria-invalid={error !== null} aria-describedby={error ? errorId : undefined} />
              <button type="submit" className="button" disabled={busy}>{busy ? "Consultando…" : "Pedir ícone"}</button>
            </form>
          </>}
        {error ? <p id={errorId} className="form__error" role="alert">{error}</p> : null}
      </div>
    </section>
  );
}
