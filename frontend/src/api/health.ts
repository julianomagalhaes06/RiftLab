export type HealthState =
  | { kind: "loading" }
  | { kind: "available" }
  | { kind: "degraded"; detail: string }
  | { kind: "unavailable"; detail: string };

interface ProblemDetail {
  detail?: string;
}

async function isOk(fetchImpl: typeof fetch, path: string): Promise<boolean> {
  try {
    const response = await fetchImpl(path);
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Consulta /health/live e, se a API responder, /health/ready. Nunca lanca: toda
 * falha de rede vira um HealthState explicito para a UI distinguir API fora do
 * ar de banco indisponivel (erro recuperavel).
 */
export async function fetchHealthState(fetchImpl: typeof fetch = fetch): Promise<HealthState> {
  const liveOk = await isOk(fetchImpl, "/health/live");

  if (!liveOk) {
    return { kind: "unavailable", detail: "A API do RiftLab nao respondeu." };
  }

  try {
    const readyResponse = await fetchImpl("/health/ready");
    if (readyResponse.ok) {
      return { kind: "available" };
    }
    const problem = (await readyResponse.json().catch(() => null)) as ProblemDetail | null;
    return {
      kind: "degraded",
      detail: problem?.detail ?? "Banco de dados indisponivel no momento.",
    };
  } catch {
    return {
      kind: "degraded",
      detail: "Nao foi possivel confirmar a conexao com o banco de dados.",
    };
  }
}
