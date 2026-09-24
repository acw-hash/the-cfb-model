"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { GameRow } from "@/components/GameRow/GameRow";
import { HowToReadKey } from "@/components/HowToReadKey/HowToReadKey";
import { OddsAsOf } from "@/components/OddsAsOf/OddsAsOf";
import { SlateGroupHeader } from "@/components/SlateGroupHeader/SlateGroupHeader";
import { SortControl } from "@/components/SortControl/SortControl";
import { TeamSearch } from "@/components/TeamSearch/TeamSearch";
import { ThisWeekHeader } from "@/components/ThisWeekHeader/ThisWeekHeader";
import type { RefreshKind } from "@/lib/artifacts/types";
import type { OddsPageContext } from "@/lib/odds/types";
import { filterGamesByQuery, parseSearchQuery } from "@/lib/this-week/search";
import {
  DEFAULT_SLATE_ORDER,
  groupSlate,
  parseSlateOrder,
  type SlateOrder,
  type ThisWeekClientGame,
} from "@/lib/this-week/sort";

import styles from "./ThisWeekSlate.module.css";

interface ThisWeekSlateProps {
  season: number;
  week: number;
  publishedAt: string;
  refreshKind: RefreshKind;
  games: ThisWeekClientGame[];
  initialOrder?: SlateOrder;
  /** When true (This Week route), order and query mirror to the URL without a refetch. */
  syncUrl?: boolean;
  /** Optional odds snapshot context (ODDS_SNAPSHOT_ENABLED). */
  odds?: OddsPageContext | null;
}

function writeOrderToUrl(order: SlateOrder): void {
  const url = new URL(window.location.href);
  if (order === DEFAULT_SLATE_ORDER) {
    url.searchParams.delete("order");
  } else {
    url.searchParams.set("order", order);
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  window.history.replaceState(null, "", next);
}

function writeQueryToUrl(query: string): void {
  const url = new URL(window.location.href);
  const trimmed = query.trim();
  if (!trimmed) {
    url.searchParams.delete("q");
  } else {
    url.searchParams.set("q", trimmed);
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  window.history.replaceState(null, "", next);
}

/**
 * Client slate: filter, sort, and group already-loaded games. Controls never refetch.
 */
export function ThisWeekSlate({
  season,
  week,
  publishedAt,
  refreshKind,
  games,
  initialOrder = DEFAULT_SLATE_ORDER,
  syncUrl = false,
  odds = null,
}: ThisWeekSlateProps): React.ReactElement {
  const [order, setOrder] = useState<SlateOrder>(initialOrder);
  const [query, setQuery] = useState("");
  const timeZone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone, []);

  useEffect(() => {
    if (!syncUrl) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    setOrder(parseSlateOrder(params.get("order")));
    setQuery(parseSearchQuery(params.get("q")));
  }, [syncUrl]);

  const filteredGames = useMemo(() => filterGamesByQuery(games, query), [games, query]);

  const groups = useMemo(
    () => groupSlate(filteredGames, order, timeZone),
    [filteredGames, order, timeZone],
  );

  const handleOrder = useCallback(
    (next: SlateOrder) => {
      setOrder(next);
      if (syncUrl) {
        writeOrderToUrl(next);
      }
    },
    [syncUrl],
  );

  const handleQuery = useCallback(
    (next: string) => {
      setQuery(next);
      if (syncUrl) {
        writeQueryToUrl(next);
      }
    },
    [syncUrl],
  );

  const clearQuery = useCallback(() => {
    setQuery("");
    if (syncUrl) {
      writeQueryToUrl("");
    }
  }, [syncUrl]);

  const trimmedQuery = query.trim();
  const showNoMatches = trimmedQuery.length > 0 && filteredGames.length === 0;
  const liveMessage = trimmedQuery ? `${filteredGames.length} games match` : "";

  return (
    <>
      <div className={styles.sticky}>
        <ThisWeekHeader
          season={season}
          week={week}
          publishedAt={publishedAt}
          refreshKind={refreshKind}
        />
        {odds ? <OddsAsOf odds={odds} timeZone={timeZone} /> : null}
        <div className={styles.controls}>
          <TeamSearch value={query} onChange={handleQuery} onClear={clearQuery} />
          <SortControl value={order} onChange={handleOrder} />
        </div>
        <p className={styles.live} aria-live="polite" aria-atomic="true">
          {liveMessage}
        </p>
      </div>
      <HowToReadKey />
      {showNoMatches ? (
        <p className={styles.noMatches} data-testid="search-no-matches">
          No games match that team.{" "}
          <button type="button" className={styles.clearSearch} onClick={clearQuery}>
            Clear search
          </button>
        </p>
      ) : (
        <div className={styles.slate} data-testid="slate" data-order={order}>
          {groups.map((group) => (
            <section key={group.id} className={styles.group} data-group={group.id}>
              <SlateGroupHeader label={group.label} />
              {group.games.map((game) => (
                <Link key={game.game_id} href={`/game/${game.game_id}`} className={styles.rowLink}>
                  <GameRow game={game} odds={odds?.byGameId[game.game_id] ?? null} />
                </Link>
              ))}
            </section>
          ))}
        </div>
      )}
    </>
  );
}
