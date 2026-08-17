import {
  ATTRIBUTION_HEADING,
  ATTRIBUTION_PLACEHOLDER,
  CFBD_ATTRIBUTION,
  HONESTY_COMMITMENTS,
  MODEL_SECTIONS,
  RIDGE_IDENTITY,
  disclaimerForYear,
} from "@/lib/about/copy";

import styles from "./AboutPage.module.css";

interface AboutPageProps {
  /** UTC calendar year for © line — server-rendered. */
  year: number;
}

/** Methodology / About composition (§5.4, §6). */
export function AboutPage({ year }: AboutPageProps): React.ReactElement {
  return (
    <article className={styles.page} data-testid="about-page">
      <header className={styles.header}>
        <h1 className={styles.title}>About</h1>
        <p className={styles.identity} data-testid="ridge-identity">
          {RIDGE_IDENTITY}
        </p>
      </header>

      {MODEL_SECTIONS.map((section) => (
        <section
          key={section.id}
          id={section.id}
          className={styles.section}
          data-testid={`about-${section.id}`}
        >
          <h2 className={styles.sectionTitle}>{section.title}</h2>
          {section.paragraphs.map((p) => (
            <p key={p.slice(0, 48)} className={styles.body}>
              {p}
            </p>
          ))}
        </section>
      ))}

      <section id="honesty" className={styles.section} data-testid="about-honesty">
        <h2 className={styles.sectionTitle}>Honesty commitments</h2>
        <ul className={styles.list}>
          {HONESTY_COMMITMENTS.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <section id="disclaimer" className={styles.section} data-testid="about-disclaimer">
        <h2 className={styles.sectionTitle}>Disclaimer</h2>
        <p className={styles.disclaimerBlock}>{disclaimerForYear(year)}</p>
      </section>

      <section
        id="responsible-gambling"
        className={styles.section}
        data-testid="about-responsible-gambling"
      >
        <h2 className={styles.sectionTitle}>Responsible gambling</h2>
        <p className={styles.rgBlock}>
          If you or someone you know has a gambling problem, call{" "}
          <a href="tel:18004262537">1-800-GAMBLER</a> (1-800-426-2537). Help is available 24/7.
          Ridge does not accept wagers and is not affiliated with any sportsbook.
        </p>
      </section>

      <section id="attribution" className={styles.section} data-testid="about-attribution">
        <h2 className={styles.sectionTitle}>{ATTRIBUTION_HEADING}</h2>
        <p className={styles.attribution}>{CFBD_ATTRIBUTION}</p>
        <p className={styles.placeholder} data-testid="attribution-placeholder">
          {ATTRIBUTION_PLACEHOLDER}
        </p>
      </section>
    </article>
  );
}
