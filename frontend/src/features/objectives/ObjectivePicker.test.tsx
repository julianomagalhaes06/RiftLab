import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ObjectiveForm } from "../../shared/api/client";
import { createMockApi, json, problem, type MockApi } from "../../test/mockApi";
import { ObjectivePicker } from "./ObjectivePicker";

const URL = /^\/api\/v1\/objectives\/interpret$/;

function Harness({ onChange }: { onChange: (objective: ObjectiveForm) => void }) {
  const [value, setValue] = useState<ObjectiveForm>({ priority: "BALANCED" });
  return (
    <ObjectivePicker
      idPrefix="t"
      value={value}
      onChange={(objective) => {
        setValue(objective);
        onChange(objective);
      }}
    />
  );
}

let mock: MockApi;
beforeEach(() => {
  mock = createMockApi();
  vi.stubGlobal("fetch", mock.fetch);
});
afterEach(() => vi.unstubAllGlobals());

function typeAndInterpret(text: string) {
  fireEvent.change(screen.getByLabelText(/descreva em uma frase/), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Interpretar texto" }));
}

describe("ObjectivePicker (CC-504): texto só vale depois de confirmado", () => {
  it("interpreta, mostra o provedor e só aplica ao confirmar", async () => {
    mock.on("POST", URL, () =>
      json(200, {
        status: "INTERPRETED",
        objective: { priority: "SURVIVAL", parserVersion: "rules_v1" },
        parserVersion: "rules_v1",
        provider: "RULES",
        reasonCodes: [],
      }),
    );
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    typeAndInterpret("não quero dano, quero aguentar");
    await screen.findByText(/Entendi:/);
    expect(screen.getByText(/regras fixas, sem modelo de linguagem/)).toBeInTheDocument();
    expect(mock.callsTo("POST", URL)[0].body).toEqual({ text: "não quero dano, quero aguentar" });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Usar este objetivo" }));
    expect(onChange).toHaveBeenCalledWith({ priority: "SURVIVAL", parserVersion: "rules_v1" });
    expect(screen.getByLabelText(/Sobrevivência/)).toBeChecked();
    expect(screen.getByText(/de texto confirmado \(interpretador rules_v1\)/)).toBeInTheDocument();
  });

  it("recusar a proposta mantém o objetivo anterior", async () => {
    mock.on("POST", URL, () =>
      json(200, {
        status: "INTERPRETED",
        objective: { priority: "DAMAGE", parserVersion: "rules_v1" },
        parserVersion: "rules_v1",
        provider: "RULES",
        reasonCodes: [],
      }),
    );
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    typeAndInterpret("quero dano");
    fireEvent.click(await screen.findByRole("button", { name: /Prefiro escolher no formulário/ }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Equilibrado/)).toBeChecked();
  });

  it("NEEDS_INPUT abre o formulário com o motivo e não muda o objetivo", async () => {
    mock.on("POST", URL, () =>
      json(200, {
        status: "NEEDS_INPUT",
        objective: null,
        parserVersion: "rules_v1",
        provider: "RULES",
        reasonCodes: ["AMBIGUOUS_OR_OUT_OF_SCOPE"],
      }),
    );
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    typeAndInterpret("quero dano e vida");
    await screen.findByText(/mais de uma prioridade sem dizer qual vem primeiro/);
    expect(screen.getByText(/o objetivo selecionado não mudou/)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText("Objetivo")).toHaveFocus());
  });

  it("timeout do servidor e falha de rede também caem no formulário", async () => {
    mock.on("POST", URL, () =>
      json(200, {
        status: "NEEDS_INPUT",
        objective: null,
        parserVersion: "lora_v1",
        provider: "LOCAL_MODEL",
        reasonCodes: ["TIMEOUT"],
      }),
    );
    render(<Harness onChange={vi.fn()} />);
    typeAndInterpret("quero mana");
    await screen.findByText(/A interpretação demorou demais/);

    mock.on("POST", URL, () => problem(500, "INTERNAL_ERROR"));
    typeAndInterpret("quero mana de novo");
    await screen.findByText(/demorou demais|não respondeu/);
    expect(screen.getByLabelText(/Equilibrado/)).toBeChecked();
  });

  it("limita o texto a 500 caracteres e escolher no formulário apaga a origem textual", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    expect(screen.getByLabelText(/descreva em uma frase/)).toHaveAttribute("maxLength", "500");
    expect(screen.getByRole("button", { name: "Interpretar texto" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/Utilidade/));
    expect(onChange).toHaveBeenCalledWith({ priority: "UTILITY" });
  });
});
