import { useCallback, useMemo, useState, type ReactNode } from "react";
import { DemoNoticeContext } from "./demoNotice";

export function DemoNoticeProvider({ children }: { children: ReactNode }) {
  const [keys, setKeys] = useState<ReadonlySet<string>>(new Set());
  const set = useCallback((key: string, on: boolean) => {
    setKeys((current) => {
      if (current.has(key) === on) return current;
      const next = new Set(current);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);
  const value = useMemo(() => ({ visible: keys.size > 0, set }), [keys, set]);
  return <DemoNoticeContext.Provider value={value}>{children}</DemoNoticeContext.Provider>;
}
