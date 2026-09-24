"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import styles from "../ModelWalkthrough.module.css";
import { fmtNum, fmtSigned, priorVariance, runKalman } from "../../../lib/visual/math";
import { PRIOR_MEAN, SEASON } from "../../../lib/visual/example";
import { AXIS, linear, usePrefersReducedMotion, useWidth } from "./shared";

type Props = { returning: number; missing: boolean };

const Y0 = -0.2;
const Y1 = 0.75;

export default function KalmanFigure({ returning, missing }: Props) {
  const rows = useMemo(
    () => runKalman(PRIOR_MEAN, priorVariance(returning, missing ? 1 : 0), SEASON),
    [returning, missing],
  );
  const last = rows.length - 1;
  const [week, setWeek] = useState(0);
  const [running, setRunning] = useState(false);
  const reduced = usePrefersReducedMotion();
  const timer = useRef<number | null>(null);
  const { ref, width } = useWidth();

  useEffect(() => {
    if (!running) return undefined;
    if (reduced) {
      setWeek(last);
      setRunning(false);
      return undefined;
    }
    timer.current = window.setInterval(() => {
      setWeek((w) => {
        if (w >= last) {
          setRunning(false);
          return w;
        }
        return w + 1;
      });
    }, 650);
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [running, reduced, last]);

  const h = 260;
  const pad = { l: 44, r: 10, t: 14, b: 34 };
  const x = linear(0, last, pad.l, Math.max(width - pad.r, pad.l + 1));
  const y = linear(Y0, Y1, h - pad.b, pad.t);
  const shown = rows.slice(0, week + 1);
  const upper = shown.map((r) => `${x(r.week)},${y(r.mean + Math.sqrt(r.variance))}`);
  const lower = shown.map((r) => `${x(r.week)},${y(r.mean - Math.sqrt(r.variance))}`).reverse();
  const band = shown.length > 1 ? `M${upper.join(" L")} L${lower.join(" L")} Z` : "";
  const line = shown.map((r, i) => `${i === 0 ? "M" : "L"}${x(r.week)},${y(r.mean)}`).join("");
  const cur = rows[week];
  const sd = Math.sqrt(cur.variance);
  const yTicks = [-0.2, 0, 0.2, 0.4, 0.6];

  const stop = () => setRunning(false);

  return (
    <div>
      <div ref={ref} className={styles.chart} style={{ minHeight: h }}>
        {width > 0 && (
          <svg
            width={width}
            height={h}
            role="img"
            aria-label={`Week ${cur.week}: rating ${fmtSigned(cur.mean, 2)}, uncertainty plus or minus ${fmtNum(sd, 2)}.`}
          >
            {yTicks.map((t) => (
              <g key={t}>
                <line
                  x1={pad.l}
                  x2={width - pad.r}
                  y1={y(t)}
                  y2={y(t)}
                  stroke={t === 0 ? "var(--text-tertiary)" : "var(--border-subtle)"}
                  strokeDasharray={t === 0 ? undefined : "2 4"}
                />
                <text
                  x={pad.l - 8}
                  y={y(t) + 4}
                  textAnchor="end"
                  fontSize={AXIS.font}
                  fill="var(--text-secondary)"
                >
                  {t === 0 ? "0" : fmtSigned(t, 1)}
                </text>
              </g>
            ))}
            {rows.map((r) => (
              <text
                key={r.week}
                x={x(r.week)}
                y={h - pad.b + 18}
                textAnchor="middle"
                fontSize={AXIS.font}
                fontWeight={r.week === week ? 600 : 400}
                fill={r.week === week ? "var(--text-primary)" : "var(--text-secondary)"}
              >
                {r.week === 0 ? "Pre" : r.obs === null ? "Bye" : r.week}
              </text>
            ))}
            <text x={pad.l} y={h - 2} fontSize={AXIS.font} fill="var(--text-secondary)">
              Week
            </text>
            {band && <path d={band} fill="var(--bg-secondary)" stroke="var(--border-subtle)" />}
            {shown.map((r) =>
              r.obs === null ? null : (
                <g key={`o${r.week}`}>
                  {r.wasCapped && r.capped !== null && (
                    <>
                      <line
                        x1={x(r.week)}
                        x2={x(r.week)}
                        y1={y(r.obs)}
                        y2={y(r.predMean + r.capped)}
                        stroke="var(--text-tertiary)"
                        strokeDasharray="3 3"
                      />
                      <line
                        x1={x(r.week) - 6}
                        x2={x(r.week) + 6}
                        y1={y(r.predMean + r.capped)}
                        y2={y(r.predMean + r.capped)}
                        stroke="var(--text-secondary)"
                        strokeWidth={1.5}
                      />
                    </>
                  )}
                  <circle
                    cx={x(r.week)}
                    cy={y(r.obs)}
                    r={4}
                    fill="var(--bg-primary)"
                    stroke="var(--text-secondary)"
                    strokeWidth={1.5}
                  />
                </g>
              ),
            )}
            <path d={line} fill="none" stroke="var(--text-primary)" strokeWidth={2} />
            <line
              x1={x(week)}
              x2={x(week)}
              y1={pad.t}
              y2={h - pad.b}
              stroke="var(--accent)"
              strokeOpacity={0.35}
            />
            <line
              x1={x(week)}
              x2={x(week)}
              y1={y(cur.mean - sd)}
              y2={y(cur.mean + sd)}
              stroke="var(--accent)"
              strokeWidth={2}
            />
            {[cur.mean - sd, cur.mean + sd].map((v) => (
              <line
                key={v}
                x1={x(week) - 5}
                x2={x(week) + 5}
                y1={y(v)}
                y2={y(v)}
                stroke="var(--accent)"
                strokeWidth={2}
              />
            ))}
            <circle cx={x(week)} cy={y(cur.mean)} r={5} fill="var(--accent)" />
          </svg>
        )}
      </div>
      <div className={styles.controls}>
        <button
          type="button"
          className={styles.button}
          onClick={() => {
            stop();
            setWeek((w) => Math.max(0, w - 1));
          }}
          disabled={week === 0}
        >
          Previous week
        </button>
        <button
          type="button"
          className={`${styles.button} ${styles.buttonPrimary}`}
          onClick={() => {
            stop();
            setWeek((w) => Math.min(last, w + 1));
          }}
          disabled={week === last}
        >
          Next week
        </button>
        <button
          type="button"
          className={styles.button}
          onClick={() => {
            if (running) {
              stop();
              return;
            }
            if (week >= last) setWeek(0);
            setRunning(true);
          }}
        >
          {running ? "Pause" : "Run the season"}
        </button>
        <span className={styles.check} aria-hidden="true">
          <svg width="12" height="12">
            <circle
              cx="6"
              cy="6"
              r="4"
              fill="var(--bg-primary)"
              stroke="var(--text-secondary)"
              strokeWidth="1.5"
            />
          </svg>
          Game result
        </span>
      </div>
      <dl className={styles.readout} aria-live="polite">
        <div>
          <dt>{cur.week === 0 ? "Preseason" : `Week ${cur.week}`}</dt>
          <dd>
            {cur.obs === null
              ? cur.week === 0
                ? "No games yet"
                : "Bye"
              : `Result ${fmtSigned(cur.obs, 2)}`}
          </dd>
        </div>
        <div>
          <dt>Surprise</dt>
          <dd>
            {cur.innovation === null
              ? "\u2014"
              : cur.wasCapped && cur.capped !== null
                ? `${fmtSigned(cur.innovation, 2)} → ${fmtSigned(cur.capped, 2)}`
                : fmtSigned(cur.innovation, 2)}
          </dd>
        </div>
        <div>
          <dt>Pull (gain K)</dt>
          <dd>{cur.gain === null ? "\u2014" : `${Math.round(cur.gain * 100)}%`}</dd>
        </div>
        <div>
          <dt>Rating</dt>
          <dd>
            {fmtSigned(cur.mean, 2)} ±{fmtNum(sd, 2)}
          </dd>
        </div>
      </dl>
      <p className={styles.note}>
        {cur.week === 0 &&
          "The season starts from the preseason view in step 2. Change returning production there and this whole path changes."}
        {cur.week > 0 &&
          cur.obs === null &&
          "No game this week, so nothing to learn from. The rating holds, and its uncertainty grows slightly because teams change over time."}
        {cur.obs !== null &&
          cur.wasCapped &&
          "A lopsided result, more than 2.5 standard deviations from what was expected. Only the capped surprise is used, so one game can't swing the rating."}
        {cur.obs !== null &&
          !cur.wasCapped &&
          (cur.week <= 2
            ? "Early in the season the model is unsure, so each result pulls the rating a long way."
            : "As results accumulate the model grows more confident, so each new game pulls less and the band narrows.")}
      </p>
    </div>
  );
}
