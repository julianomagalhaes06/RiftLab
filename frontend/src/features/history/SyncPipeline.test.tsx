import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { HistorySyncStatus } from "../../shared/api/client";
import { SyncPipeline } from "./SyncPipeline";

const base: HistorySyncStatus = { state: "SUCCEEDED", listed: 0, newEnqueued: 0, alreadyKnown: 0, skippedPurged: 0 };

function steps() {
  return within(screen.getByRole("list")).getAllByRole("listitem");
}

describe("Etapas visíveis da atualização com a Riot", () => {
  it("mostra cada consulta com o tempo e o download com progresso e o que foi lido", () => {
    render(
      <SyncPipeline
        status={{
          ...base,
          listed: 20,
          newEnqueued: 8,
          alreadyKnown: 12,
          finishedAt: "2026-11-15T18:00:03Z",
          steps: { accountMs: 182, listMs: 240, leagueMs: 95 },
          imports: { total: 8, succeeded: 3, failed: 0, pending: 5 },
          evidence: { matches: 3, participants: 30, timelineEvents: 4210, itemPurchases: 236 },
        }}
      />,
    );
    const [account, list, league, download, evidence] = steps();
    expect(account).toHaveTextContent("Conta encontrada na Riot");
    expect(account).toHaveTextContent("182 ms");
    expect(account).toHaveTextContent("Account-V1");
    expect(list).toHaveTextContent("20 partidas · 240 ms");
    expect(league).toHaveTextContent("Elo consultado");
    expect(download).toHaveTextContent("Baixando partidas e linhas do tempo");
    expect(download).toHaveTextContent("3 de 8 partidas · 12 já no RiftLab");
    expect(within(download).getByRole("progressbar")).toHaveAttribute("value", "3");
    expect(evidence).toHaveTextContent("4.210 eventos · 236 compras de itens · 30 participantes");
  });

  it("aponta a etapa que falhou quando a Riot recusa a chave", () => {
    render(<SyncPipeline status={{ ...base, state: "FAILED", reason: "RIOT_AUTH_FAILED", steps: { accountMs: 150 } }} />);
    const [account, list, league] = steps();
    expect(account).toHaveTextContent("concluída");
    expect(list).toHaveTextContent("falhou");
    expect(league).toHaveTextContent("aguardando");
    expect(screen.queryByText(/Baixando/)).not.toBeInTheDocument();
  });

  it("explica quando não havia partida nova e identifica a busca anterior", () => {
    render(
      <SyncPipeline
        status={{
          ...base,
          reason: "COOLDOWN",
          listed: 5,
          alreadyKnown: 5,
          finishedAt: "2026-11-15T18:00:03Z",
          steps: { accountMs: 120, listMs: 200, leagueMs: 80 },
        }}
      />,
    );
    expect(screen.getByText(/Última busca na API oficial da Riot/)).toBeInTheDocument();
    expect(steps()[3]).toHaveTextContent("5 partidas listadas já estavam no RiftLab");
  });

  it("some sem execução registrada e mostra a fila enquanto a busca não começou", () => {
    const { container, rerender } = render(<SyncPipeline status={{ ...base, state: "IDLE" }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<SyncPipeline status={{ ...base }} />);
    expect(container).toBeEmptyDOMElement(); // execução antiga, sem durações
    rerender(<SyncPipeline status={{ ...base, state: "QUEUED" }} />);
    expect(screen.getByText("Buscando agora na API oficial da Riot")).toBeInTheDocument();
    expect(steps()).toHaveLength(3);
  });
});
