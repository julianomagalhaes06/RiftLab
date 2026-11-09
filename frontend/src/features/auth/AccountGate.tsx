import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";
import { loginPath } from "../../shared/router/routes";
import "./auth.css";

/**
 * Convite dentro de uma página pública, quando a pessoa sem conta chega a algo que guarda dados
 * dela (a análise e o estudo de uma decisão). Volta para o mesmo endereço depois do login.
 */
export function AccountGate({ title, lead }: { title: string; lead: string }) {
  const { href } = useRouter();
  return (
    <aside className="account-gate surface surface--pad brackets" aria-labelledby="account-gate-title">
      <div>
        <p className="page-head__context">Disponível com conta</p>
        <h3 id="account-gate-title">{title}</h3>
        <p className="muted">{lead}</p>
      </div>
      <div className="account-gate__actions">
        <Link to={loginPath(href, "register")} className="button">Criar conta</Link>
        <Link to={loginPath(href)} className="button button--secondary">Entrar</Link>
      </div>
    </aside>
  );
}
