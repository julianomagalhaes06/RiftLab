import { createContext, useContext } from "react";
import type { SessionUser, SocialProvider } from "../../shared/api/client";

export interface AuthState {
  checked: boolean;
  authRequired: boolean;
  user: SessionUser | null;
  /** Logins sociais configurados no servidor; sem nenhum, a tela só mostra o formulário. */
  socialProviders: readonly SocialProvider[];
  /** Pode usar o que é da conta (análise, estudo, Painel): com sessão, ou numa instalação sem
   * login exigido. Sem isso, a pessoa só navega pela leitura pública. */
  hasAccount: boolean;
  /** Grava o resultado de login/cadastro sem nova consulta. */
  signedIn: (user: SessionUser) => void;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthState>({
  checked: true,
  authRequired: false,
  user: null,
  socialProviders: [],
  hasAccount: true,
  signedIn: () => undefined,
  signOut: async () => undefined,
});

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
