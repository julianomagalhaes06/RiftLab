export type Theme = "dark" | "light";
export const THEME_KEY = "riftlab:theme";

/** Tema já aplicado ao documento: o script de `index.html` lê a escolha antes da primeira pintura. */
export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function applyTheme(theme: Theme): void {
  if (theme === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Armazenamento bloqueado: o tema vale só até recarregar a página.
  }
}
