import { PrototypeBanner } from "../components/PrototypeBanner";
import { StepHeading } from "../components/StepHeading";

interface AvaliacaoEsquemaScreenProps {
  onBack: () => void;
}

export function AvaliacaoEsquemaScreen({ onBack }: AvaliacaoEsquemaScreenProps) {
  return (
    <section aria-labelledby="avaliacao-heading">
      <StepHeading stepKey="avaliacao-esquema" id="avaliacao-heading">
        Avaliação e exportação (esquema)
      </StepHeading>
      <PrototypeBanner />
      <p className="schema-note">
        Esquema — não interativo nesta etapa. Comportamento real chega em CC-304/CC-305 (revisão) e
        CC-505 (feedback e exportação).
      </p>
      <ul>
        <li>Revisor avalia alternativas em ordem aleatorizada, sem identidade do modelo visível.</li>
        <li>Nota de 0 (inviável) a 3 (adequada), com opção de empate ou abstenção.</li>
        <li>Estado vazio: nenhuma análise disponível para avaliar ainda.</li>
        <li>Estado de erro: falha ao registrar feedback, com nova tentativa.</li>
        <li>
          Exportação de relatório em JSON/HTML, sem credenciais, PUUID ou Riot ID; HTML escapado, sem
          scripts externos.
        </li>
        <li>Retorno claro para a lista de decisões após avaliar ou exportar.</li>
      </ul>
      <div className="actions">
        <button type="button" onClick={onBack}>
          Voltar
        </button>
      </div>
    </section>
  );
}
