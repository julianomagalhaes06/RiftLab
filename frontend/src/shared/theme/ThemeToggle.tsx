import { useState } from "react";
import { applyTheme, currentTheme, type Theme } from "./theme";
import "./theme.css";

/** Seletor de tema no topo: sol (claro) e lua (escuro), com a escolha guardada no navegador. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const light = theme === "light";
  const icon = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  return (
    <button
      type="button"
      className="theme-toggle"
      role="switch"
      aria-checked={light}
      aria-label="Tema claro"
      title={light ? "Mudar para o tema escuro" : "Mudar para o tema claro"}
      onClick={() => {
        const next: Theme = light ? "dark" : "light";
        applyTheme(next);
        setTheme(next);
      }}
    >
      <span className={light ? "theme-toggle__side is-active" : "theme-toggle__side"}>
        <svg {...icon}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
      </span>
      <span className={light ? "theme-toggle__side" : "theme-toggle__side is-active"}>
        <svg {...icon}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></svg>
      </span>
    </button>
  );
}
