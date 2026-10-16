import { createContext, useContext } from "react";

export type ItemNamesMap = Readonly<Record<string, string>>;
export const ItemNamesContext = createContext<ItemNamesMap>({});
export const ItemVersionContext = createContext<string | null>(null);

export function useItemName() {
  const names = useContext(ItemNamesContext);
  return (item: { itemId: number; name?: string | null }) => names[item.itemId] || item.name || `Item #${item.itemId}, nome indisponível`;
}
