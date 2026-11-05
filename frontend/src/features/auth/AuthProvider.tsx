import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { AuthContext, type AuthState } from "./authContext";
import { AUTH_REQUIRED_EVENT, api, type SessionUser } from "../../shared/api/client";

/**
 * Sessão local (Sprint 2, #10/#14). Consulta `/api/v1/auth/session` ao abrir e sempre que
 * uma resposta 401 AUTH_REQUIRED chega. Falha na consulta não bloqueia a interface: quem
 * decide é o servidor, que continua recusando sem sessão quando o login é exigido.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [checked, setChecked] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [user, setUser] = useState<SessionUser | null>(null);

  const refresh = useCallback((signal?: AbortSignal) => {
    api.getAuthSession(signal).then((session) => {
      setAuthRequired(session.authRequired);
      setUser(session.user ?? null);
    }).catch(() => {
      if (!signal?.aborted) setAuthRequired(false);
    }).finally(() => {
      if (!signal?.aborted) setChecked(true);
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    const onExpired = () => refresh();
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
      setUser(null);
      refresh();
    }
  }, [refresh]);

  const value = useMemo<AuthState>(() => ({
    checked, authRequired, user, signOut,
    signedIn: (next) => { setUser(next); setChecked(true); },
  }), [checked, authRequired, user, signOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
