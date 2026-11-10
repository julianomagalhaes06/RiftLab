import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "../../shared/router/context";
import { AuthContext, type AuthState } from "./authContext";
import { AUTH_REQUIRED_EVENT, api, type SessionUser, type SocialProvider } from "../../shared/api/client";

/**
 * Sessão local (Sprint 2, #10). Consulta `/api/v1/auth/session` ao abrir e sempre que
 * uma resposta 401 AUTH_REQUIRED chega. Falha na consulta não bloqueia a interface: quem
 * decide é o servidor, que continua recusando sem sessão quando o login é exigido.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [checked, setChecked] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [user, setUser] = useState<SessionUser | null>(null);
  const [socialProviders, setSocialProviders] = useState<readonly SocialProvider[]>([]);

  const { navigate } = useRouter();
  const [demoEnabled, setDemoEnabled] = useState(false);
  const hadUser = useRef(false);
  useEffect(() => {
    hadUser.current = user !== null;
  }, [user]);

  // `expired`: uma chamada voltou 401. Se havia sessão e ela acabou, a pessoa volta ao início.
  const refresh = useCallback((signal?: AbortSignal, expired = false) => {
    api.getAuthSession(signal).then((session) => {
      setAuthRequired(session.authRequired);
      setUser(session.user ?? null);
      if (expired && !session.user && hadUser.current) navigate("/", { replace: true });
      setSocialProviders(session.socialProviders ?? []);
      setDemoEnabled(session.demoEnabled ?? false);
    }).catch(() => {
      if (!signal?.aborted) setAuthRequired(false);
    }).finally(() => {
      if (!signal?.aborted) setChecked(true);
    });
  }, [navigate]);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    const onExpired = () => refresh(undefined, true);
    window.addEventListener(AUTH_REQUIRED_EVENT, onExpired);
    return () => {
      controller.abort();
      window.removeEventListener(AUTH_REQUIRED_EVENT, onExpired);
    };
  }, [refresh]);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      // Na mesma atualização: sair de uma área da conta vai ao início, não à tela de entrar.
      setUser(null);
      navigate("/", { replace: true });
      refresh();
    }
  }, [refresh, navigate]);

  const value = useMemo<AuthState>(() => ({
    checked, authRequired, user, socialProviders, signOut, hasAccount: !authRequired || user !== null, demoEnabled,
    signedIn: (next) => { setUser(next); setChecked(true); },
  }), [checked, authRequired, user, socialProviders, signOut, demoEnabled]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
