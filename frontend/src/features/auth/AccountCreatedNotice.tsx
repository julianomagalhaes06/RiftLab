import { useEffect, useState } from "react";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";

const MARK = "conta";
// Duração da saída em auth.css (`account-toast-out`).
const EXIT_MS = 200;

/**
 * Cartão flutuante de conta recém-criada pelo Google ou Discord, quando a pessoa volta à página
 * de onde saiu. Não cobre o conteúdo nem some sozinho, porque traz uma ação. A marca sai do
 * endereço logo que é lida.
 */
export function AccountCreatedNotice() {
  const { href, route, navigate } = useRouter();
  const [visible, setVisible] = useState(() => new URLSearchParams(window.location.search).get(MARK) === "nova");
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    const url = new URL(href, window.location.origin);
    if (url.searchParams.get(MARK) !== "nova") return;
    url.searchParams.delete(MARK);
    navigate(url.pathname + url.search, { replace: true });
  }, [href, navigate]);
  // Sai com uma animação curta antes de deixar a página; com movimento reduzido, some na hora.
  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => setVisible(false), EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [leaving]);
  if (!visible) return null;
  const dismiss = () => setLeaving(true);
  return (
    <div className={leaving ? "account-toast account-toast--leaving" : "account-toast"} role="status">
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
              <Link to="/conta" className="button" onClick={dismiss}>Vincular agora</Link>
            )}
            <button type="button" className="button button--secondary" onClick={dismiss}>Agora não</button>
          </div>
        </div>
      </div>
    </div>
  );
}
