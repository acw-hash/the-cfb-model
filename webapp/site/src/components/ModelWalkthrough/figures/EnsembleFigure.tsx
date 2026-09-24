"use client";

import { useState } from "react";
import styles from "../ModelWalkthrough.module.css";
import { fmtNum, fmtSigned } from "../../../lib/visual/math";
import { HOME, MEMBERS, MU_TOTAL } from "../../../lib/visual/example";

const FEATURES = [
  { name: "Rating matchup", detail: "Offense vs defense, special teams, pace, home field" },
  { name: "Tempo", detail: "Expected possessions for both teams" },
  { name: "Situation", detail: "Rest, travel, time zones, altitude, rivalry" },
  { name: "Recent form", detail: "Weighted trend and last-3 change" },
];

const COLORS = ["var(--text-primary)", "var(--text-tertiary)"];

export default function EnsembleFigure() {
  const [on, setOn] = useState<Record<string, boolean>>({ lgbm: true, enet: true });
  const active = MEMBERS.filter((m) => on[m.id]);
  const wSum = active.reduce((a, m) => a + m.weight, 0);
  const weights = MEMBERS.map((m) => (on[m.id] && wSum > 0 ? m.weight / wSum : 0));
  const mu = MEMBERS.reduce((a, m, i) => a + weights[i] * m.mu, 0);
  const unavailable = active.length === 0;

  return (
    <div>
      <div className={styles.flow}>
        <div className={styles.flowCol}>
          <p className={styles.flowHead}>Matchup features</p>
          {FEATURES.map((f) => (
            <div key={f.name} className={styles.featureItem}>
              {f.name}
              <small>{f.detail}</small>
            </div>
          ))}
        </div>
        <div className={styles.flowCol}>
          <p className={styles.flowHead}>Models (tap to switch off)</p>
          {MEMBERS.map((m, i) => (
            <button
              key={m.id}
              type="button"
              className={styles.memberToggle}
              aria-pressed={on[m.id]}
              onClick={() => setOn((s) => ({ ...s, [m.id]: !s[m.id] }))}
            >
              <span className={styles.memberName}>{m.name}</span>
              <span className={styles.memberKind}>{m.kind}</span>
              <span className={styles.memberNum}>
                <b>{fmtSigned(m.mu)}</b>
                <span>{on[m.id] ? `weight ${Math.round(weights[i] * 100)}%` : "excluded"}</span>
              </span>
            </button>
          ))}
        </div>
        <div className={styles.flowCol}>
          <p className={styles.flowHead}>Blend</p>
          <div className={styles.weightBar} aria-hidden="true">
            {MEMBERS.map((m, i) => (
              <span key={m.id} style={{ width: `${weights[i] * 100}%`, background: COLORS[i] }} />
            ))}
          </div>
          <div
            className={`${styles.output} ${unavailable ? styles.outputUnavailable : ""}`}
            aria-live="polite"
          >
            {unavailable ? (
              <>
                <span className={styles.outputBig}>Forecast unavailable</span>
                <span className={styles.outputSmall}>
                  No credible model. Published as null with null_reason cold_start_insufficient.
                </span>
              </>
            ) : (
              <>
                <span className={styles.outputSmall}>Expected margin</span>
                <span className={styles.outputBig}>{fmtSigned(mu)}</span>
                <span className={styles.outputSmall}>
                  {HOME} by {fmtNum(Math.abs(mu))} points
                </span>
                <span className={styles.outputSmall}>Combined score {fmtNum(MU_TOTAL)}</span>
              </>
            )}
          </div>
        </div>
      </div>
      <p className={styles.note}>
        {unavailable
          ? "With no model passing its health checks, Ridge refuses to invent a number. The row on This Week says the forecast is unavailable and why."
          : active.length === 1
            ? `Only ${active[0].name} passed, so it carries all the weight. The forecast is still honest: it comes from a real model, not a placeholder.`
            : "Weights are learned from how each model did on games it hadn't seen, and always add up to 100%."}
      </p>
    </div>
  );
}
