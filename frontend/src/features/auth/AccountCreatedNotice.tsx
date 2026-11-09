import { useEffect, useState } from "react";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";

const MARK = "conta";

/**
 * Aviso curto de conta recém-criada pelo Google ou Discord, quando a pessoa volta à página de
 * onde saiu (sem a tela de boas-vindas). A marca sai do endereço logo que é lida.
 */
export function AccountCreatedNotice() {
  const { href, navigate } = useRouter();
  const [visible, setVisible] = useState(() => new URLSearchParams(window.location.search).get(MARK) === "nova");
  useEffect(() => {
    const url = new URL(href, window.location.origin);
    if (url.searchParams.get(MARK) !== "nova") return;
    url.searchParams.delete(MARK);
    navigate(url.pathname + url.search, { replace: true });
  }, [href, navigate]);
  if (!visible) return null;
  return (
    <div className="notice notice--info account-created" role="status">
      <p>Sua conta foi criada. Para ver seu Painel e sua posição, <Link to="/conta">vincule sua conta Riot</Link>.</p>
      <button type="button" className="button button--secondary" onClick={() => setVisible(false)}>Fechar</button>
    </div>
  );
}
