import { PublishedAtStamp } from "@/components/PublishedAtStamp/PublishedAtStamp";
import { StaleBadge } from "@/components/StaleBadge/StaleBadge";
import type { GamePrediction } from "@/lib/artifacts/types";
import { formatRefreshKind } from "@/lib/this-week/refresh-kind";
import { PROVENANCE_GLOSS } from "@/lib/game-detail/provenance";

import styles from "./ProvenanceStrip.module.css";

interface ProvenanceStripProps {
  game: GamePrediction;
}

/** Vintage, ensemble, feature time — each with a plain-language gloss on-page. */
export function ProvenanceStrip({ game }: ProvenanceStripProps): React.ReactElement {
  const items = [
    { gloss: PROVENANCE_GLOSS.vintage, value: game.vintage_label },
    { gloss: PROVENANCE_GLOSS.ensemble, value: game.ensemble_scope_label },
    { gloss: PROVENANCE_GLOSS.featureTime, value: game.feature_time_label },
  ];

  return (
    <section className={styles.block} data-testid="provenance">
      <h2 className={styles.label}>Provenance</h2>
      <dl className={styles.list}>
        {items.map((item) => (
          <div key={item.gloss.field} className={styles.item}>
            <dt className={styles.title}>{item.gloss.title}</dt>
            <dd className={styles.value}>{item.value}</dd>
            <p className={styles.meaning}>{item.gloss.meaning}</p>
          </div>
        ))}
      </dl>
      <div className={styles.publish}>
        <PublishedAtStamp publishedAt={game.published_at} />
        <p className={styles.refresh}>{formatRefreshKind(game.refresh_kind)}</p>
        <StaleBadge staleStamp={game.stale_stamp} sources={game.stale_sources} />
      </div>
    </section>
  );
}
