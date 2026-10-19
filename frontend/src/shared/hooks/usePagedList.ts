import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError } from "../api/errors";

export interface Page<T> {
  items: T[];
  nextCursor?: string | null;
}

export interface UsePagedListOptions<T> {
  /** Muda quando a consulta muda (outra partida, outro participante): recarrega do zero. */
  resetKey: string;
  fetchPage: (cursor: string | null, signal: AbortSignal) => Promise<Page<T>>;
  enabled?: boolean;
}

interface PagedState<T> {
  key: string;
  items: T[];
  nextCursor: string | null;
  status: "loading" | "ready" | "error";
  error: unknown;
  loadingMore: boolean;
  loadMoreError: unknown;
}

export interface UsePagedListResult<T> {
  items: T[];
  nextCursor: string | null;
  status: "loading" | "ready" | "error";
  error: unknown;
  loadingMore: boolean;
  loadMoreError: unknown;
  loadMore(): void;
  reload(): void;
  /** Rebusca a primeira página sem voltar a "carregando"; falha mantém a lista atual. */
  refresh(): void;
}

function initial<T>(key: string): PagedState<T> {
  return {
    key,
    items: [],
    nextCursor: null,
    status: "loading",
    error: null,
    loadingMore: false,
    loadMoreError: null,
  };
}

/**
 * Lista paginada por cursor ("carregar mais"): primeira página ao montar/mudar
 * `resetKey`, próximas sob demanda, tudo cancelável (AbortController) ao sair da tela
 * ou ao trocar a consulta. Uma resposta atrasada de consulta antiga nunca entra na lista.
 */
export function usePagedList<T>(options: UsePagedListOptions<T>): UsePagedListResult<T> {
  const { resetKey, enabled = true } = options;
  const latest = useRef(options);
  useEffect(() => {
    latest.current = options;
  });

  const [state, setState] = useState<PagedState<T>>(() => initial<T>(resetKey));
  const [reloads, setReloads] = useState(0);
  const quiet = useRef(false);
  const moreController = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    const controller = new AbortController();
    latest.current
      .fetchPage(null, controller.signal)
      .then((page) => {
        quiet.current = false;
        setState({
          key: resetKey,
          items: page.items,
          nextCursor: page.nextCursor ?? null,
          status: "ready",
          error: null,
          loadingMore: false,
          loadMoreError: null,
        });
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) {
          return;
        }
        if (quiet.current) {
          quiet.current = false;
          return;
        }
        setState({ ...initial<T>(resetKey), status: "error", error });
      });
    return () => {
      controller.abort();
      moreController.current?.abort();
    };
  }, [resetKey, enabled, reloads]);

  const current = state.key === resetKey ? state : initial<T>(resetKey);

  const loadMore = useCallback(() => {
    if (current.status !== "ready" || current.loadingMore || current.nextCursor === null) {
      return;
    }
    const controller = new AbortController();
    moreController.current = controller;
    const cursor = current.nextCursor;
    setState({ ...current, loadingMore: true, loadMoreError: null });
    latest.current
      .fetchPage(cursor, controller.signal)
      .then((page) => {
        setState((previous) =>
          previous.key === resetKey
            ? {
                ...previous,
                items: [...previous.items, ...page.items],
                nextCursor: page.nextCursor ?? null,
                loadingMore: false,
              }
            : previous,
        );
      })
      .catch((error: unknown) => {
        if (!isAbortError(error)) {
          setState((previous) =>
            previous.key === resetKey
              ? { ...previous, loadingMore: false, loadMoreError: error }
              : previous,
          );
        }
      });
  }, [current, resetKey]);

  const reload = useCallback(() => {
    setState(initial<T>(resetKey));
    setReloads((count) => count + 1);
  }, [resetKey]);

  const refresh = useCallback(() => {
    quiet.current = true;
    setReloads((count) => count + 1);
  }, []);

  return { ...current, loadMore, reload, refresh };
}
