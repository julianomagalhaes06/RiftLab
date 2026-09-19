import type { components } from "./types";

type ProblemDetail = components["schemas"]["ProblemDetail"];

/** Código sintético: a requisição nem chegou a uma resposta HTTP (rede, CORS, API fora do ar). */
export const NETWORK_ERROR = "NETWORK_ERROR";
/** Código sintético: a API respondeu 2xx, mas o corpo não é o JSON esperado. */
export const INVALID_RESPONSE = "INVALID_RESPONSE";

const CODE_PATTERN = /^[A-Z0-9_]{1,64}$/;
const REQUEST_ID_PATTERN = /^[\w-]{1,64}$/;

/**
 * Erro tipado de uma chamada à API do RiftLab. Guarda só {status, code, requestId}:
 * nunca o `detail` livre nem o corpo da resposta (a interface mostra o texto da
 * tabela única de mensagens, ver messages.ts). `status` 0 = sem resposta HTTP.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(status: number, code: string, requestId: string | null = null) {
    super(`${status} ${code}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Cancelamento por AbortController: não é falha e nunca deve virar mensagem ao usuário. */
export function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

function asProblem(value: unknown): Partial<ProblemDetail> | null {
  return typeof value === "object" && value !== null ? (value as Partial<ProblemDetail>) : null;
}

/**
 * Converte uma resposta de erro (application/problem+json) em ApiError. Corpo que
 * não é um problem+json válido cai em `HTTP_<status>`; `code` fora do formato
 * MAIÚSCULAS_COM_UNDERSCORE também (nada vindo do servidor é repassado como texto).
 */
export async function parseProblem(response: Response): Promise<ApiError> {
  const problem = await readProblem(response);
  const code =
    typeof problem?.code === "string" && CODE_PATTERN.test(problem.code)
      ? problem.code
      : `HTTP_${response.status}`;
  const requestId =
    typeof problem?.requestId === "string" && REQUEST_ID_PATTERN.test(problem.requestId)
      ? problem.requestId
      : null;
  return new ApiError(response.status, code, requestId);
}

async function readProblem(response: Response): Promise<Partial<ProblemDetail> | null> {
  try {
    return asProblem(JSON.parse(await response.text()));
  } catch {
    return null;
  }
}
