import { useAuth } from "../features/auth/authContext";
import { Link } from "../shared/router/Link";
import type { Route } from "../shared/router/routes";

// `account`: área da conta própria; sem conta, sai do menu (o início explica o que ela oferece).
const entries = [
  { name: "home", href: "/", label: "Início", icon: "home", account: false },
  { name: "panel", href: "/painel", label: "Painel", icon: "panel", account: true },
  { name: "champions", href: "/campeoes", label: "Campeões", icon: "champions", account: false },
  { name: "position", href: "/minha-posicao", label: "Minha posição", icon: "position", account: true },
  { name: "history", href: "/historico", label: "Histórico", icon: "history", account: true },
] as const;

function NavIcon({ kind }: { kind: typeof entries[number]["icon"] }) {
  const common = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  switch (kind) {
    case "home": return <svg {...common}><path d="M3 11 12 4l9 7" /><path d="M5 10v10h14V10" /><path d="M10 20v-6h4v6" /></svg>;
    case "panel": return <svg {...common}><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /></svg>;
    case "champions": return <svg {...common}><path d="M12 2 20 6v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6l8-4Z" /><path d="m9 12 2 2 4-4" /></svg>;
    case "position": return <svg {...common}><path d="M8 4h8v6a4 4 0 0 1-8 0V4Z" /><path d="M8 6H4v2a4 4 0 0 0 4 4m8-6h4v2a4 4 0 0 1-4 4M12 14v4m-4 3h8" /></svg>;
    case "history": return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" /></svg>;
  }
}

export function MainNav({ route }: { route: Route }) {
  const { hasAccount } = useAuth();
  const active = route.name === "historyMatch" ? "history" : route.name;
  return (
    <nav className="main-nav" aria-label="Áreas principais">
      {entries.filter((entry) => hasAccount || !entry.account).map((entry) => {
        const selected = active === entry.name;
        return (
          <Link key={entry.name} to={entry.href} className={selected ? "main-nav__link main-nav__link--active" : "main-nav__link"} aria-current={selected ? "page" : undefined}>
            <NavIcon kind={entry.icon} />
            <span>{entry.label}</span>
            {selected ? <span className="sr-only"> (área atual)</span> : null}
          </Link>
        );
      })}
    </nav>
  );
}
