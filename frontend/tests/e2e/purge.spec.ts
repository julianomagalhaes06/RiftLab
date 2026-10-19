import { expect, test } from "@playwright/test";
import { job, mockApi, ok } from "./support/mock-api";

/** Remoção de partida com prévia e confirmação digitada (CC-603), com API simulada. */
const evidence = "../.private/validation/sprint-6/CC-603";

for (const width of [1280, 360]) {
  test(`remover partida em ${width}px: prévia, confirmação pelo id e retorno ao Histórico`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 700 ? 780 : 900 });
    let polls = 0;
    const api = await mockApi(page, {
      "GET /api/v1/imports/:id": () =>
        ok(++polls < 2 ? job({ status: "RUNNING", kind: "PURGE", attempts: 1 }) : job({ status: "SUCCEEDED", kind: "PURGE", attempts: 1 })),
    });
    await page.goto("/matches/DEMO-0008");
    await page.getByText("Remover esta partida do RiftLab").click();
    await expect(page.getByRole("heading", { name: "Remover esta partida" })).toBeVisible();
    await page.getByRole("button", { name: "Ver o que será removido" }).click();
    await expect(page.getByText("40 eventos da linha do tempo")).toBeVisible();
    await expect(page.getByText(/não desaprendem/)).toBeVisible();

    const remove = page.getByRole("button", { name: "Remover definitivamente" });
    await expect(remove).toBeDisabled();
    await page.getByLabel(/Digite o id da partida/).fill("DEMO-0009");
    await expect(remove).toBeDisabled();
    await page.getByLabel(/Digite o id da partida/).fill("DEMO-0008");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `${evidence}/remover-confirmacao-${width}.png`, fullPage: true });
    await remove.click();

    await expect(page.getByRole("status").filter({ hasText: "Partida removida" })).toBeFocused();
    expect(api.count("DELETE /api/v1/matches/:id")).toBe(1);
    await page.getByRole("link", { name: "Voltar ao Histórico" }).click();
    await expect(page.getByRole("heading", { name: "Partidas para estudar" })).toBeVisible();
  });
}
