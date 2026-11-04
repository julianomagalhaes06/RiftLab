import "./auth.css";
import { useState } from "react";
import { useAuth } from "./authContext";

/** Usuário da sessão e o botão Sair, no topo (Sprint 2, #14). Sem sessão, não aparece. */
export function UserMenu() {
  const { user, signOut } = useAuth();
  const [leaving, setLeaving] = useState(false);
  if (!user) return null;
  return (
    <div className="user-menu">
      <span className="user-menu__name"><span className="sr-only">Conectado como </span>{user.username}</span>
      <button type="button" className="button button--secondary user-menu__out" disabled={leaving}
        onClick={() => { setLeaving(true); void signOut().finally(() => setLeaving(false)); }}>
        {leaving ? "Saindo…" : "Sair"}
      </button>
    </div>
  );
}
