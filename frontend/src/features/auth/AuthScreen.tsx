import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api } from "../../shared/api/client";
import { messageForCode } from "../../shared/api/messages";
import { isApiError } from "../../shared/api/errors";
import { useAuth } from "./authContext";
import "./auth.css";

type Mode = "login" | "register";

/** Entrar ou criar conta local (Sprint 2, #10/#14). Mostrada quando o servidor exige login. */
export function AuthScreen() {
  const { signedIn } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const userId = useId();
  const passwordId = useId();
  const errorId = useId();
  const hintId = useId();

  useEffect(() => {
    document.title = (mode === "login" ? "Entrar" : "Criar conta") + " — RiftLab";
  }, [mode]);

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    headingRef.current?.focus();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError("Preencha usuário e senha.");
      return;
    }
    setSending(true);
    setError(null);
    try {
      const session = mode === "login"
        ? await api.login(username.trim(), password)
        : await api.register(username.trim(), password);
      if (session.user) signedIn(session.user);
    } catch (caught) {
      setError(isApiError(caught) ? messageForCode(caught.code) : "Não foi possível entrar agora. Tente de novo.");
    } finally {
      setSending(false);
    }
  }

  const title = mode === "login" ? "Entrar no RiftLab" : "Criar conta no RiftLab";
  return (
    <main id="conteudo" className="auth-screen">
      <section className="auth-card surface surface--pad" aria-labelledby="auth-heading">
        <p className="auth-card__brand"><span className="app__mark" aria-hidden="true">R</span>RiftLab</p>
        <h2 id="auth-heading" ref={headingRef} tabIndex={-1}>{title}</h2>
        <p className="muted">
          {mode === "login"
            ? "Entre com sua conta local para revisar suas partidas e decisões de compra."
            : "A conta fica nesta instalação do RiftLab. Depois, vincule sua conta Riot pelo ícone de perfil."}
        </p>
        <form className="form" onSubmit={submit} noValidate>
          <label htmlFor={userId} className="form__label">Usuário</label>
          <input id={userId} className="form__input" name="username" autoComplete="username"
            autoCapitalize="none" spellCheck={false} value={username}
            onChange={(event) => setUsername(event.target.value)}
            aria-invalid={error !== null} aria-describedby={error ? errorId : undefined} />
          <label htmlFor={passwordId} className="form__label">Senha</label>
          <input id={passwordId} className="form__input" name="password" type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"} value={password}
            onChange={(event) => setPassword(event.target.value)} aria-invalid={error !== null}
            aria-describedby={[mode === "register" ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined} />
          {mode === "register" ? <p id={hintId} className="auth-card__hint">Usuário com 3 a 32 caracteres; senha com pelo menos 10.</p> : null}
          {error ? <p id={errorId} className="form__error" role="alert">{error}</p> : null}
          <button type="submit" className="button" disabled={sending}>
            {sending ? "Enviando…" : mode === "login" ? "Entrar" : "Criar conta"}
          </button>
        </form>
        <p className="auth-card__switch">
          {mode === "login" ? "Ainda não tem conta?" : "Já tem conta?"}{" "}
          <button type="button" className="text-link" onClick={() => switchMode(mode === "login" ? "register" : "login")}>
            {mode === "login" ? "Criar conta" : "Entrar"}
          </button>
        </p>
      </section>
    </main>
  );
}
