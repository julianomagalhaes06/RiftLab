import { useContext, useState } from "react";
import { ItemVersionContext } from "../hooks/useItemName";
import "./item-icon.css";

export function ItemIcon({ itemId }: { itemId: number }) {
  const version = useContext(ItemVersionContext);
  const url = version && /^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(version)
    && Number.isSafeInteger(itemId) && itemId > 0
    ? `https://ddragon.leagueoflegends.com/cdn/${version}/img/item/${itemId}.png`
    : null;
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const showImage = url !== null && failedUrl !== url;
  return <span className="item-icon" aria-hidden="true" data-state={showImage ? "image" : "fallback"}>
    {showImage ? <img src={url} alt="" width={32} height={32} loading="lazy" decoding="async"
      referrerPolicy="no-referrer" onError={() => setFailedUrl(url)} /> : null}
  </span>;
}
