"use client";

import { useMemo, useState } from "react";
import styles from "../ModelWalkthrough.module.css";
import { fmtNum, fmtSigned, normPdf, svgCoord } from "../../../lib/visual/math";
import { CONFORMAL_WIDEN, INCOHERENT, Q10, Q90 } from "../../../lib/visual/example";
import { exampleForecast } from "./UncertaintyFigure";
import { AXIS, Segmented, linear, useWidth } from "./shared";

type Mode = "heads" | "calibrated" | "incoherent";

const LO = -42;
const HI = 50;

const NOTES: Record<Mode, string> = {
  heads:
    "Two quantile models predict the 10th and 90th percentile of the margin directly. On their own they tend to be a little too narrow.",
  calibrated:
    "Both ends are pushed out by the same amount, learned from how often similar ranges missed over the last two seasons. This is the range published on This Week.",
  incoherent:
    "Here the low end came out above the forecast itself, which can't describe a sensible range. The gate withholds it: the row shows the forecast with no range, rather than a wrong one.",
};

export default function RangeFigure() {
  const f = useMemo(exampleForecast, []);
  const [mode, setMode] = useState<Mode>("calibrated");
  const { ref, width } = useWidth();
  const h = 230;
  const pad = { l: 8, r: 8, t: 34, b: 38 };
  const x = linear(LO, HI, pad.l, Math.max(width - pad.r, pad.l + 1));
  const peak = normPdf(f.mu, f.mu, f.sigma);
  const y = linear(0, peak * 1.12, h - pad.b, pad.t);

  const q10 = mode === "incoherent" ? INCOHERENT.q10 : Q10;
  const q90 = mode === "incoherent" ? INCOHERENT.q90 : Q90;
  const lo = mode === "calibrated" ? Q10 - CONFORMAL_WIDEN : q10;
  const hi = mode === "calibrated" ? Q90 + CONFORMAL_WIDEN : q90;
  const withheld = mode === "incoherent";

  const curve: string[] = [];
  const area: string[] = [];
  const n = 180;
  for (let i = 0; i <= n; i += 1) {
    const v = LO + ((HI - LO) * i) / n;
    const pt = `${svgCoord(x(v))},${svgCoord(y(normPdf(v, f.mu, f.sigma)))}`;
    curve.push(`${i === 0 ? "M" : "L"}${pt}`);
    if (v >= lo && v <= hi) area.push(pt);
  }
  const bandPath = area.length
    ? `M${x(lo)},${y(0)} L${x(lo)},${y(normPdf(lo, f.mu, f.sigma))} L${area.join(" L")} L${x(hi)},${y(normPdf(hi, f.mu, f.sigma))} L${x(hi)},${y(0)} Z`
    : "";

  const ticks = [-40, -20, 0, 20, 40];
  const published = withheld
    ? `${fmtSigned(f.mu)}`
    : `${fmtSigned(f.mu)} [${fmtSigned(lo)}, ${fmtSigned(hi)}]`;

  return (
    <div>
      <div ref={ref} className={styles.chart} style={{ minHeight: h }}>
        {width > 0 && (
          <svg
            width={width}
            height={h}
            role="img"
            aria-label={
              withheld
                ? "Range withheld by the coherence gate."
                : `Range from ${fmtSigned(lo)} to ${fmtSigned(hi)}.`
            }
          >
            {bandPath && (
              <path
                d={bandPath}
                fill={
                  withheld
                    ? "none"
                    : mode === "calibrated"
                      ? "var(--accent)"
                      : "var(--bg-secondary)"
                }
                fillOpacity={mode === "calibrated" ? 0.14 : 1}
                stroke={withheld ? "var(--text-tertiary)" : "none"}
                strokeDasharray={withheld ? "4 4" : undefined}
              />
            )}
            {mode === "calibrated" &&
              [Q10, Q90].map((q) => (
                <line
                  key={q}
                  x1={x(q)}
                  x2={x(q)}
                  y1={y(0)}
                  y2={y(normPdf(q, f.mu, f.sigma)) - 4}
                  stroke="var(--text-tertiary)"
                  strokeDasharray="3 3"
                />
              ))}
            <path d={curve.join("")} fill="none" stroke="var(--text-primary)" strokeWidth={1.75} />
            {[lo, hi].map((v, i) => (
              <g key={`b${i}`}>
                <line
                  x1={x(v)}
                  x2={x(v)}
                  y1={y(0)}
                  y2={pad.t - 4}
                  stroke={
                    withheld
                      ? "var(--text-tertiary)"
                      : mode === "calibrated"
                        ? "var(--accent)"
                        : "var(--text-secondary)"
                  }
                  strokeWidth={1.5}
                />
                <text
                  x={x(v)}
                  y={pad.t - 10}
                  textAnchor={i === 0 ? "end" : "start"}
                  fontSize={AXIS.label}
                  fontWeight={500}
                  fill={
                    withheld
                      ? "var(--text-tertiary)"
                      : mode === "calibrated"
                        ? "var(--accent)"
                        : "var(--text-secondary)"
                  }
                >
                  {mode === "calibrated"
                    ? fmtSigned(v)
                    : `${i === 0 ? "q10" : "q90"} ${fmtSigned(v)}`}
                </text>
              </g>
            ))}
            <line
              x1={x(f.mu)}
              x2={x(f.mu)}
              y1={y(0)}
              y2={y(peak) - 2}
              stroke={withheld ? "var(--semantic-stale)" : "var(--text-primary)"}
              strokeWidth={withheld ? 2 : 1}
            />
            <text
              x={x(f.mu)}
              y={y(peak) - 8}
              textAnchor={withheld ? "end" : "middle"}
              fontSize={AXIS.font}
              fill={withheld ? "var(--semantic-stale)" : "var(--text-primary)"}
            >
              μ {fmtSigned(f.mu)}
            </text>
            {withheld && (
              <text
                x={x((q10 + q90) / 2)}
                y={y(peak * 0.35)}
                textAnchor="middle"
                fontSize={AXIS.label}
                fontWeight={600}
                fill="var(--text-secondary)"
              >
                Withheld
              </text>
            )}
            <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} stroke="var(--text-tertiary)" />
            {ticks.map((t) => (
              <text
                key={t}
                x={x(t)}
                y={y(0) + 16}
                textAnchor="middle"
                fontSize={AXIS.font}
                fill="var(--text-secondary)"
              >
                {t > 0 ? `+${t}` : t < 0 ? `\u2212${-t}` : "0"}
              </text>
            ))}
            <text
              x={width / 2}
              y={h - 4}
              textAnchor="middle"
              fontSize={AXIS.font}
              fill="var(--text-secondary)"
            >
              Final margin, home minus away (points)
            </text>
          </svg>
        )}
      </div>
      <div className={styles.controls}>
        <Segmented<Mode>
          label="Range construction"
          value={mode}
          onChange={setMode}
          options={[
            { value: "heads", label: "Quantile models" },
            { value: "calibrated", label: "Calibrated range" },
            { value: "incoherent", label: "When it fails" },
          ]}
        />
      </div>
      <dl className={styles.readout}>
        <div>
          <dt>Shown on This Week</dt>
          <dd>{mode === "heads" ? "Not yet published" : published}</dd>
        </div>
        <div>
          <dt>Target coverage</dt>
          <dd>80%</dd>
        </div>
        <div>
          <dt>Widening</dt>
          <dd>{mode === "calibrated" ? `±${fmtNum(CONFORMAL_WIDEN)} points` : "\u2014"}</dd>
        </div>
      </dl>
      <p className={styles.note}>{NOTES[mode]}</p>
    </div>
  );
}
