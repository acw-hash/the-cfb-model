"use client";

import { useMemo, useState } from "react";
import styles from "../ModelWalkthrough.module.css";
import {
  TIERS,
  TIER_NAMES,
  type TierId,
  fmtNum,
  fmtPct,
  fmtSigned,
  marginPmf,
  rawTier,
  tierLabel,
  winShare,
} from "../../../lib/visual/math";
import { AWAY, HOME, KEY_WEIGHTS } from "../../../lib/visual/example";
import { exampleForecast } from "./UncertaintyFigure";
import { AXIS, Slider, linear, useWidth } from "./shared";

const SEGMENTS: { id: TierId; from: number; to: number }[] = [
  { id: "toss_up", from: 0.5, to: TIERS.lean },
  { id: "lean", from: TIERS.lean, to: TIERS.clear },
  { id: "clear_lean", from: TIERS.clear, to: TIERS.strong },
  { id: "strong_lean", from: TIERS.strong, to: 1 },
];

export default function ConvictionFigure() {
  const base = useMemo(exampleForecast, []);
  const [mu, setMu] = useState(Math.round(base.mu * 10) / 10);
  const [sigma, setSigma] = useState(Math.round(base.sigma * 10) / 10);
  const [hold, setHold] = useState(false);
  const { ref, width } = useWidth();

  const pHome = useMemo(() => winShare(marginPmf(mu, sigma, KEY_WEIGHTS)), [mu, sigma]);
  const homeFavored = mu > 0 || (mu === 0 && pHome >= 0.5);
  const pFav = homeFavored ? pHome : 1 - pHome;
  const team = homeFavored ? HOME : AWAY;
  const tier = rawTier(pFav);

  const h = 124;
  const pad = { l: 8, r: 8 };
  const x = linear(0.5, 1, pad.l, Math.max(width - pad.r, pad.l + 1));
  const barY = 36;
  const barH = 22;
  const compact = width < 480;

  return (
    <div>
      <div ref={ref} className={styles.chart} style={{ minHeight: h }}>
        {width > 0 && (
          <svg
            width={width}
            height={h}
            role="img"
            aria-label={`Favored side win chance ${fmtPct(pFav)}: ${tierLabel(tier, team)}.`}
          >
            {SEGMENTS.map((s) => {
              const current = s.id === tier;
              return (
                <g key={s.id}>
                  <rect
                    x={x(s.from) + 1}
                    y={barY}
                    width={Math.max(0, x(s.to) - x(s.from) - 2)}
                    height={barH}
                    rx={4}
                    fill={current ? "var(--text-primary)" : "var(--bg-secondary)"}
                  />
                  <text
                    x={(x(s.from) + x(s.to)) / 2}
                    y={barY - 10}
                    textAnchor="middle"
                    fontSize={AXIS.font}
                    fontWeight={current ? 600 : 400}
                    fill={current ? "var(--text-primary)" : "var(--text-secondary)"}
                  >
                    {compact && s.id !== tier
                      ? TIER_NAMES[s.id].replace(" lean", "")
                      : TIER_NAMES[s.id]}
                  </text>
                </g>
              );
            })}
            {hold &&
              [TIERS.lean, TIERS.clear, TIERS.strong].map((t) => (
                <rect
                  key={t}
                  x={x(t - TIERS.holdBand)}
                  y={barY}
                  width={x(t) - x(t - TIERS.holdBand)}
                  height={barH}
                  fill="var(--semantic-revised)"
                  opacity={0.45}
                />
              ))}
            {[0.5, TIERS.lean, TIERS.clear, TIERS.strong, 1].map((t) => (
              <text
                key={t}
                x={x(t)}
                y={barY + barH + 40}
                textAnchor={t === 0.5 ? "start" : t === 1 ? "end" : "middle"}
                fontSize={AXIS.font}
                fill="var(--text-secondary)"
              >
                {t === TIERS.lean ? "57.5%" : `${Math.round(t * 100)}%`}
              </text>
            ))}
            <line
              x1={x(pFav)}
              x2={x(pFav)}
              y1={barY - 4}
              y2={barY + barH + 4}
              stroke="var(--accent)"
              strokeWidth={3}
              strokeLinecap="round"
            />
            <circle
              cx={x(pFav)}
              cy={barY + barH / 2}
              r={5}
              fill="var(--accent)"
              stroke="var(--bg-primary)"
              strokeWidth={2}
            />
            <text
              x={Math.min(Math.max(x(pFav), pad.l + 16), width - pad.r - 16)}
              y={barY + barH + 20}
              textAnchor="middle"
              fontSize={AXIS.label}
              fontWeight={600}
              fill="var(--accent)"
            >
              {fmtPct(pFav)}
            </text>
          </svg>
        )}
      </div>
      <div className={styles.controls}>
        <Slider
          id="mu"
          label="Expected margin μ"
          min={-24}
          max={24}
          step={0.1}
          value={mu}
          onChange={setMu}
          display={fmtSigned(mu)}
        />
        <Slider
          id="sigma"
          label="Uncertainty σ"
          min={8}
          max={22}
          step={0.1}
          value={sigma}
          onChange={setSigma}
          display={fmtNum(sigma)}
        />
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={hold}
            onChange={(e) => setHold(e.currentTarget.checked)}
          />
          Show hold zones
        </label>
      </div>
      <dl className={styles.readout} aria-live="polite">
        <div>
          <dt>{HOME} win chance</dt>
          <dd>{fmtPct(pHome)}</dd>
        </div>
        <div>
          <dt>{AWAY} win chance</dt>
          <dd>{fmtPct(1 - pHome)}</dd>
        </div>
        <div>
          <dt>Label</dt>
          <dd>
            <span className={`${styles.chip} ${tier === "toss_up" ? styles.chipMuted : ""}`}>
              {tierLabel(tier, team)}
            </span>
          </dd>
        </div>
      </dl>
      <p className={styles.note}>
        {hold
          ? "Grey zones sit just under each threshold. A game already in a tier keeps it there, so a small wiggle between Tuesday and Saturday doesn't flip the label."
          : "Try raising σ without touching μ: the same expected margin becomes a weaker lean, because a noisier game is harder to call."}
      </p>
    </div>
  );
}
