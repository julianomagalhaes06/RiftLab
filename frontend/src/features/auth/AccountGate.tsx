import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";
import { loginPath } from "../../shared/router/routes";
import "./auth.css";

/**
 * Convite para criar conta ou entrar quando a pessoa, navegando sem conta, chega a algo que
 * guarda dados dela (análise, estudo, Painel). Volta para o mesmo endereço depois do login.
 * `inline`: dentro de uma página pública (ex.: no lugar da análise de uma decisão).
 */
export function AccountGate({
  title,
  lead,
  inline = false,
}: {
  title: string;
  lead: string;
  inline?: boolean;
}) {
  const { href } = useRouter();
  const actions = (
    <div className="account-gate__actions">
      <Link to={loginPath(href, "register")} className="button">Criar conta</Link>
      <Link to={loginPath(href)} className="button button--secondary">Entrar</Link>
    </div>
  );
  if (inline) {
    return (
      <aside className="account-gate account-gate--inline surface surface--pad brackets" aria-labelledby="account-gate-title">
        <div>
          <p className="page-head__context">Disponível com conta</p>
          <h3 id="account-gate-title">{title}</h3>
          <p className="muted">{lead}</p>
        </div>
        {actions}
      </aside>
    );
  }
  return (
    <section className="area-page account-gate" aria-labelledby="account-gate-title">
      <header className="page-head">
        <p className="page-head__context">Disponível com conta</p>
        <h2 id="account-gate-title">{title}</h2>
        <p className="page-head__lead">{lead}</p>
      </header>
      <div className="account-gate__card surface surface--pad brackets">
        <p className="muted">
          Sem conta, você continua buscando jogadores e abrindo partidas. A conta fica nesta
          instalação do RiftLab e usa seu e-mail só para confirmar o acesso.
        </p>
        {actions}
        <p className="account-gate__back"><Link to="/">Voltar ao início</Link></p>
      </div>
    </section>
  );
}
