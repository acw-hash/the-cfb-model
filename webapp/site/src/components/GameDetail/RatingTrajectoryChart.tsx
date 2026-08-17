import { Figure } from "@/components/Figure/Figure";
import { formatEpa } from "@/lib/formatting/numbers";
import {
  CHART_VIEW_HEIGHT,
  CHART_VIEW_WIDTH,
  makeScales,
  niceTicks,
  pathsForSeries,
  xTickWeeks,
  yDomain,
  type ChartScales,
} from "@/lib/game-detail/geometry";
import type { RatingDimension, RatingPoint } from "@/lib/game-detail/ratings";
import { trajectoryCaption } from "@/lib/game-detail/ratings";

import styles from "./RatingTrajectoryChart.module.css";

interface RatingTrajectoryChartProps {
  homeSchool: string;
  awaySchool: string;
  home: RatingPoint[];
  away: RatingPoint[];
  throughWeek: number;
}

interface Marker {
  week: number;
  mean: number;
  current: boolean;
}

function markersFor(
  points: RatingPoint[],
  dimension: RatingDimension,
  throughWeek: number,
): Marker[] {
  return points.map((p) => ({
    week: p.week,
    mean: dimension === "off" ? p.off_epa : p.def_epa,
    current: p.week === throughWeek,
  }));
}

function Panel({
  title,
  dimension,
  home,
  away,
  scales,
  throughWeek,
}: {
  title: string;
  dimension: RatingDimension;
  home: RatingPoint[];
  away: RatingPoint[];
  scales: ChartScales;
  throughWeek: number;
}): React.ReactElement {
  const homePaths = pathsForSeries(home, dimension, scales);
  const awayPaths = pathsForSeries(away, dimension, scales);
  const yTicks = niceTicks(scales.yMin, scales.yMax);
  const zeroInRange = scales.yMin < 0 && scales.yMax > 0;
  const homeMarks = markersFor(home, dimension, throughWeek);
  const awayMarks = markersFor(away, dimension, throughWeek);

  return (
    <div className={styles.panel}>
      <h3 className={styles.panelTitle}>{title}</h3>
      <div className={styles.plot}>
        <div className={styles.yAxis} aria-hidden="true">
          {yTicks.map((tick) => (
            <span
              key={tick}
              className={styles.yTick}
              style={{ top: `${(scales.y(tick) / CHART_VIEW_HEIGHT) * 100}%` }}
            >
              <Figure variant="c2">{formatEpa(tick)}</Figure>
            </span>
          ))}
        </div>
        <div className={styles.plotBody}>
          <svg
            className={styles.svg}
            viewBox={`0 0 ${CHART_VIEW_WIDTH} ${CHART_VIEW_HEIGHT}`}
            role="presentation"
            aria-hidden="true"
            focusable="false"
          >
            {zeroInRange ? (
              <line
                x1={scales.x(scales.xMin)}
                x2={scales.x(scales.xMax)}
                y1={scales.y(0)}
                y2={scales.y(0)}
                className={styles.zero}
              />
            ) : null}
            {awayPaths.band.map((d) => (
              <path key={`ab-${d}`} d={d} className={styles.bandAway} />
            ))}
            {homePaths.band.map((d) => (
              <path key={`hb-${d}`} d={d} className={styles.bandHome} />
            ))}
            {awayPaths.line.map((d) => (
              <path key={`al-${d}`} d={d} className={styles.lineAway} />
            ))}
            {homePaths.line.map((d) => (
              <path key={`hl-${d}`} d={d} className={styles.lineHome} />
            ))}
            {awayMarks.map((m) => (
              <circle
                key={`am-${m.week}`}
                cx={scales.x(m.week)}
                cy={scales.y(m.mean)}
                r={3}
                className={m.current ? styles.openAway : styles.dotAway}
              />
            ))}
            {homeMarks.map((m) => (
              <circle
                key={`hm-${m.week}`}
                cx={scales.x(m.week)}
                cy={scales.y(m.mean)}
                r={3}
                className={m.current ? styles.openHome : styles.dotHome}
              />
            ))}
          </svg>
        </div>
      </div>
    </div>
  );
}

/**
 * Hand-rolled SVG rating trajectories (§4.3).
 * Both teams, shared axes, ±1 posterior SD bands, gaps not interpolated.
 */
export function RatingTrajectoryChart({
  homeSchool,
  awaySchool,
  home,
  away,
  throughWeek,
}: RatingTrajectoryChartProps): React.ReactElement {
  const xMin = 1;
  const xMax = Math.max(1, throughWeek);
  const domain = yDomain([home, away]);
  const scales = makeScales(xMin, xMax, domain.min, domain.max);
  const weeks = xTickWeeks(xMin, xMax);
  const caption = trajectoryCaption(homeSchool, awaySchool, home, away, throughWeek);

  return (
    <figure
      className={styles.figure}
      data-testid="trajectory-chart"
      aria-labelledby="trajectory-chart-label"
      aria-describedby="trajectory-chart-caption"
    >
      <h2 className={styles.label} id="trajectory-chart-label">
        Stage-1 ratings
      </h2>
      <div className={styles.legend} aria-hidden="true">
        <span className={styles.legendHome}>{homeSchool}</span>
        <span className={styles.legendAway}>{awaySchool}</span>
      </div>
      <Panel
        title="Offense"
        dimension="off"
        home={home}
        away={away}
        scales={scales}
        throughWeek={throughWeek}
      />
      <Panel
        title="Defense"
        dimension="def"
        home={home}
        away={away}
        scales={scales}
        throughWeek={throughWeek}
      />
      <div className={styles.xAxis} aria-hidden="true">
        {weeks.map((week) => (
          <span
            key={week}
            className={styles.xTick}
            style={{ left: `${(scales.x(week) / CHART_VIEW_WIDTH) * 100}%` }}
          >
            <Figure variant="c2">{week}</Figure>
          </span>
        ))}
      </div>
      <p className={styles.xTitle} aria-hidden="true">
        Week
      </p>
      <figcaption className={styles.caption} id="trajectory-chart-caption">
        {caption}. Home team uses a solid line; away team uses a dashed line (not color alone).
      </figcaption>
    </figure>
  );
}
