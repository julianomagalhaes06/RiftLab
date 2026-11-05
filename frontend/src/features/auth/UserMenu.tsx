import "./auth.css";
import { useState } from "react";
import { Link } from "../../shared/router/Link";
import { useAuth } from "./authContext";

/** Usuário da sessão e o botão Sair, no topo (Sprint 2, #14). Sem sessão, não aparece. */
export function UserMenu() {
  const { user, signOut } = useAuth();
  const [leaving, setLeaving] = useState(false);
  if (!user) return null;
  return (
    <div className="user-menu">
      <span className="user-menu__name">
        {user.guest
          ? <Link to="/conta" aria-label="Minha conta (visitante)">Visitante</Link>
          : <Link to="/conta" aria-label={`Minha conta (${user.username})`}>{user.username}</Link>}
      </span>
      <button type="button" className="button button--secondary user-menu__out" disabled={leaving}
        onClick={() => { setLeaving(true); void signOut().finally(() => setLeaving(false)); }}>
        {leaving ? "Saindo…" : "Sair"}
      </button>
    </div>
  );
}
