import { PrototypeBanner } from "../components/PrototypeBanner";
import { StepHeading } from "../components/StepHeading";

interface OperacaoEsquemaScreenProps {
  onBack: () => void;
}

export function OperacaoEsquemaScreen({ onBack }: OperacaoEsquemaScreenProps) {
  return (
    <section aria-labelledby="operacao-heading">
      <StepHeading stepKey="operacao-esquema" id="operacao-heading">
        Operação e expurgo (esquema)
      </StepHeading>
      <PrototypeBanner />
      <p className="schema-note">
        Esquema — não interativo nesta etapa. Comportamento real chega em CC-603 (retenção e
        expurgo).
      </p>
      <ul>
        <li>Operador consulta partidas importadas, modelo ativo e falhas de importação.</li>
        <li>Estado vazio: nenhuma partida importada ainda.</li>
        <li>Expurgo exige confirmação explícita na interface antes de remover dados.</li>
        <li>
          Expurgo remove partida, estados, análises, feedback vinculado e arquivos brutos; invalida
          datasets/modelos dependentes até reavaliação.
        </li>
        <li>Estado de erro: falha ao expurgar, com causa e nova tentativa.</li>
      </ul>
      <div className="actions">
        <button type="button" onClick={onBack}>
          Voltar
        </button>
      </div>
    </section>
  );
}
