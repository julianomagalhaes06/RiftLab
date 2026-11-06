import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, type SessionUser } from "../../shared/api/client";
import { messageForCode } from "../../shared/api/messages";
import { isApiError } from "../../shared/api/errors";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";
import { loginPath } from "../../shared/router/routes";
import { emblemSrc, type Tier } from "../ranked/tiers";
import { useAuth } from "./authContext";
import "./auth.css";

export type AuthMode = "login" | "register";
type Mode = AuthMode;

const USERNAME_PATTERN = /^[A-Za-z0-9_.-]+$/;
// Escada decorativa: do Ferro ao Desafiante (só emblemas oficiais locais, sem dado de conta).
const LADDER: readonly Tier[] = ["IRON", "SILVER", "GOLD", "DIAMOND", "CHALLENGER"];

const COPY: Record<Mode, { tab: string; title: string; lead: string; heroTitle: string; heroLead: string; submit: string }> = {
  login: {
    tab: "Entrar",
    title: "Entrar no RiftLab",
    lead: "Use sua conta local para continuar de onde parou.",
    heroTitle: "Entenda suas compras. Decida melhor na próxima.",
    heroLead: "Revise cada compra de item com o contexto daquele momento da partida.",
    submit: "Entrar",
  },
  register: {
    tab: "Criar conta",
    title: "Criar conta no RiftLab",
    lead: "A conta fica nesta instalação. Depois, vincule sua conta Riot pelo ícone de perfil.",
    heroTitle: "Crie sua conta e comece pela sua última partida.",
    heroLead: "Seus estudos, reflexões e sua posição ficam separados das outras contas.",
    submit: "Criar conta",
  },
};

const POINTS = [
  ["Reveja o momento da compra", "Inventário, ouro aproximado e adversários antes de cada item."],
  ["Compare alternativas legais", "Até três itens possíveis naquele instante, com o custo que faltava."],
  ["Acompanhe sua evolução", "Elo da sua conta, estudos e reflexões no mesmo lugar."],
] as const;

function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
      <path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function EyeIcon({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
      {open ? null : <path d="M4 20L20 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />}
    </svg>
  );
}

/**
 * Entrar ou criar conta (/entrar, /criar-conta). A conta não é exigida para navegar: perfis e
 * partidas são públicos, e a pessoa chega aqui quando quer analisar ou guardar algo. Por isso
 * a tela volta para `next` (onde a pessoa estava) e oferece seguir sem conta. Padrão Arena:
 * página dividida (vitrine à esquerda, formulário à direita; empilhada no celular), abas
 * Entrar/Criar conta, senha com mostrar/ocultar, requisitos do cadastro conferidos enquanto a
 * pessoa digita e uma confirmação depois do cadastro com o próximo passo.
 */
export function AuthScreen({ mode, next }: { mode: Mode; next: string | null }) {
  const { signedIn } = useAuth();
  const { navigate } = useRouter();
  const destination = next ?? "/painel";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [created, setCreated] = useState<SessionUser | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const welcomeRef = useRef<HTMLHeadingElement>(null);
  const tabsId = useId();
  const userId = useId();
  const passwordId = useId();
  const errorId = useId();
  const rulesId = useId();
  const userHintId = useId();
  const copy = COPY[mode];

  useEffect(() => {
    document.title = (created ? "Conta criada" : copy.tab) + " — RiftLab";
  }, [copy.tab, created]);

  useEffect(() => {
    if (created) welcomeRef.current?.focus();
  }, [created]);

  function switchMode(value: Mode, focusTab = false) {
    setError(null);
    navigate(loginPath(next, value), { replace: true });
    if (focusTab) document.getElementById(`${tabsId}-${value}`)?.focus();
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      switchMode(mode === "login" ? "register" : "login", true);
    }
  }

  async function run(action: () => Promise<{ user?: SessionUser | null }>, onUser: (user: SessionUser) => void) {
    setSending(true);
    setError(null);
    try {
      const session = await action();
      if (session.user) onUser(session.user);
    } catch (caught) {
      setError(isApiError(caught) ? messageForCode(caught.code) : "Não foi possível entrar agora. Tente de novo.");
    } finally {
      setSending(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError("Preencha usuário e senha.");
      return;
    }
    if (mode === "login") {
      void run(() => api.login(username.trim(), password), (user) => {
        signedIn(user);
        navigate(destination, { replace: true });
      });
    } else {
      void run(() => api.register(username.trim(), password), setCreated);
    }
  }

  const name = username.trim();
  const rules = [
    { ok: name.length >= 3 && name.length <= 32, text: "Usuário com 3 a 32 caracteres" },
    { ok: name.length > 0 && USERNAME_PATTERN.test(name), text: "Só letras, números, ponto, hífen ou sublinhado" },
    { ok: password.length >= 10 && password.length <= 128, text: "Senha com pelo menos 10 caracteres" },
  ];

  return (
    <div className="auth-page">
      <header className="auth-top">
        <Link to="/" className="auth-top__brand"><span className="app__mark" aria-hidden="true">R</span>RiftLab</Link>
        <Link to={next ?? "/"} className="auth-top__back">Continuar sem conta</Link>
      </header>

      <main id="conteudo" className="auth-main">
        <section className="auth-hero" aria-label="Sobre o RiftLab">
          <p className="auth-hero__context">Revisão pós-partida · League of Legends</p>
          <p className="auth-hero__title">{copy.heroTitle}</p>
          <p className="auth-hero__lead">{copy.heroLead}</p>
          <ul className="auth-hero__points">
            {POINTS.map(([title, text]) => (
              <li key={title}>
                <span className="auth-hero__check"><CheckIcon /></span>
                <span><strong>{title}</strong><small>{text}</small></span>
              </li>
            ))}
          </ul>
        </section>

        <section className="auth-card surface surface--pad brackets" aria-labelledby={created ? `${tabsId}-welcome` : `${tabsId}-heading`}>
          {created ? (
            <div className="auth-welcome">
              <span className="auth-welcome__badge"><CheckIcon /></span>
              <h1 id={`${tabsId}-welcome`} ref={welcomeRef} tabIndex={-1}>Conta criada</h1>
              <p className="muted">Boas-vindas, <strong>{created.username}</strong>. Já dá para analisar compras e guardar seus estudos. Para ver seu Painel e sua posição, vincule sua conta Riot.</p>
              <button type="button" className="button auth-wide" onClick={() => { signedIn(created); navigate(next ?? "/conta", { replace: true }); }}>
                {next ? "Voltar para onde eu estava" : "Vincular conta Riot"}
              </button>
              <button type="button" className="button button--secondary auth-wide" onClick={() => { signedIn(created); navigate(next ? "/conta" : "/painel", { replace: true }); }}>
                {next ? "Vincular conta Riot" : "Ir para o Painel"}
              </button>
            </div>
          ) : (
            <>
              <div className="segmented auth-tabs" role="tablist" aria-label="Acesso ao RiftLab">
                {(["login", "register"] as const).map((value) => (
                  <button
                    key={value}
                    id={`${tabsId}-${value}`}
                    type="button"
                    role="tab"
                    aria-selected={mode === value}
                    aria-controls={`${tabsId}-panel`}
                    tabIndex={mode === value ? 0 : -1}
                    onClick={() => switchMode(value)}
                    onKeyDown={onTabKey}
                  >
                    {COPY[value].tab}
                  </button>
                ))}
              </div>
              <div id={`${tabsId}-panel`} role="tabpanel" aria-labelledby={`${tabsId}-${mode}`} className="auth-panel">
                <h1 id={`${tabsId}-heading`} ref={headingRef} tabIndex={-1}>{copy.title}</h1>
                <p className="muted">{copy.lead}</p>
                <form className="form auth-form" onSubmit={submit} noValidate>
                  <label htmlFor={userId} className="form__label">Usuário</label>
                  <input id={userId} className="form__input" name="username" autoComplete="username"
                    autoCapitalize="none" spellCheck={false} value={username}
                    onChange={(event) => setUsername(event.target.value)} aria-invalid={error !== null}
                    aria-describedby={[mode === "register" ? userHintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined} />
                  {mode === "register" ? <p id={userHintId} className="auth-hint">É com este nome que você entra. Ele não aparece para outras pessoas.</p> : null}
                  <label htmlFor={passwordId} className="form__label">Senha</label>
                  <div className="auth-password">
                    <input id={passwordId} className="form__input" name="password" type={showPassword ? "text" : "password"}
                      autoComplete={mode === "login" ? "current-password" : "new-password"} value={password}
                      onChange={(event) => setPassword(event.target.value)} aria-invalid={error !== null}
                      aria-describedby={[mode === "register" ? rulesId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined} />
                    <button type="button" className="auth-password__toggle" aria-controls={passwordId} aria-pressed={showPassword}
                      aria-label={showPassword ? "Ocultar senha" : "Mostrar senha"} onClick={() => setShowPassword(!showPassword)}>
                      <EyeIcon open={!showPassword} />
                    </button>
                  </div>
                  {mode === "register" ? (
                    <ul id={rulesId} className="auth-rules" aria-label="Requisitos da conta">
                      {rules.map((rule) => (
                        <li key={rule.text} className={rule.ok ? "is-met" : undefined}>
                          <CheckIcon />
                          <span>{rule.text}<span className="sr-only">{rule.ok ? " (atendido)" : " (pendente)"}</span></span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {error ? <p id={errorId} className="form__error" role="alert">{error}</p> : null}
                  <button type="submit" className="button auth-wide" disabled={sending}>
                    {sending ? "Enviando…" : copy.submit}
                  </button>
                </form>
                <p className="auth-hint auth-hint--center">
                  Sem conta, perfis, partidas e partida ao vivo continuam abertos.{" "}
                  <Link to={next ?? "/"}>Continuar sem conta</Link>
                </p>
              </div>
            </>
          )}
        </section>

        <figure className="auth-ladder surface surface--rank brackets">
          <div className="auth-ladder__row" aria-hidden="true">
            {LADDER.map((tier) => (
              <span key={tier} className="auth-ladder__slot" data-tier={tier}>
                <img src={emblemSrc(tier)} alt="" width="104" height="104" />
              </span>
            ))}
          </div>
          <figcaption>Do Ferro ao Desafiante, uma decisão de cada vez.</figcaption>
        </figure>
      </main>

      <footer className="auth-foot">
        <p>Retratos e ícones: Data Dragon · Emblemas: Ranked Emblems · © Riot Games</p>
        <p lang="en">RiftLab isn&apos;t endorsed by Riot Games and doesn&apos;t reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties.</p>
      </footer>
    </div>
  );
}
