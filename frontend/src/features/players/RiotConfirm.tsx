import "./players.css";

/** Endereço aberto de fora do RiftLab (link, favorito, outro site): nada vai à Riot sem o clique
 * (CC-1218). Dentro do app, a busca, o aviso ao vivo e a sincronização seguem automáticos. */
export function RiotConfirm({ gameName, tagLine, onConfirm, what }: { gameName: string; tagLine: string; onConfirm: () => void; what: string }) {
  return (
    <div className="riot-confirm surface" role="status">
      <p>
        Este endereço foi aberto de fora do RiftLab. {what} consulta a Riot com a chave deste servidor, então só acontece
        quando você pedir.
      </p>
      <button type="button" className="button" onClick={onConfirm}>
        Buscar {gameName}#{tagLine} na Riot
      </button>
    </div>
  );
}
