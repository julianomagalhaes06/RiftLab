import { useEffect, useState } from "react";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";

const MARK = "conta";

/**
 * Cartão flutuante de conta recém-criada pelo Google ou Discord, quando a pessoa volta à página
 * de onde saiu. Não cobre o conteúdo nem some sozinho, porque traz uma ação. A marca sai do
 * endereço logo que é lida.
 */
export function AccountCreatedNotice() {
  const { href, route, navigate } = useRouter();
  const [visible, setVisible] = useState(() => new URLSearchParams(window.location.search).get(MARK) === "nova");
  useEffect(() => {
    const url = new URL(href, window.location.origin);
    if (url.searchParams.get(MARK) !== "nova") return;
    url.searchParams.delete(MARK);
    navigate(url.pathname + url.search, { replace: true });
  }, [href, navigate]);
  if (!visible) return null;
  return (
    <div className="account-toast" role="status">
      <div className="account-toast__card surface surface--pad">
        <span className="account-toast__badge" aria-hidden="true">
          <svg viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 8.5l3 3 7-7" />
          </svg>
        </span>
        <div className="account-toast__body">
          <strong>Conta criada</strong>
          <p>Vincule sua conta Riot para ver seu Painel e sua posição.</p>
          <div className="account-toast__actions">
            {route.name === "account" ? null : (
              <Link to="/conta" className="button" onClick={() => setVisible(false)}>Vincular agora</Link>
            )}
            <button type="button" className="button button--secondary" onClick={() => setVisible(false)}>Agora não</button>
          </div>
        </div>
      </div>
    </div>
  );
}
