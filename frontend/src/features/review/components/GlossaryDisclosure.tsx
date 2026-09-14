import { GLOSSARY } from "../data/glossary";

/**
 * <details>/<summary> nativos: operavel por teclado (Enter/Espaço) e leitor de
 * tela sem depender de hover ou JavaScript de abrir/fechar.
 */
export function GlossaryDisclosure() {
  return (
    <details className="glossary">
      <summary>Glossário</summary>
      <dl>
        {GLOSSARY.map((entry) => (
          <div className="glossary__entry" key={entry.term}>
            <dt>{entry.term}</dt>
            <dd>{entry.definition}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
