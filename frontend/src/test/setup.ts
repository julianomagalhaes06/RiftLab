import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Buscas recentes (CC-1208) ficam no localStorage: um teste não herda as do anterior.
afterEach(() => {
  window.localStorage.clear();
});

// jsdom não implementa scroll: troca de rota chama window.scrollTo, que sem isto logaria
// "Not implemented" no console a cada teste.
window.scrollTo = () => {};
