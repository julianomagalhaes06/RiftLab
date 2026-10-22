import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Buscas recentes (CC-1208) ficam no localStorage: um teste não herda as do anterior.
afterEach(() => {
  window.localStorage.clear();
});
