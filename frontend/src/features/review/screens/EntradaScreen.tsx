import { useState } from "react";
import { PrototypeBanner } from "../components/PrototypeBanner";
import { QualityNotice } from "../components/QualityNotice";
import { StepHeading } from "../components/StepHeading";

const MATCH_ID_PATTERN = /^BR1_[0-9]{1,20}$/;

interface EntradaScreenProps {
  onOpenSuccess: () => void;
  onOpenUnavailable: () => void;
  onOpenSchema: (schema: "avaliacao" | "operacao") => void;
}

type Notice = { tone: "warning" | "unavailable"; label: string; message: string } | null;

export function EntradaScreen({ onOpenSuccess, onOpenUnavailable, onOpenSchema }: EntradaScreenProps) {
  const [matchId, setMatchId] = useState("");
  const [notice, setNotice] = useState<Notice>(null);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = matchId.trim();
    if (trimmed.length === 0) {
      setNotice({
        tone: "warning",
        label: "Campo obrigatório",
        message: "Informe um ID de partida ou use \"Abrir demonstração\" abaixo.",
      });
      return;
    }
    if (!MATCH_ID_PATTERN.test(trimmed)) {
      setNotice({
        tone: "warning",
        label: "ID inválido (protótipo)",
        message: "Formato esperado: BR1_ seguido de números. Este é um exemplo de validação, não uma busca real.",
      });
      return;
    }
    setNotice(null);
    onOpenSuccess();
  }

  function handleLiveMatch() {
    setNotice({
      tone: "unavailable",
      label: "Partida em andamento",
      message:
        "RiftLab não acompanha partidas em andamento (sem overlay, Live Client Data ou Spectator). Disponível somente pós-partida ou em cenário didático.",
    });
  }

  return (
    <section aria-labelledby="entrada-heading">
      <StepHeading stepKey="entrada" id="entrada-heading">
        Abrir uma partida (protótipo)
      </StepHeading>
      <PrototypeBanner />
      <p>
        Análise exclusivamente pós-partida, fila ranqueada solo/duo, região BR1. Modo
        demonstração não exige chave da Riot.
      </p>

      <form onSubmit={handleSubmit} noValidate>
        <div className="field">
          <label htmlFor="match-id-input">ID da partida (ex.: BR1_1234567890)</label>
          <input
            id="match-id-input"
            name="matchId"
            type="text"
            autoComplete="off"
            value={matchId}
            onChange={(event) => {
              setMatchId(event.target.value);
              setNotice(null);
            }}
          />
        </div>
        <button type="submit">Abrir (simulado)</button>
      </form>

      <p className="section-divider">ou</p>
      <button type="button" onClick={onOpenSuccess}>
        Abrir demonstração (sem ID)
      </button>

      {notice ? (
        <QualityNotice tone={notice.tone} label={notice.label}>
          {notice.message}
        </QualityNotice>
      ) : null}

      <fieldset className="scenario-fieldset">
        <legend>Cenários ilustrativos adicionais</legend>
        <button type="button" onClick={onOpenUnavailable}>
          Simular acesso indisponível
        </button>
        <button type="button" onClick={handleLiveMatch}>
          Simular partida em andamento
        </button>
      </fieldset>

      <nav aria-label="Esquemas não interativos">
        <p>Ver também (esquema, não interativo nesta etapa):</p>
        <ul>
          <li>
            <button type="button" className="link-button" onClick={() => onOpenSchema("avaliacao")}>
              Avaliação e exportação
            </button>
          </li>
          <li>
            <button type="button" className="link-button" onClick={() => onOpenSchema("operacao")}>
              Operação e expurgo
            </button>
          </li>
        </ul>
      </nav>
    </section>
  );
}
