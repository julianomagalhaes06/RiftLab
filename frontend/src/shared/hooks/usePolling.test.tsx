import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePolling } from "./usePolling";

interface Job {
  status: "RUNNING" | "DONE";
}

describe("usePolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const isTerminal = (job: Job) => job.status === "DONE";

  it("expõe o dado, para no estado terminal e não consulta mais depois", async () => {
    const fetcher = vi
      .fn<(signal: AbortSignal) => Promise<Job>>()
      .mockResolvedValueOnce({ status: "RUNNING" })
      .mockResolvedValueOnce({ status: "DONE" });
    const { result } = renderHook(() => usePolling<Job>({ resetKey: "a", fetcher, isTerminal }));

    expect(result.current.phase).toBe("polling");
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.data).toEqual({ status: "RUNNING" });
    await act(() => vi.advanceTimersByTimeAsync(1_000));

    expect(result.current).toMatchObject({ phase: "done", data: { status: "DONE" }, attempts: 2 });
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("cancela a requisição em voo e o timer ao desmontar (AbortController)", async () => {
    const signals: AbortSignal[] = [];
    const fetcher = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return Promise.resolve<Job>({ status: "RUNNING" });
    });
    const { unmount } = renderHook(() => usePolling<Job>({ resetKey: "a", fetcher, isTerminal }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(fetcher).toHaveBeenCalledTimes(2);

    unmount();

    expect(signals[signals.length - 1].aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("trocar o resetKey reinicia do zero e nunca mostra o dado do alvo anterior", async () => {
    const fetcher = vi.fn(async () => ({ status: "RUNNING" }) as Job);
    const { result, rerender } = renderHook(
      ({ id }) => usePolling<Job>({ resetKey: id, fetcher, isTerminal }),
      { initialProps: { id: "a" } },
    );
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.attempts).toBe(1);

    rerender({ id: "b" });

    expect(result.current).toMatchObject({ data: null, attempts: 0, phase: "polling" });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.attempts).toBe(1);
  });

  it("enabled=false não consulta", async () => {
    const fetcher = vi.fn(async () => ({ status: "RUNNING" }) as Job);
    renderHook(() => usePolling<Job>({ resetKey: "a", fetcher, isTerminal, enabled: false }));

    await vi.advanceTimersByTimeAsync(10_000);

    expect(fetcher).not.toHaveBeenCalled();
  });

  it("restart() volta a consultar depois de um estado terminal", async () => {
    let status: Job["status"] = "DONE";
    const fetcher = vi.fn(async () => ({ status }) as Job);
    const { result } = renderHook(() => usePolling<Job>({ resetKey: "a", fetcher, isTerminal }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.phase).toBe("done");

    status = "RUNNING";
    act(() => result.current.restart());
    await act(() => vi.advanceTimersByTimeAsync(0));

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current).toMatchObject({ phase: "polling", data: { status: "RUNNING" } });
  });
});
