import "./auth.css";
import { useState } from "react";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";
import { loginPath } from "../../shared/router/routes";
import { useAuth } from "./authContext";

/** Usuário da sessão e o botão Sair, no topo. Sem sessão e com contas ativas, Entrar e Criar
 * conta (voltando à página atual). Numa instalação sem login exigido, não aparece. Sair leva
 * ao início, de qualquer página. */
export function UserMenu() {
  const { user, authRequired, signOut } = useAuth();
  const { href, route, navigate } = useRouter();
  const [leaving, setLeaving] = useState(false);
  if (!user) {
    if (!authRequired) return null;
    const next = route.name === "home" ? null : href;
    return (
      <div className="user-menu user-menu--anonymous">
        <Link to={loginPath(next)} className="button button--secondary user-menu__out">Entrar</Link>
        <Link to={loginPath(next, "register")} className="button user-menu__out">Criar conta</Link>
      </div>
    );
  }
  return (
    <div className="user-menu">
      <span className="user-menu__name">
        <Link to="/conta" aria-label={`Minha conta (${user.email})`} title={user.email}>{user.name ?? user.email}</Link>
      </span>
      <button type="button" className="button button--secondary user-menu__out" disabled={leaving}
        onClick={() => {
          setLeaving(true);
          // Sair é uma escolha: volta ao início, em vez de cair no convite da área da conta.
          void signOut().finally(() => {
            setLeaving(false);
            navigate("/", { replace: true });
          });
        }}>
        {leaving ? "Saindo…" : "Sair"}
      </button>
    </div>
  );
}
