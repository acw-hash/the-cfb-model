"use client";

import { useState } from "react";
import styles from "../ModelWalkthrough.module.css";
import { AXIS, Segmented, linear, useWidth } from "./shared";

type RunId = "tue" | "thu" | "fri" | "sat";

/** Hours from the previous Saturday 00:00 UTC. */
const RUNS: Record<RunId, { hour: number; label: string; note: string }> = {
  tue: {
    hour: 78,
    label: "Tuesday 06:00 UTC",
    note: "Primary publish. Last Saturday's results are in the ratings, and every game from Thursday to Saturday is forecast.",
  },
  thu: {
    hour: 126,
    label: "Thursday 06:00 UTC",
    note: "Refresh. No games have finished since Tuesday, so the ratings have no new results. The whole slate is still forecast.",
  },
  fri: {
    hour: 150,
    label: "Friday 06:00 UTC",
    note: "Refresh. Thursday night's result is now in the ratings, and that game has left the slate because it already kicked off.",
  },
  sat: {
    hour: 174,
    label: "Saturday 06:00 UTC",
    note: "Last refresh before most kickoffs. Friday night's result is in too. Each game is graded against the last publish before it starts.",
  },
};

const GAMES: {
  hour: number;
  label?: string;
  above?: boolean;
  anchor?: "start" | "middle" | "end";
}[] = [
  { hour: 17, label: "Last Saturday", anchor: "start" },
  { hour: 20 },
  { hour: 23 },
  { hour: 143, label: "Thu night" },
  { hour: 167, label: "Fri night", above: true },
  { hour: 180, label: "Saturday slate", anchor: "end" },
  { hour: 184 },
  { hour: 188 },
];

const DAYS = ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export default function InputsFigure() {
  const [run, setRun] = useState<RunId>("tue");
  const { ref, width } = useWidth();
  const h = 150;
  const pad = { l: 4, r: 4 };
  const x = linear(0, 192, pad.l, Math.max(width - pad.r, pad.l + 1));
  const cut = RUNS[run].hour;
  const gy = 70;
  const compact = width < 460;

  return (
    <div>
      <div ref={ref} className={styles.chart} style={{ minHeight: h }}>
        {width > 0 && (
          <svg
            width={width}
            height={h}
            role="img"
            aria-label={`Week timeline. Data before ${RUNS[run].label} is used; later games are forecast.`}
          >
            <rect x={x(0)} y={28} width={x(cut) - x(0)} height={80} fill="var(--bg-secondary)" />
            {DAYS.map((d, i) => (
              <g key={`${d}-${i}`}>
                <line
                  x1={x(i * 24)}
                  x2={x(i * 24)}
                  y1={28}
                  y2={108}
                  stroke="var(--border-subtle)"
                />
                <text
                  x={x(i * 24 + 12)}
                  y={18}
                  textAnchor="middle"
                  fontSize={AXIS.font}
                  fill="var(--text-secondary)"
                >
                  {compact ? d.slice(0, 2) : d}
                </text>
              </g>
            ))}
            <line x1={x(0)} x2={x(192)} y1={gy} y2={gy} stroke="var(--text-tertiary)" />
            {(Object.keys(RUNS) as RunId[]).map((id) => (
              <line
                key={id}
                x1={x(RUNS[id].hour)}
                x2={x(RUNS[id].hour)}
                y1={gy - 5}
                y2={gy + 5}
                stroke="var(--text-tertiary)"
              />
            ))}
            {GAMES.map((g) => {
              const known = g.hour < cut;
              return (
                <g key={g.hour}>
                  <circle
                    cx={x(g.hour)}
                    cy={gy}
                    r={5}
                    fill={known ? "var(--text-primary)" : "var(--bg-primary)"}
                    stroke={known ? "var(--text-primary)" : "var(--text-secondary)"}
                    strokeWidth={1.5}
                  />
                  {g.label && !compact && (
                    <text
                      x={
                        g.anchor === "end"
                          ? x(190)
                          : g.anchor === "start"
                            ? x(g.hour) - 5
                            : x(g.hour)
                      }
                      y={g.above ? gy - 12 : gy + 22}
                      textAnchor={g.anchor ?? "middle"}
                      fontSize={AXIS.font}
                      fill="var(--text-secondary)"
                    >
                      {g.label}
                    </text>
                  )}
                </g>
              );
            })}
            <line x1={x(cut)} x2={x(cut)} y1={24} y2={116} stroke="var(--accent)" strokeWidth={2} />
            <text
              x={x(cut)}
              y={132}
              textAnchor={cut > 150 ? "end" : "middle"}
              fontSize={AXIS.label}
              fontWeight={500}
              fill="var(--accent)"
            >
              as_of
            </text>
            <text x={x(0) + 6} y={42} fontSize={AXIS.font} fill="var(--text-secondary)">
              Known
            </text>
            {cut < 160 && (
              <text
                x={x(192) - 6}
                y={42}
                textAnchor="end"
                fontSize={AXIS.font}
                fill="var(--text-secondary)"
              >
                Not yet known
              </text>
            )}
          </svg>
        )}
      </div>
      <div className={styles.controls}>
        <Segmented<RunId>
          label="Publish run"
          value={run}
          onChange={setRun}
          options={[
            { value: "tue", label: "Tue" },
            { value: "thu", label: "Thu" },
            { value: "fri", label: "Fri" },
            { value: "sat", label: "Sat" },
          ]}
        />
        <span className={styles.check} aria-hidden="true">
          <svg width="12" height="12">
            <circle cx="6" cy="6" r="4.5" fill="var(--text-primary)" />
          </svg>
          In the ratings
        </span>
        <span className={styles.check} aria-hidden="true">
          <svg width="12" height="12">
            <circle
              cx="6"
              cy="6"
              r="4.5"
              fill="var(--bg-primary)"
              stroke="var(--text-secondary)"
              strokeWidth="1.5"
            />
          </svg>
          Being forecast
        </span>
      </div>
      <p className={styles.note} aria-live="polite">
        <strong>{RUNS[run].label}.</strong> {RUNS[run].note}
      </p>
    </div>
  );
}
