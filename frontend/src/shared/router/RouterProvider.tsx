import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { RouterContext, type Router } from "./context";
import { parseRoute, routeToPath, type Route } from "./routes";

interface LocationState {
  pathname: string;
  search: string;
}

function readLocation(): LocationState {
  return { pathname: window.location.pathname, search: window.location.search };
}

/**
 * Mantém a rota atual em sincronia com `window.location` (History API): links e
 * `navigate` usam pushState/replaceState, e voltar/avançar (popstate) atualizam a rota.
 */
export function RouterProvider({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<LocationState>(readLocation);

  useEffect(() => {
    const onPopState = () => setLocation(readLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((to: string | Route, options?: { replace?: boolean }) => {
    const target = typeof to === "string" ? to : routeToPath(to);
    const url = new URL(target, window.location.origin);
    if (url.origin !== window.location.origin) {
      return; // nunca navega para fora da aplicação pelo roteador
    }
    const next = `${url.pathname}${url.search}`;
    const current = `${window.location.pathname}${window.location.search}`;
    if (options?.replace || next === current) {
      window.history.replaceState(null, "", next);
    } else {
      window.history.pushState(null, "", next);
    }
    setLocation({ pathname: url.pathname, search: url.search });
  }, []);

  const value = useMemo<Router>(
    () => ({
      route: parseRoute(location.pathname, location.search),
      href: `${location.pathname}${location.search}`,
      navigate,
    }),
    [location, navigate],
  );

  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}
