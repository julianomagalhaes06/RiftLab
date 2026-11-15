import { expect, test } from "@playwright/test";
import { backstageFixture, riotCall } from "../../src/features/backstage/backstageFixture";
import { mockApi, ok, sequence } from "./support/mock-api";

const evidence = "../.private/validation/demo-real/bastidores";
const admin = { id: "u-admin", email: "admin@example.test", name: "Admin", isAdmin: true };

for (const width of [360, 1280, 1440]) {
  test(`Bastidores em ${width}px: chamadas ao vivo chegam e a página não rola de lado`, async ({ page }) => {
    await page.route("https://**/*", (route) => route.abort());
    await page.setViewportSize({ width, height: 900 });
    const first = backstageFixture();
    const second = backstageFixture({
      calls: [riotCall(14, { kind: "match" }), riotCall(13, { kind: "timeline" }), ...first.calls],
    });
    await mockApi(page, {
      "GET /api/v1/auth/session": () => ok({ authRequired: true, user: admin, demoEnabled: false, socialProviders: [] }),
      "GET /api/v1/admin/backstage": sequence(() => ok(first), () => ok(second)),
    });
    await page.goto("/bastidores");
    await expect(page.getByRole("heading", { name: "O que o RiftLab está fazendo agora" })).toBeVisible();
    const live = page.getByRole("region", { name: /Chamadas à API da Riot, ao vivo/ });
    await expect(live.getByRole("row")).toHaveCount(6);
    // Segunda leitura (2 s depois): duas chamadas novas no topo, destacadas.
    await expect(live.getByRole("row")).toHaveCount(8, { timeout: 6000 });
    await expect(live.locator(".backstage-calls__row--fresh")).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `${evidence}/bastidores-${width}.png`, fullPage: true });
  });
}
