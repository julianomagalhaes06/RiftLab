import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { MatchSummary } from "../../shared/api/client";
import { RouterProvider } from "../../shared/router/RouterProvider";
import { matchSummary } from "../../test/mockApi";
import { MatchCard } from "./MatchCard";

function renderCard(overrides: Record<string, unknown>) {
  const match = matchSummary({ matchId: "BR1_1", ...overrides }) as unknown as MatchSummary;
  render(
    <RouterProvider>
      <ul>
        <MatchCard match={match} />
      </ul>
    </RouterProvider>,
  );
}

describe("selo da revisão no cartão do Histórico", () => {
  it("mostra quantas compras da conta foram comparadas com a referência", () => {
    renderCard({ reviewPurchases: { ready: 2, total: 3 } });
    expect(screen.getByText("Revisão: 2 de 3 compras")).toBeInTheDocument();
  });

  it("avisa quando nenhuma compra pôde ser revisada", () => {
    renderCard({ reviewPurchases: { ready: 0, total: 2 } });
    expect(screen.getByText("Sem compras para revisar")).toBeInTheDocument();
  });

  it("sem dados de revisão, não mostra selo", () => {
    renderCard({ reviewPurchases: null });
    expect(screen.queryByText(/Revisão:|Sem compras para revisar/)).not.toBeInTheDocument();
  });
});
