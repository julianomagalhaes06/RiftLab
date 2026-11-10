import { useEffect, useState } from "react";
import { useRouter } from "../../shared/router/context";
import { Link } from "../../shared/router/Link";

const MARK = "conta";
const VIA = "via";
// Duração da saída em auth.css (`account-toast-out`).
const EXIT_MS = 200;
const SERVICES: Record<string, string> = { google: "Google", discord: "Discord" };

type Notice = { kind: "created" } | { kind: "linked"; service: string };

/** Lê a marca que a volta do login social deixa no endereço (`conta=nova`, `conta=ligada&via=google`). */
function readNotice(search: string): Notice | null {
  const params = new URLSearchParams(search);
  const mark = params.get(MARK);
  if (mark === "nova") return { kind: "created" };
  const service = SERVICES[params.get(VIA) ?? ""];
  if (mark === "ligada" && service) return { kind: "linked", service };
  return null;
}

/**
 * Cartão flutuante na volta do login com Google ou Discord: conta recém-criada, quando a pessoa
 * volta à página de onde saiu, ou serviço ligado a uma conta que já existia com o mesmo e-mail
 * (o dono também recebe um e-mail). Não cobre o conteúdo nem some sozinho, porque traz uma
 * ação. A marca sai do endereço logo que é lida.
 */
export function AccountNotice() {
  const { href, route, navigate } = useRouter();
  const [notice] = useState(() => readNotice(window.location.search));
  const [visible, setVisible] = useState(notice !== null);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    const url = new URL(href, window.location.origin);
    if (readNotice(url.search) === null) return;
    url.searchParams.delete(MARK);
    url.searchParams.delete(VIA);
    navigate(url.pathname + url.search, { replace: true });
  }, [href, navigate]);
  // Sai com uma animação curta antes de deixar a página; com movimento reduzido, some na hora.
  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => setVisible(false), EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [leaving]);
  if (!visible || notice === null) return null;
  const dismiss = () => setLeaving(true);
  return (
    <div className={leaving ? "account-toast account-toast--leaving" : "account-toast"} role="status">
      <div className="account-toast__card surface surface--pad">
        <span className="account-toast__badge" aria-hidden="true">
          <svg viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 8.5l3 3 7-7" />
          </svg>
        </span>
        {notice.kind === "created" ? (
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
        ) : (
          <div className="account-toast__body">
            <strong>{notice.service} ligado à sua conta</strong>
            <p>Este e-mail já tinha conta no RiftLab. Agora você também entra com o {notice.service}.</p>
            <div className="account-toast__actions">
              <button type="button" className="button button--secondary" onClick={dismiss}>Entendi</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
