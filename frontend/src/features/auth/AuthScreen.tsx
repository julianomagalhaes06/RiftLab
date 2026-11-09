import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, type SessionUser, type SocialProvider } from "../../shared/api/client";
import { messageForCode } from "../../shared/api/messages";
import { isApiError } from "../../shared/api/errors";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";
import { loginPath } from "../../shared/router/routes";
import { ThemeToggle } from "../../shared/theme/ThemeToggle";
import { emblemSrc, type Tier } from "../ranked/tiers";
import { useAuth } from "./authContext";
import "./auth.css";

export type AuthMode = "login" | "register" | "forgot";
type Mode = AuthMode;
type TabMode = Exclude<Mode, "forgot">;
/** Passo: formulário principal; código de confirmação; código + senha nova da recuperação. */
type Step = "form" | "verify" | "reset";

// Escada decorativa: do Ferro ao Desafiante (só emblemas oficiais locais, sem dado de conta).
const LADDER: readonly Tier[] = ["IRON", "SILVER", "GOLD", "DIAMOND", "CHALLENGER"];
const RESEND_SECONDS = 60;

const COPY: Record<Mode, { tab: string; title: string; lead: string; heroTitle: string; heroLead: string; submit: string }> = {
  login: {
    tab: "Entrar",
    title: "Entrar no RiftLab",
    lead: "Use seu e-mail para continuar de onde parou.",
    heroTitle: "Entenda suas compras. Decida melhor na próxima.",
    heroLead: "Revise cada compra de item com o contexto daquele momento da partida.",
    submit: "Entrar",
  },
  register: {
    tab: "Criar conta",
    title: "Criar conta no RiftLab",
    lead: "Enviamos um código para confirmar seu e-mail. Depois, vincule sua conta Riot pelo ícone de perfil.",
    heroTitle: "Crie sua conta e comece pela sua última partida.",
    heroLead: "Seus estudos, reflexões e sua posição ficam separados das outras contas.",
    submit: "Criar conta",
  },
  forgot: {
    tab: "Redefinir senha",
    title: "Redefinir a senha",
    lead: "Informe o e-mail da conta. Enviamos um código para você criar uma senha nova.",
    heroTitle: "Volte para as suas partidas.",
    heroLead: "Um código no seu e-mail basta para criar uma senha nova.",
    submit: "Enviar código",
  },
};

const POINTS = [
  ["Reveja o momento da compra", "Inventário, ouro aproximado e adversários antes de cada item."],
  ["Compare alternativas legais", "Até três itens possíveis naquele instante, com o custo que faltava."],
  ["Acompanhe sua evolução", "Elo da sua conta, estudos e reflexões no mesmo lugar."],
] as const;

const SOCIAL_LABELS: Record<SocialProvider, string> = { google: "Google", discord: "Discord" };
/** Motivos com que a API devolve a pessoa a esta tela depois de um login social. */
const SOCIAL_NOTICES: Readonly<Record<string, string>> = {
  cancelado: "Login cancelado. Escolha outra forma de entrar.",
  falhou: "Não foi possível entrar por esse serviço agora. Tente de novo.",
  email: "Esse serviço não confirmou o seu e-mail. Confirme o e-mail lá ou use o formulário abaixo.",
  indisponivel: "Esse login não está disponível neste servidor.",
};

function SocialLogin({ providers, next }: { providers: readonly SocialProvider[]; next: string | null }) {
  if (providers.length === 0) return null;
  const query = next ? `?next=${encodeURIComponent(next)}` : "";
  return (
    <div className="auth-social">
      <div className="auth-social__buttons">
        {providers.map((provider) => (
          // Link comum: a API redireciona para o provedor, e a página inteira precisa ir junto.
          <a key={provider} className="button button--secondary auth-social__button"
            href={`/api/v1/auth/social/${provider}/start${query}`}>
            <img src={`/brand/${provider}.svg`} alt="" width="20" height="20" />
            Continuar com {SOCIAL_LABELS[provider]}
          </a>
        ))}
      </div>
      <p className="auth-social__or">ou com e-mail</p>
    </div>
  );
}

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

function passwordRules(password: string) {
  return [
    { ok: password.length >= 8 && password.length <= 128, text: "Pelo menos 8 caracteres" },
    { ok: /\p{Lu}/u.test(password), text: "Uma letra maiúscula" },
    { ok: /[^\p{L}\p{N}\s]/u.test(password), text: "Um símbolo, como ! @ # ou -" },
  ];
}

function PasswordRules({ id, password }: { id: string; password: string }) {
  return (
    <ul id={id} className="auth-rules" aria-label="Requisitos da senha">
      {passwordRules(password).map((rule) => (
        <li key={rule.text} className={rule.ok ? "is-met" : undefined}>
          <CheckIcon />
          <span>{rule.text}<span className="sr-only">{rule.ok ? " (atendido)" : " (pendente)"}</span></span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Entrar, criar conta e redefinir a senha (/entrar, /criar-conta, /esqueci-senha). A conta não é
 * exigida para navegar: a pessoa chega aqui quando quer analisar ou guardar algo, por isso a tela
 * volta para `next` e oferece seguir sem conta. O cadastro só abre a sessão depois do código
 * enviado por e-mail; a senha digitada fica em memória até lá, porque confirmar também a exige.
 * Padrão Arena: página dividida (vitrine à esquerda, formulário à direita; empilhada no celular).
 */
export function AuthScreen({ mode, next, welcome = null }: { mode: Mode; next: string | null; welcome?: SessionUser | null }) {
  const { signedIn, socialProviders } = useAuth();
  const { navigate } = useRouter();
  const destination = next ?? "/painel";
  const [step, setStep] = useState<Step>("form");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [code, setCode] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(
    () => SOCIAL_NOTICES[new URLSearchParams(window.location.search).get("social") ?? ""] ?? null,
  );
  const [sending, setSending] = useState(false);
  const [created, setCreated] = useState<SessionUser | null>(welcome);
  const [fromRegister, setFromRegister] = useState(false);
  const [resendIn, setResendIn] = useState(0);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const welcomeRef = useRef<HTMLHeadingElement>(null);
  const firstStep = useRef(true);
  const tabsId = useId();
  const nameId = useId();
  const emailId = useId();
  const passwordId = useId();
  const confirmationId = useId();
  const codeId = useId();
  const errorId = useId();
  const rulesId = useId();
  const copy = COPY[mode];
  const address = email.trim();

  useEffect(() => {
    document.title = (created ? "Conta criada" : step === "verify" ? "Confirmar e-mail" : copy.tab) + " — RiftLab";
  }, [copy.tab, created, step]);

  useEffect(() => {
    if (created) welcomeRef.current?.focus();
  }, [created]);

  // O aviso da volta do login social aparece uma vez: recarregar a página não o repete.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("social")) return;
    url.searchParams.delete("social");
    window.history.replaceState(window.history.state, "", url);
  }, []);

  // Troca de passo: o foco acompanha o título novo (teclado e leitor de tela).
  useEffect(() => {
    if (firstStep.current) {
      firstStep.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  // Contagem do intervalo de reenvio do código.
  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = window.setTimeout(() => setResendIn((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [resendIn]);

  function clearMessages() {
    setError(null);
    setNotice(null);
  }

  function switchMode(value: TabMode, focusTab = false) {
    clearMessages();
    setStep("form");
    navigate(loginPath(next, value), { replace: true });
    if (focusTab) document.getElementById(`${tabsId}-${value}`)?.focus();
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      switchMode(mode === "login" ? "register" : "login", true);
    }
  }

  /** Roda o pedido; devolve o código de erro da API (ou null) depois de mostrar a mensagem. */
  async function run(action: () => Promise<void>, silence: readonly string[] = []): Promise<string | null> {
    setSending(true);
    clearMessages();
    try {
      await action();
      return null;
    } catch (caught) {
      const failure = isApiError(caught) ? caught.code : null;
      if (!failure || !silence.includes(failure)) {
        setError(failure ? messageForCode(failure) : "Não foi possível concluir agora. Tente de novo.");
      }
      return failure;
    } finally {
      setSending(false);
    }
  }

  function goToCode(target: Step) {
    setCode("");
    setResendIn(RESEND_SECONDS);
    setStep(target);
  }

  async function submitForm() {
    if (mode === "forgot") {
      if (!address) return setError("Informe seu e-mail.");
      await run(async () => {
        await api.forgotPassword(address);
        setPassword("");
        goToCode("reset");
      });
      return;
    }
    if (mode === "register" && !name.trim()) return setError("Informe seu nome.");
    if (!address || !password) return setError("Preencha e-mail e senha.");
    if (mode === "register") {
      if (password !== confirmation) return setError("As senhas não são iguais.");
      await run(async () => {
        await api.register(name.trim(), address, password);
        setFromRegister(true);
        goToCode("verify");
      });
      return;
    }
    const failure = await run(async () => {
      const session = await api.login(address, password);
      if (session.user) {
        signedIn(session.user);
        navigate(destination, { replace: true });
      }
    }, ["EMAIL_NOT_VERIFIED"]);
    if (failure === "EMAIL_NOT_VERIFIED") {
      // Conta criada e não confirmada: manda outro código (o servidor respeita o intervalo).
      setFromRegister(false);
      goToCode("verify");
      setNotice("Confirme seu e-mail para entrar.");
      await api.resendCode(address).catch(() => undefined);
    }
  }

  async function submitVerify() {
    if (code.length !== 6) return setError("Digite os 6 dígitos do código.");
    await run(async () => {
      const session = await api.verifyEmail(address, code, password);
      if (!session.user) return;
      if (fromRegister) {
        setCreated(session.user);
      } else {
        signedIn(session.user);
        navigate(destination, { replace: true });
      }
    });
  }

  async function submitReset() {
    if (code.length !== 6) return setError("Digite os 6 dígitos do código.");
    if (!password) return setError("Crie a senha nova.");
    const failure = await run(() => api.resetPassword(address, code, password));
    if (failure === null) {
      setPassword("");
      setCode("");
      setStep("form");
      navigate(loginPath(next), { replace: true });
      setNotice("Senha alterada. Entre com a senha nova.");
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    clearMessages();
    void (step === "verify" ? submitVerify() : step === "reset" ? submitReset() : submitForm());
  }

  function resend() {
    void run(async () => {
      await (step === "verify" ? api.resendCode(address) : api.forgotPassword(address));
      setResendIn(RESEND_SECONDS);
      setNotice("Se puder ser enviado, um código novo chega em instantes. Confira também o spam.");
    });
  }

  function back() {
    clearMessages();
    setCode("");
    setStep("form");
  }

  const passwordField = (label: string, autoComplete: string, describedBy: string | undefined) => (
    <>
      <label htmlFor={passwordId} className="form__label">{label}</label>
      <div className="auth-password">
        <input id={passwordId} className="form__input" name="password" type={showPassword ? "text" : "password"}
          autoComplete={autoComplete} value={password}
          onChange={(event) => setPassword(event.target.value)} aria-invalid={error !== null}
          aria-describedby={describedBy} />
        <button type="button" className="auth-password__toggle" aria-controls={passwordId} aria-pressed={showPassword}
          aria-label={showPassword ? "Ocultar senha" : "Mostrar senha"} onClick={() => setShowPassword(!showPassword)}>
          <EyeIcon open={!showPassword} />
        </button>
      </div>
    </>
  );
  const errorLine = error ? <p id={errorId} className="form__error" role="alert">{error}</p> : null;
  const noticeLine = notice ? <p className="auth-notice" role="status">{notice}</p> : null;
  const codeField = (
    <>
      <label htmlFor={codeId} className="form__label">Código de 6 dígitos</label>
      <input id={codeId} className="form__input auth-code" name="code" inputMode="numeric" autoComplete="one-time-code"
        maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
        aria-invalid={error !== null} aria-describedby={error ? errorId : undefined} />
    </>
  );
  const codeActions = (
    <div className="auth-code-actions">
      <button type="button" className="auth-link" onClick={resend} disabled={sending || resendIn > 0}>
        {resendIn > 0 ? `Reenviar código em ${resendIn} s` : "Reenviar código"}
      </button>
      <button type="button" className="auth-link" onClick={back} disabled={sending}>Corrigir o e-mail</button>
    </div>
  );

  return (
    <div className="auth-page">
      <header className="auth-top">
        <Link to="/" className="auth-top__brand"><span className="app__mark" aria-hidden="true">R</span>RiftLab</Link>
        <div className="auth-top__tools">
          <ThemeToggle />
          <Link to={next ?? "/"} className="auth-top__back">Continuar sem conta</Link>
        </div>
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
              <p className="muted">E-mail <strong>{created.email}</strong> confirmado. Já dá para analisar compras e guardar seus estudos. Para ver seu Painel e sua posição, vincule sua conta Riot.</p>
              <button type="button" className="button auth-wide" onClick={() => { signedIn(created); navigate(next ?? "/conta", { replace: true }); }}>
                {next ? "Voltar para onde eu estava" : "Vincular conta Riot"}
              </button>
              <button type="button" className="button button--secondary auth-wide" onClick={() => { signedIn(created); navigate(next ? "/conta" : "/", { replace: true }); }}>
                {next ? "Vincular conta Riot" : "Agora não"}
              </button>
            </div>
          ) : step !== "form" ? (
            <div className="auth-panel">
              <h1 id={`${tabsId}-heading`} ref={headingRef} tabIndex={-1}>{step === "verify" ? "Confirme seu e-mail" : "Crie uma senha nova"}</h1>
              <p className="muted">
                {step === "verify" ? "Enviamos um código de 6 dígitos para " : "Se houver uma conta, enviamos um código de 6 dígitos para "}
                <strong>{address}</strong>. Ele vale por 15 minutos.
              </p>
              {noticeLine}
              <form className="form auth-form" onSubmit={submit} noValidate>
                {codeField}
                {step === "reset" ? (
                  <>
                    {passwordField("Senha nova", "new-password", rulesId)}
                    <PasswordRules id={rulesId} password={password} />
                  </>
                ) : null}
                {errorLine}
                <button type="submit" className="button auth-wide" disabled={sending}>
                  {sending ? "Enviando…" : step === "verify" ? "Confirmar e-mail" : "Salvar senha nova"}
                </button>
              </form>
              {codeActions}
            </div>
          ) : (
            <>
              {/* Vale para entrar e para criar conta: fica acima das abas, uma vez só. */}
              {mode !== "forgot" ? <SocialLogin providers={socialProviders} next={next} /> : null}
              {mode !== "forgot" ? (
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
              ) : null}
              <div id={`${tabsId}-panel`} role={mode !== "forgot" ? "tabpanel" : undefined} aria-labelledby={mode !== "forgot" ? `${tabsId}-${mode}` : undefined} className="auth-panel">
                <h1 id={`${tabsId}-heading`} ref={headingRef} tabIndex={-1}>{copy.title}</h1>
                <p className="muted">{copy.lead}</p>
                {noticeLine}
                <form className="form auth-form" onSubmit={submit} noValidate>
                  {mode === "register" ? (
                    <>
                      <label htmlFor={nameId} className="form__label">Nome</label>
                      <input id={nameId} className="form__input" name="name" type="text" autoComplete="name"
                        maxLength={40} value={name} onChange={(event) => setName(event.target.value)}
                        aria-invalid={error !== null} aria-describedby={error ? errorId : undefined} />
                    </>
                  ) : null}
                  <label htmlFor={emailId} className="form__label">E-mail</label>
                  <input id={emailId} className="form__input" name="email" type="email" autoComplete="email"
                    autoCapitalize="none" spellCheck={false} value={email}
                    onChange={(event) => setEmail(event.target.value)} aria-invalid={error !== null}
                    aria-describedby={error ? errorId : undefined} />
                  {mode !== "forgot"
                    ? passwordField("Senha", mode === "login" ? "current-password" : "new-password",
                      [mode === "register" ? rulesId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined)
                    : null}
                  {mode === "register" ? (
                    <>
                      <PasswordRules id={rulesId} password={password} />
                      <label htmlFor={confirmationId} className="form__label">Confirmar senha</label>
                      <input id={confirmationId} className="form__input" name="password-confirmation"
                        type={showPassword ? "text" : "password"} autoComplete="new-password" value={confirmation}
                        onChange={(event) => setConfirmation(event.target.value)} aria-invalid={error !== null}
                        aria-describedby={error ? errorId : undefined} />
                    </>
                  ) : null}
                  {errorLine}
                  <button type="submit" className="button auth-wide" disabled={sending}>
                    {sending ? "Enviando…" : copy.submit}
                  </button>
                </form>
                {mode === "login" ? (
                  <p className="auth-hint auth-hint--center"><Link to={loginPath(next, "forgot")}>Esqueci minha senha</Link></p>
                ) : mode === "forgot" ? (
                  <p className="auth-hint auth-hint--center"><Link to={loginPath(next)}>Voltar para entrar</Link></p>
                ) : null}
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
