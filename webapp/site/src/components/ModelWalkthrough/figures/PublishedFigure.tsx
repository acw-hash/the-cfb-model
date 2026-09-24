"use client";

import { useMemo, useState } from "react";
import styles from "../ModelWalkthrough.module.css";
import {
  fmtNum,
  fmtPct,
  fmtSigned,
  marginPmf,
  rawTier,
  tierLabel,
  winShare,
} from "../../../lib/visual/math";
import {
  AWAY,
  CONFORMAL_WIDEN,
  HOME,
  KEY_WEIGHTS,
  KICKOFF_LABEL,
  Q10,
  Q90,
} from "../../../lib/visual/example";
import { exampleForecast } from "./UncertaintyFigure";
import { Segmented } from "./shared";

type State = "normal" | "stale" | "refused" | "unavailable";

const NOTES: Record<State, string> = {
  normal: "Everything passed. The row shows the forecast, its range and the conviction label.",
  stale:
    "An input feed hasn't updated for more than 6 hours. The row is marked and the label is hidden until fresh data arrives.",
  refused:
    "The uncertainty estimate failed its checks, so σ is refused. Without a trustworthy σ there is no honest win chance, so the label disappears too.",
  unavailable:
    "No model passed its health checks for this game. Ridge publishes nothing rather than a placeholder, and says why.",
};

export default function PublishedFigure() {
  const f = useMemo(exampleForecast, []);
  const [state, setState] = useState<State>("normal");
  const pHome = useMemo(() => winShare(marginPmf(f.mu, f.sigma, KEY_WEIGHTS)), [f]);
  const tier = rawTier(f.mu >= 0 ? pHome : 1 - pHome);
  const label = tierLabel(tier, f.mu >= 0 ? HOME : AWAY);
  const lo = Q10 - CONFORMAL_WIDEN;
  const hi = Q90 + CONFORMAL_WIDEN;

  const hasMu = state !== "unavailable";
  const hasSigma = state === "normal" || state === "stale";
  const showTier = state === "normal";

  const fields: { name: string; value: string | null }[] = [
    { name: "mu_margin", value: hasMu ? fmtSigned(f.mu) : null },
    { name: "sigma_margin", value: hasSigma ? fmtNum(f.sigma) : null },
    {
      name: "margin_interval_lo / hi",
      value: hasMu ? `${fmtSigned(lo)} / ${fmtSigned(hi)}` : null,
    },
    { name: "p_win_home", value: hasSigma ? fmtPct(pHome) : null },
    { name: "conviction_label", value: showTier ? label : null },
    { name: "stale_stamp", value: state === "stale" ? "STALE(odds, 7.0h)" : null },
    { name: "null_reason", value: state === "unavailable" ? "cold_start_insufficient" : null },
  ];

  return (
    <div>
      <div className={styles.row} aria-live="polite">
        <span className={styles.rowKick}>{KICKOFF_LABEL}</span>
        <span className={styles.rowTeams}>
          {AWAY} <span>@</span> {HOME}
        </span>
        <span className={styles.rowNum}>
          {hasMu ? (
            <>
              <b>{fmtSigned(f.mu)}</b>
              <span>
                [{fmtSigned(lo)}, {fmtSigned(hi)}]
              </span>
            </>
          ) : (
            <span style={{ marginLeft: 0 }}>Forecast unavailable</span>
          )}
        </span>
        <span className={styles.rowTier}>
          {state === "stale" && <span className={styles.stale}>STALE(odds, 7.0h)</span>}
          {showTier && (
            <span className={`${styles.chip} ${tier === "toss_up" ? styles.chipMuted : ""}`}>
              {label}
            </span>
          )}
        </span>
      </div>
      <ul className={styles.fieldList}>
        {fields.map((fl) => (
          <li key={fl.name} className={fl.value === null ? styles.fieldNull : undefined}>
            <code>{fl.name}</code>
            <span className={styles.num}>{fl.value ?? "null"}</span>
          </li>
        ))}
      </ul>
      <div className={styles.controls}>
        <Segmented<State>
          label="Row state"
          value={state}
          onChange={setState}
          options={[
            { value: "normal", label: "Normal" },
            { value: "stale", label: "Stale inputs" },
            { value: "refused", label: "σ refused" },
            { value: "unavailable", label: "Unavailable" },
          ]}
        />
      </div>
      <p className={styles.note}>{NOTES[state]}</p>
    </div>
  );
}
