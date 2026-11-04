import { createContext, useContext } from "react";
import type { SessionUser } from "../../shared/api/client";

export interface AuthState {
  checked: boolean;
  authRequired: boolean;
  user: SessionUser | null;
  /** Grava o resultado de login/cadastro sem nova consulta. */
  signedIn: (user: SessionUser) => void;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthState>({
  checked: true,
  authRequired: false,
  user: null,
  signedIn: () => undefined,
  signOut: async () => undefined,
});

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
