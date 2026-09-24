"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import styles from "./ModelWalkthrough.module.css";
import { GROUPS, PAGE, STEPS, type StepGroup } from "../../lib/visual/copy";
import { DEFAULT_RETURNING } from "../../lib/visual/example";
import InputsFigure from "./figures/InputsFigure";
import PriorFigure from "./figures/PriorFigure";
import KalmanFigure from "./figures/KalmanFigure";
import EnsembleFigure from "./figures/EnsembleFigure";
import UncertaintyFigure from "./figures/UncertaintyFigure";
import RangeFigure from "./figures/RangeFigure";
import ConvictionFigure from "./figures/ConvictionFigure";
import PublishedFigure from "./figures/PublishedFigure";

const GROUP_SPANS: { group: StepGroup; start: number; span: number }[] = [
  { group: "ratings", start: 1, span: 3 },
  { group: "forecast", start: 4, span: 1 },
  { group: "distribution", start: 5, span: 3 },
  { group: "site", start: 8, span: 1 },
];

function readStepFromUrl(): number {
  if (typeof window === "undefined") return 0;
  const n = Number(new URLSearchParams(window.location.search).get("step"));
  return Number.isInteger(n) && n >= 1 && n <= STEPS.length ? n - 1 : 0;
}

export default function ModelWalkthrough() {
  const [step, setStep] = useState(0);
  const [returning, setReturning] = useState(DEFAULT_RETURNING);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    setStep(readStepFromUrl());
  }, []);

  const go = useCallback((i: number) => {
    const next = Math.min(STEPS.length - 1, Math.max(0, i));
    setStep(next);
    const url = new URL(window.location.href);
    url.searchParams.set("step", String(next + 1));
    window.history.replaceState(window.history.state, "", url);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight") go(step + 1);
      if (e.key === "ArrowLeft") go(step - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, step]);

  const s = STEPS[step];
  const headingId = `visual-step-${s.id}`;

  let figure: ReactNode;
  switch (s.id) {
    case "inputs":
      figure = <InputsFigure />;
      break;
    case "prior":
      figure = (
        <PriorFigure
          returning={returning}
          missing={missing}
          onReturning={setReturning}
          onMissing={setMissing}
        />
      );
      break;
    case "update":
      figure = <KalmanFigure returning={returning} missing={missing} />;
      break;
    case "forecast":
      figure = <EnsembleFigure />;
      break;
    case "uncertainty":
      figure = <UncertaintyFigure />;
      break;
    case "range":
      figure = <RangeFigure />;
      break;
    case "conviction":
      figure = <ConvictionFigure />;
      break;
    default:
      figure = <PublishedFigure />;
  }

  return (
    <div className={styles.root}>
      <header className={styles.intro}>
        <h1 className={styles.title}>{PAGE.title}</h1>
        <p className={styles.lede}>{PAGE.lede}</p>
      </header>

      <nav className={styles.spine} aria-label="Model stages">
        {GROUP_SPANS.map((g) => {
          const active = s.group === g.group;
          return (
            <div
              key={g.group}
              className={`${styles.group} ${active ? styles.groupActive : ""}`}
              style={{ gridColumn: `${g.start} / span ${g.span}` }}
            >
              {GROUPS[g.group]}
            </div>
          );
        })}
        {STEPS.map((st, i) => {
          const cls = [
            styles.node,
            i < step ? styles.nodeDone : "",
            i === step ? styles.nodeCurrent : "",
          ].join(" ");
          return (
            <button
              key={st.id}
              type="button"
              className={cls}
              style={{ gridColumn: i + 1 }}
              aria-current={i === step ? "step" : undefined}
              aria-label={`Step ${i + 1}: ${st.short}`}
              onClick={() => go(i)}
            >
              <span className={styles.dot}>{i + 1}</span>
              <span className={styles.nodeLabel}>{st.short}</span>
            </button>
          );
        })}
      </nav>

      <section aria-labelledby={headingId}>
        <div className={styles.stageHead}>
          <div>
            <p className={styles.counter}>{PAGE.stepOf(step + 1, STEPS.length)}</p>
            <h2 id={headingId} className={styles.stageTitle}>
              {s.title}
            </h2>
          </div>
          <div className={styles.pager}>
            <button
              type="button"
              className={styles.button}
              onClick={() => go(step - 1)}
              disabled={step === 0}
            >
              {PAGE.back}
            </button>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              onClick={() => go(step === STEPS.length - 1 ? 0 : step + 1)}
            >
              {step === STEPS.length - 1 ? PAGE.restart : PAGE.next}
            </button>
          </div>
        </div>

        <div className={styles.stageBody}>
          <div className={styles.figure}>
            <span className={styles.exampleTag}>{PAGE.exampleTag}</span>
            {figure}
          </div>
          <div className={styles.text}>
            {s.plain.map((p) => (
              <p key={p}>{p}</p>
            ))}
            <div className={styles.tech}>
              <h3 className={styles.techHeading}>{PAGE.techHeading}</h3>
              {s.technical.map((p) => (
                <p key={p}>{p}</p>
              ))}
              {s.formula && <span className={styles.formula}>{s.formula}</span>}
            </div>
          </div>
        </div>
      </section>

      <p className={styles.closing}>
        {PAGE.closingBefore}{" "}
        <Link className={styles.link} href="/results">
          {PAGE.closingLinks.results}
        </Link>
        {PAGE.closingMiddle}{" "}
        <Link className={styles.link} href="/about">
          {PAGE.closingLinks.about}
        </Link>
        .
      </p>
    </div>
  );
}
