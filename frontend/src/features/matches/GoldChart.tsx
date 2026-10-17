import { useEffect, useId, useMemo, useRef, useState, type PointerEvent } from "react";
import type { MatchParticipant, MatchTimeline, TimelineObjective } from "../../shared/api/client";
import { BLUE, compactGold, goldDiffSeries, type GoldPoint } from "./matchView";

const PAD = { top: 14, right: 12, bottom: 58, left: 56 };
const MARKER_GAP = 13;

/** Largura real do contêiner: o SVG é desenhado em pixels, sem escalar texto. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.round(entry.contentRect.width))));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}
const MARKED: Partial<Record<TimelineObjective["kind"], string>> = {
  DRAGON: "Dragão",
  BARON: "Barão",
  HERALD: "Arauto",
  ATAKHAN: "Atakhan",
  VOIDGRUB: "Larvas",
};

function niceMax(value: number): number {
  const steps = [1000, 2000, 2500, 5000, 7500, 10000, 15000, 20000, 30000];
  return steps.find((step) => step >= value) ?? Math.ceil(value / 10000) * 10000;
}

function leaderText(point: GoldPoint): string {
  if (point.diff === 0) return "ouro empatado";
  return `${point.diff > 0 ? "time azul" : "time vermelho"} à frente por ${compactGold(Math.abs(point.diff)).replace("+", "")}`;
}

/** Diferença de ouro azul − vermelho por minuto, com objetivos marcados no eixo. */
export function GoldChart({ timeline, participants }: { timeline: MatchTimeline; participants: MatchParticipant[] }) {
  const id = useId().replace(/:/g, "");
  const svgRef = useRef<SVGSVGElement>(null);
  const [plotRef, WIDTH] = useWidth();
  const HEIGHT = WIDTH < 560 ? 210 : 250;
  const PLOT_W = WIDTH - PAD.left - PAD.right;
  const PLOT_H = HEIGHT - PAD.top - PAD.bottom;
  const [hover, setHover] = useState<number | null>(null);
  const series = useMemo(() => goldDiffSeries(timeline, participants), [timeline, participants]);

  if (timeline.goldFrames == null) {
    return <p className="muted">O gráfico de ouro aparece depois que a partida for preparada de novo.</p>;
  }
  if (series.length < 2) {
    return <p className="muted">A partida não tem quadros de ouro suficientes para o gráfico.</p>;
  }

  const lastMinute = series.at(-1)!.minute;
  const yMax = niceMax(Math.max(1000, ...series.map((p) => Math.abs(p.diff))));
  const x = (minute: number) => PAD.left + (minute / lastMinute) * PLOT_W;
  const y = (gold: number) => PAD.top + PLOT_H / 2 - (gold / yMax) * (PLOT_H / 2);
  const zero = y(0);
  const line = series.map((p, i) => `${i ? "L" : "M"}${x(p.minute).toFixed(1)},${y(p.diff).toFixed(1)}`).join(" ");
  const area = `${line} L${x(lastMinute).toFixed(1)},${zero} L${x(0).toFixed(1)},${zero} Z`;
  const step = WIDTH < 560 ? 10 : 5;
  const ticks = Array.from({ length: Math.floor(lastMinute / step) + 1 }, (_, i) => i * step);
  const lastX: number[] = [];
  const markers = timeline.objectives
    .filter((o) => MARKED[o.kind] && o.team != null && o.timestampMs / 60_000 <= lastMinute)
    .map((objective) => {
      const cx = x(objective.timestampMs / 60_000);
      let row = 0;
      while (lastX[row] !== undefined && cx - lastX[row] < MARKER_GAP) row += 1;
      lastX[row] = cx;
      return { objective, cx, cy: PAD.top + PLOT_H + 30 + Math.min(row, 2) * 11 };
    });
  const peakBlue = series.reduce((best, p) => (p.diff > best.diff ? p : best), series[0]);
  const peakRed = series.reduce((best, p) => (p.diff < best.diff ? p : best), series[0]);
  const final = series.at(-1)!;
  const active = hover !== null ? series[hover] : null;

  function onMove(event: PointerEvent<SVGSVGElement>) {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return;
    const minute = (((event.clientX - box.left) / box.width) * WIDTH - PAD.left) / PLOT_W * lastMinute;
    let nearest = 0;
    series.forEach((p, i) => {
      if (Math.abs(p.minute - minute) < Math.abs(series[nearest].minute - minute)) nearest = i;
    });
    setHover(nearest);
  }

  return (
    <figure className="gold-chart">
      <figcaption className="gold-chart__caption">
        <span>Diferença de ouro entre os times</span>
        <span className="gold-chart__legend">
          <span><i className="gold-chart__key gold-chart__key--blue" aria-hidden="true" />Time azul à frente</span>
          <span><i className="gold-chart__key gold-chart__key--red" aria-hidden="true" />Time vermelho à frente</span>
        </span>
      </figcaption>
      <p className="sr-only">
        Maior vantagem do time azul: {compactGold(peakBlue.diff)} aos {Math.round(peakBlue.minute)} min. Maior vantagem do
        time vermelho: {compactGold(-peakRed.diff)} aos {Math.round(peakRed.minute)} min. No fim, {leaderText(final)}.
      </p>
      <div className="gold-chart__plot" ref={plotRef}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          width={WIDTH}
          height={HEIGHT}
          role="img"
          aria-label="Gráfico da diferença de ouro por minuto"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        >
          <defs>
            <clipPath id={`${id}-above`}><rect x={0} y={0} width={WIDTH} height={zero} /></clipPath>
            <clipPath id={`${id}-below`}><rect x={0} y={zero} width={WIDTH} height={HEIGHT - zero} /></clipPath>
          </defs>
          {[yMax, yMax / 2, -yMax / 2, -yMax].map((value) => (
            <g key={value}>
              <line className="gold-chart__grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={y(value)} y2={y(value)} />
              <text className="gold-chart__tick" x={PAD.left - 8} y={y(value) + 4} textAnchor="end">{compactGold(value)}</text>
            </g>
          ))}
          {ticks.map((minute) => (
            <text key={minute} className="gold-chart__tick" x={x(minute)} y={PAD.top + PLOT_H + 16} textAnchor="middle">{minute}′</text>
          ))}
          <path d={area} className="gold-chart__area gold-chart__area--blue" clipPath={`url(#${id}-above)`} />
          <path d={area} className="gold-chart__area gold-chart__area--red" clipPath={`url(#${id}-below)`} />
          <path d={line} className="gold-chart__line gold-chart__line--blue" clipPath={`url(#${id}-above)`} />
          <path d={line} className="gold-chart__line gold-chart__line--red" clipPath={`url(#${id}-below)`} />
          <line className="gold-chart__zero" x1={PAD.left} x2={WIDTH - PAD.right} y1={zero} y2={zero} />
          {markers.map(({ objective, cx, cy }, index) => (
            <circle
              key={`${objective.timestampMs}-${index}`}
              className={`gold-chart__marker gold-chart__marker--${objective.team === BLUE ? "blue" : "red"}`}
              cx={cx}
              cy={cy}
              r={5}
            >
              <title>{`${MARKED[objective.kind]} do time ${objective.team === BLUE ? "azul" : "vermelho"} aos ${Math.floor(objective.timestampMs / 60_000)} min`}</title>
            </circle>
          ))}
          {active ? (
            <g pointerEvents="none">
              <line className="gold-chart__crosshair" x1={x(active.minute)} x2={x(active.minute)} y1={PAD.top} y2={PAD.top + PLOT_H} />
              <circle className={`gold-chart__dot gold-chart__dot--${active.diff >= 0 ? "blue" : "red"}`} cx={x(active.minute)} cy={y(active.diff)} r={5} />
            </g>
          ) : null}
        </svg>
        {active ? (
          <div
            className="gold-chart__tooltip"
            style={{ left: `${Math.min(Math.max(x(active.minute), 90), WIDTH - 90)}px` }}
            role="status"
          >
            <strong>{compactGold(active.diff)}</strong>
            <span>{Math.round(active.minute)} min · {leaderText(active)}</span>
            <span><i className="gold-chart__key gold-chart__key--blue" aria-hidden="true" />Azul {active.blue.toLocaleString("pt-BR")}</span>
            <span><i className="gold-chart__key gold-chart__key--red" aria-hidden="true" />Vermelho {active.red.toLocaleString("pt-BR")}</span>
          </div>
        ) : null}
      </div>
      <p className="gold-chart__note">Pontos abaixo do eixo: dragões, arautos, larvas, barões e Atakhan, na cor do time que conquistou.</p>
      <details className="gold-chart__table">
        <summary>Ver em tabela</summary>
        <table>
          <thead><tr><th scope="col">Minuto</th><th scope="col">Time azul</th><th scope="col">Time vermelho</th><th scope="col">Diferença</th></tr></thead>
          <tbody>
            {series.map((point) => (
              <tr key={point.minute}>
                <td>{Math.round(point.minute)}</td>
                <td>{point.blue.toLocaleString("pt-BR")}</td>
                <td>{point.red.toLocaleString("pt-BR")}</td>
                <td>{compactGold(point.diff)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
