"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import styles from "../ModelWalkthrough.module.css";
import {
  fmtNum,
  fmtPct,
  fmtSigned,
  marginPmf,
  mulberry32,
  samplerFor,
  winShare,
} from "../../../lib/visual/math";
import { HOME, KEY_WEIGHTS, MEMBERS, RATING_VAR, SIGMA_HEAD } from "../../../lib/visual/example";
import { AXIS, Segmented, linear, usePrefersReducedMotion, useWidth } from "./shared";

const LO = -38;
const HI = 46;
const N_SIMS = 2000;
const BATCH = 50;

export function exampleForecast() {
  const mu = MEMBERS.reduce((a, m) => a + m.weight * m.mu, 0);
  const memberVar = MEMBERS.reduce((a, m) => a + m.weight * (m.mu - mu) ** 2, 0);
  const headVar = SIGMA_HEAD ** 2;
  const sigma = Math.sqrt(headVar + RATING_VAR + memberVar);
  return { mu, sigma, headVar, memberVar, ratingVar: RATING_VAR };
}

type Shape = "smooth" | "key";

export default function UncertaintyFigure() {
  const f = useMemo(exampleForecast, []);
  const [shape, setShape] = useState<Shape>("key");
  const pmf = useMemo(
    () => marginPmf(f.mu, f.sigma, shape === "key" ? KEY_WEIGHTS : null),
    [f, shape],
  );
  const pWin = winShare(pmf);
  const [counts, setCounts] = useState<Map<number, number> | null>(null);
  const [drawn, setDrawn] = useState(0);
  const [homeWins, setHomeWins] = useState(0);
  const [ties, setTies] = useState(0);
  const raf = useRef<number | null>(null);
  const reduced = usePrefersReducedMotion();
  const { ref, width } = useWidth();

  useEffect(
    () => () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
    },
    [],
  );

  const reset = () => {
    if (raf.current !== null) cancelAnimationFrame(raf.current);
    setCounts(null);
    setDrawn(0);
    setHomeWins(0);
    setTies(0);
  };

  const simulate = () => {
    reset();
    const sample = samplerFor(pmf);
    const rand = mulberry32(42);
    const map = new Map<number, number>();
    let n = 0;
    let wins = 0;
    let tieCount = 0;
    const stepOnce = (k: number) => {
      for (let i = 0; i < k && n < N_SIMS; i += 1) {
        const m = sample(rand());
        map.set(m, (map.get(m) ?? 0) + 1);
        if (m > 0) wins += 1;
        if (m === 0) tieCount += 1;
        n += 1;
      }
      setCounts(new Map(map));
      setDrawn(n);
      setHomeWins(wins);
      setTies(tieCount);
    };
    if (reduced) {
      stepOnce(N_SIMS);
      return;
    }
    const frame = () => {
      stepOnce(BATCH);
      if (n < N_SIMS) raf.current = requestAnimationFrame(frame);
    };
    raf.current = requestAnimationFrame(frame);
  };

  const h = 220;
  const pad = { l: 8, r: 8, t: 18, b: 40 };
  const x = linear(LO - 0.5, HI + 0.5, pad.l, Math.max(width - pad.r, pad.l + 1));
  const bw = Math.max(1, (x(1) - x(0)) * 0.78);
  const maxP = Math.max(...pmf.ps) * 1.1;
  const y = linear(0, maxP, h - pad.b, pad.t);
  const scale = drawn > 0 ? 1 / drawn : 0;
  const ticks = width < 480 ? [-28, -14, 0, 7, 14, 28, 42] : [-28, -14, -7, 0, 3, 7, 14, 28, 42];

  const parts = [
    { label: "Game-to-game randomness (σ-head)", v: f.headVar, color: "var(--text-primary)" },
    { label: "Rating uncertainty", v: f.ratingVar, color: "var(--accent)" },
    { label: "Model disagreement", v: f.memberVar, color: "var(--text-tertiary)" },
  ];
  const totalVar = parts.reduce((a, p) => a + p.v, 0);

  return (
    <div>
      <div className={styles.weightBar} style={{ height: 10, marginBottom: 8 }} aria-hidden="true">
        {parts.map((p) => (
          <span
            key={p.label}
            style={{ width: `${(p.v / totalVar) * 100}%`, background: p.color }}
          />
        ))}
      </div>
      <ul className={styles.fieldList} style={{ marginTop: 0, marginBottom: 22 }}>
        {parts.map((p) => (
          <li key={p.label}>
            <span>
              <svg width="10" height="10" aria-hidden="true" style={{ marginRight: 6 }}>
                <rect width="10" height="10" rx="2" fill={p.color} />
              </svg>
              {p.label}
            </span>
            <span className={styles.num}>
              {fmtNum(p.v)} pts² ({fmtPct(p.v / totalVar)})
            </span>
          </li>
        ))}
        <li>
          <span>
            <code>sigma_margin</code>
          </span>
          <span className={styles.num}>
            σ = √{fmtNum(totalVar)} = {fmtNum(f.sigma)} points
          </span>
        </li>
      </ul>

      <div ref={ref} className={styles.chart} style={{ minHeight: h }}>
        {width > 0 && (
          <svg
            width={width}
            height={h}
            role="img"
            aria-label={`Distribution of final margins centered at ${fmtSigned(f.mu)} and uncertainty σ ${fmtNum(f.sigma)}. ${HOME} wins ${fmtPct(pWin)}.`}
          >
            <rect
              x={x(0.5)}
              y={pad.t - 6}
              width={x(HI + 0.5) - x(0.5)}
              height={h - pad.b - pad.t + 6}
              fill="var(--bg-secondary)"
              opacity={0.6}
            />
            {pmf.ks.map((k, i) => {
              if (k < LO || k > HI) return null;
              const p = pmf.ps[i];
              const sim = counts ? (counts.get(k) ?? 0) * scale : null;
              const key = k === 3 || k === 7 || k === -3 || k === -7;
              return (
                <g key={k}>
                  {sim === null ? (
                    <rect
                      x={x(k) - bw / 2}
                      y={y(p)}
                      width={bw}
                      height={y(0) - y(p)}
                      fill={shape === "key" && key ? "var(--accent)" : "var(--text-secondary)"}
                      opacity={shape === "key" && key ? 1 : 0.55}
                    />
                  ) : (
                    <>
                      <rect
                        x={x(k) - bw / 2}
                        y={y(Math.min(sim, maxP))}
                        width={bw}
                        height={y(0) - y(Math.min(sim, maxP))}
                        fill={k > 0 ? "var(--text-primary)" : "var(--text-tertiary)"}
                      />
                      <line
                        x1={x(k) - bw / 2}
                        x2={x(k) + bw / 2}
                        y1={y(p)}
                        y2={y(p)}
                        stroke="var(--accent)"
                        strokeWidth={1.5}
                      />
                    </>
                  )}
                </g>
              );
            })}
            <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} stroke="var(--text-tertiary)" />
            <line
              x1={x(f.mu)}
              x2={x(f.mu)}
              y1={y(0)}
              y2={pad.t - 10}
              stroke="var(--text-primary)"
              strokeDasharray="3 3"
            />
            <text x={x(f.mu) + 5} y={pad.t - 2} fontSize={AXIS.font} fill="var(--text-primary)">
              μ {fmtSigned(f.mu)}
            </text>
            {ticks.map((t) => (
              <text
                key={t}
                x={x(t)}
                y={y(0) + 15}
                textAnchor="middle"
                fontSize={AXIS.font}
                fontWeight={t === 3 || t === 7 ? 600 : 400}
                fill={t === 3 || t === 7 ? "var(--text-primary)" : "var(--text-secondary)"}
              >
                {t > 0 ? `+${t}` : t < 0 ? `\u2212${-t}` : "0"}
              </text>
            ))}
            <text x={x(1)} y={h - 4} fontSize={AXIS.font} fill="var(--text-secondary)">
              {HOME} wins by
            </text>
            <text
              x={x(-1)}
              y={h - 4}
              textAnchor="end"
              fontSize={AXIS.font}
              fill="var(--text-secondary)"
            >
              Away wins by
            </text>
          </svg>
        )}
      </div>
      <div className={styles.controls}>
        <Segmented<Shape>
          label="Margin shape"
          value={shape}
          onChange={(v) => {
            reset();
            setShape(v);
          }}
          options={[
            { value: "smooth", label: "Smooth bell curve" },
            { value: "key", label: "With key numbers" },
          ]}
        />
        <button
          type="button"
          className={`${styles.button} ${styles.buttonPrimary}`}
          onClick={simulate}
        >
          Simulate {N_SIMS.toLocaleString("en-US")} games
        </button>
      </div>
      <dl className={styles.readout} aria-live="polite">
        <div>
          <dt>{HOME} win chance</dt>
          <dd>{fmtPct(pWin)}</dd>
        </div>
        <div>
          <dt>Simulated so far</dt>
          <dd>{drawn.toLocaleString("en-US")}</dd>
        </div>
        <div>
          <dt>{HOME} won</dt>
          <dd>{drawn - ties > 0 ? fmtPct(homeWins / (drawn - ties)) : "\u2014"}</dd>
        </div>
      </dl>
      <p className={styles.note}>
        {drawn === 0
          ? "Each bar is the chance of one exact final margin. The shaded side is where the home team wins."
          : "The simulated share settles toward the exact win chance as more games are drawn. Production uses 100,000 draws per game."}
      </p>
    </div>
  );
}
