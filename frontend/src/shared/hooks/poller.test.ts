import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import { backoffDelay, nextDelay, startPoller, type PollerState } from "./poller";

interface Job {
  status: "RUNNING" | "DONE";
  retryAt?: string | null;
}

const cfg = { initialDelayMs: 1_000, maxDelayMs: 8_000, factor: 2, retryAtPollCeilingMs: 60_000 };

describe("backoffDelay / nextDelay", () => {
  it("cresce exponencialmente e respeita o teto", () => {
    expect([0, 1, 2, 3, 4, 10].map((n) => backoffDelay(n, 1_000, 2, 8_000))).toEqual([
      1_000, 2_000, 4_000, 8_000, 8_000, 8_000,
    ]);
  });

  it("sem retryAt (ou já vencido) usa só o backoff", () => {
    expect(nextDelay(1, null, 0, cfg)).toBe(2_000);
    expect(nextDelay(1, "1970-01-01T00:00:00Z", 10_000, cfg)).toBe(2_000);
    expect(nextDelay(1, "lixo", 0, cfg)).toBe(2_000);
  });

  it("nunca consulta antes do retryAt (mais 250 ms), limitado pelo teto de consulta", () => {
    const now = Date.parse("2026-09-19T12:00:00Z");
    expect(nextDelay(0, "2026-09-19T12:00:05Z", now, cfg)).toBe(5_250);
    expect(nextDelay(0, "2026-09-19T12:15:00Z", now, cfg)).toBe(60_000);
  });
});

describe("startPoller", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-19T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function run(
    fetcher: (signal: AbortSignal) => Promise<Job>,
    extra: Partial<Parameters<typeof startPoller<Job>>[0]> = {},
  ) {
    const states: PollerState<Job>[] = [];
    const poller = startPoller<Job>({
      fetcher,
      isTerminal: (job) => job.status === "DONE",
      getRetryAt: (job) => job.retryAt,
      onUpdate: (state) => states.push(state),
      ...cfg,
      ...extra,
    });
    return { poller, states, last: () => states[states.length - 1] };
  }

  it("busca já na partida, faz backoff entre consultas e para no estado terminal", async () => {
    const fetcher = vi
      .fn<(signal: AbortSignal) => Promise<Job>>()
      .mockResolvedValueOnce({ status: "RUNNING" })
      .mockResolvedValueOnce({ status: "RUNNING" })
      .mockResolvedValueOnce({ status: "DONE" });
    const { last } = run(fetcher);

    await vi.advanceTimersByTimeAsync(0);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(last().nextPollAt).toBe(Date.now() + 1_000);

    await vi.advanceTimersByTimeAsync(999);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetcher).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(2_000); // segundo intervalo = 2 s
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(last()).toMatchObject({ phase: "done", attempts: 3, nextPollAt: null });

    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetcher).toHaveBeenCalledTimes(3); // nenhuma consulta depois do estado terminal
  });

  it("respeita retryAt: não consulta antes dele", async () => {
    const retryAt = new Date(Date.now() + 20_000).toISOString();
    const fetcher = vi
      .fn<(signal: AbortSignal) => Promise<Job>>()
      .mockResolvedValueOnce({ status: "RUNNING", retryAt })
      .mockResolvedValueOnce({ status: "DONE" });
    run(fetcher);

    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(19_999);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(251);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("stop() cancela a requisição em voo (AbortSignal) e o timer agendado", async () => {
    let seen: AbortSignal | undefined;
    const fetcher = vi.fn((signal: AbortSignal) => {
      seen = signal;
      return new Promise<Job>(() => {});
    });
    const { poller, states } = run(fetcher);
    await vi.advanceTimersByTimeAsync(0);

    poller.stop();

    expect(seen?.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(states).toHaveLength(0);
  });

  it("stop() com timer agendado impede novas consultas e novas atualizações", async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: "RUNNING" });
    const { poller, states } = run(fetcher);
    await vi.advanceTimersByTimeAsync(0);
    const updates = states.length;

    poller.stop();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(states).toHaveLength(updates);
  });

  it("resposta que chega depois do stop() é ignorada", async () => {
    let resolve: (job: Job) => void = () => {};
    const fetcher = vi.fn(
      () =>
        new Promise<Job>((r) => {
          resolve = r;
        }),
    );
    const { poller, states } = run(fetcher);
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();

    resolve({ status: "DONE" });
    await vi.advanceTimersByTimeAsync(0);

    expect(states).toHaveLength(0);
  });

  it("erro definitivo (4xx) para na hora com fase failed", async () => {
    const fetcher = vi.fn().mockRejectedValue(new ApiError(404, "RESOURCE_NOT_FOUND"));
    const { last } = run(fetcher);

    await vi.advanceTimersByTimeAsync(0);

    expect(last()).toMatchObject({ phase: "failed", nextPollAt: null });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("erro transitório (rede/5xx) tenta de novo com backoff e se recupera", async () => {
    const fetcher = vi
      .fn<(signal: AbortSignal) => Promise<Job>>()
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR"))
      .mockRejectedValueOnce(new ApiError(503, "DATABASE_UNAVAILABLE"))
      .mockResolvedValueOnce({ status: "DONE" });
    const { last } = run(fetcher);

    await vi.advanceTimersByTimeAsync(0);
    expect(last()).toMatchObject({ phase: "polling", error: expect.any(ApiError) });
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.advanceTimersByTimeAsync(2_000);

    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(last()).toMatchObject({ phase: "done", error: null });
  });

  it("erros transitórios seguidos esgotam maxConsecutiveErrors e viram failed", async () => {
    const fetcher = vi.fn().mockRejectedValue(new ApiError(0, "NETWORK_ERROR"));
    const { last } = run(fetcher, { maxConsecutiveErrors: 3 });

    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.advanceTimersByTimeAsync(2_000);

    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(last().phase).toBe("failed");
  });

  it("teto de duração: para com fase stalled em vez de consultar para sempre", async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: "RUNNING" });
    const { last } = run(fetcher, { maxDurationMs: 20_000 });

    await vi.advanceTimersByTimeAsync(120_000);

    expect(last()).toMatchObject({ phase: "stalled", nextPollAt: null });
    const calls = fetcher.mock.calls.length;
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetcher).toHaveBeenCalledTimes(calls);
  });
});
