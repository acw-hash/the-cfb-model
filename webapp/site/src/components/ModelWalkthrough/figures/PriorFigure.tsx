"use client";

import styles from "../ModelWalkthrough.module.css";
import {
  KALMAN,
  fmtNum,
  fmtSigned,
  normPdf,
  priorVariance,
  svgCoord,
} from "../../../lib/visual/math";
import { PRIOR_MEAN, SEASON } from "../../../lib/visual/example";
import { AXIS, Slider, linear, useWidth } from "./shared";

type Props = {
  returning: number;
  missing: boolean;
  onReturning: (v: number) => void;
  onMissing: (v: boolean) => void;
};

const X0 = -0.42;
const X1 = 0.58;

function curvePath(mu: number, sd: number, x: (v: number) => number, y: (v: number) => number) {
  const pts: string[] = [];
  const n = 160;
  for (let i = 0; i <= n; i += 1) {
    const v = X0 + ((X1 - X0) * i) / n;
    pts.push(`${i === 0 ? "M" : "L"}${svgCoord(x(v))},${svgCoord(y(normPdf(v, mu, sd)))}`);
  }
  return pts.join("");
}

export default function PriorFigure({ returning, missing, onReturning, onMissing }: Props) {
  const { ref, width } = useWidth();
  const h = 230;
  const pad = { l: 8, r: 8, t: 22, b: 40 };
  const variance = priorVariance(returning, missing ? 1 : 0);
  const sd = Math.sqrt(variance);
  const refSd = Math.sqrt(priorVariance(0.95, 0));
  const peak = normPdf(PRIOR_MEAN, PRIOR_MEAN, refSd);
  const x = linear(X0, X1, pad.l, Math.max(width - pad.r, pad.l + 1));
  const y = linear(0, peak * 1.08, h - pad.b, pad.t);

  const w1 = SEASON[0];
  const p1 = variance + KALMAN.q;
  const r1 = KALMAN.rEpaBase ** 2 * (KALMAN.refSnaps / w1.snaps);
  const firstPull = p1 / (p1 + r1);

  const bandPts: string[] = [];
  const n = 60;
  for (let i = 0; i <= n; i += 1) {
    const v = PRIOR_MEAN - sd + (2 * sd * i) / n;
    bandPts.push(`${svgCoord(x(v))},${svgCoord(y(normPdf(v, PRIOR_MEAN, sd)))}`);
  }
  const band = `M${x(PRIOR_MEAN - sd)},${y(0)} L${bandPts.join(" L")} L${x(PRIOR_MEAN + sd)},${y(0)} Z`;

  const ticks = [-0.4, -0.2, 0, 0.2, 0.4];

  return (
    <div>
      <div ref={ref} className={styles.chart} style={{ minHeight: h }}>
        {width > 0 && (
          <svg
            width={width}
            height={h}
            role="img"
            aria-label={`Preseason rating centered at ${fmtSigned(PRIOR_MEAN, 2)} with uncertainty of plus or minus ${fmtNum(sd, 2)} per snap.`}
          >
            <path d={band} fill="var(--bg-secondary)" />
            <path
              d={curvePath(PRIOR_MEAN, refSd, x, y)}
              fill="none"
              stroke="var(--text-tertiary)"
              strokeDasharray="4 4"
            />
            <path
              d={curvePath(PRIOR_MEAN, sd, x, y)}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={2}
            />
            <line
              x1={x(PRIOR_MEAN)}
              x2={x(PRIOR_MEAN)}
              y1={y(0)}
              y2={pad.t - 6}
              stroke="var(--text-primary)"
            />
            <text
              x={x(PRIOR_MEAN) + 6}
              y={pad.t - 8}
              fontSize={AXIS.font}
              fill="var(--text-primary)"
            >
              Preseason view {fmtSigned(PRIOR_MEAN, 2)}
            </text>
            <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} stroke="var(--text-tertiary)" />
            {ticks.map((t) => (
              <g key={t}>
                <line x1={x(t)} x2={x(t)} y1={y(0)} y2={y(0) + 4} stroke="var(--text-tertiary)" />
                <text
                  x={x(t)}
                  y={y(0) + 17}
                  textAnchor="middle"
                  fontSize={AXIS.font}
                  fill="var(--text-secondary)"
                >
                  {t === 0 ? "League avg" : fmtSigned(t, 1)}
                </text>
              </g>
            ))}
            <text
              x={width / 2}
              y={h - 4}
              textAnchor="middle"
              fontSize={AXIS.font}
              fill="var(--text-secondary)"
            >
              Offensive rating, expected points added per snap vs league average
            </text>
            {width > 420 && (
              <text
                x={x(PRIOR_MEAN + refSd * 1.2) + 4}
                y={y(normPdf(PRIOR_MEAN + refSd * 1.2, PRIOR_MEAN, refSd)) - 4}
                fontSize={AXIS.font}
                fill="var(--text-secondary)"
              >
                Nearly whole roster back
              </text>
            )}
          </svg>
        )}
      </div>
      <div className={styles.controls}>
        <Slider
          id="returning"
          label="Returning production"
          min={0.2}
          max={0.95}
          step={0.01}
          value={returning}
          onChange={onReturning}
          display={`${Math.round(returning * 100)}%`}
        />
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={missing}
            onChange={(e) => onMissing(e.currentTarget.checked)}
          />
          Transfer portal data missing
        </label>
      </div>
      <dl className={styles.readout}>
        <div>
          <dt>Starting uncertainty</dt>
          <dd>±{fmtNum(sd, 2)} per snap</dd>
        </div>
        <div>
          <dt>Prior variance</dt>
          <dd>{fmtNum(variance, 4)}</dd>
        </div>
        <div>
          <dt>Pull of the first game</dt>
          <dd>{Math.round(firstPull * 100)}%</dd>
        </div>
      </dl>
      <p className={styles.note}>
        The pull is how far week 1 moves the rating toward what actually happened. It carries into
        the next step.
      </p>
    </div>
  );
}
