import { createContext, useContext } from "react";
import type { Route } from "./routes";

export interface Router {
  route: Route;
  /** Caminho + query atuais (ex.: "/matches/DEMO-0008?participant=1"). */
  href: string;
  navigate(to: string | Route, options?: { replace?: boolean }): void;
}

export const RouterContext = createContext<Router | null>(null);

export function useRouter(): Router {
  const router = useContext(RouterContext);
  if (router === null) {
    throw new Error("useRouter fora de <RouterProvider>.");
  }
  return router;
}

export function useRoute(): Route {
  return useRouter().route;
}
