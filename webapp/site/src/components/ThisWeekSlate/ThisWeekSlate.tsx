"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { GameRow } from "@/components/GameRow/GameRow";
import { SlateGroupHeader } from "@/components/SlateGroupHeader/SlateGroupHeader";
import { SortControl } from "@/components/SortControl/SortControl";
import { ThisWeekHeader } from "@/components/ThisWeekHeader/ThisWeekHeader";
import type { RefreshKind } from "@/lib/artifacts/types";
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
  /** When true (This Week route), order is mirrored to ?order= without a refetch. */
  syncUrl?: boolean;
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

/**
 * Client slate: sort/group already-loaded games. Toggle never refetches.
 */
export function ThisWeekSlate({
  season,
  week,
  publishedAt,
  refreshKind,
  games,
  initialOrder = DEFAULT_SLATE_ORDER,
  syncUrl = false,
}: ThisWeekSlateProps): React.ReactElement {
  const [order, setOrder] = useState<SlateOrder>(initialOrder);
  const timeZone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone, []);

  useEffect(() => {
    if (!syncUrl) {
      return;
    }
    const fromUrl = parseSlateOrder(new URLSearchParams(window.location.search).get("order"));
    setOrder(fromUrl);
  }, [syncUrl]);

  const groups = useMemo(() => groupSlate(games, order, timeZone), [games, order, timeZone]);

  const handleOrder = useCallback(
    (next: SlateOrder) => {
      setOrder(next);
      if (syncUrl) {
        writeOrderToUrl(next);
      }
    },
    [syncUrl],
  );

  return (
    <>
      <div className={styles.sticky}>
        <ThisWeekHeader
          season={season}
          week={week}
          publishedAt={publishedAt}
          refreshKind={refreshKind}
        />
        <SortControl value={order} onChange={handleOrder} />
      </div>
      <div className={styles.slate} data-testid="slate" data-order={order}>
        {groups.map((group) => (
          <section key={group.id} className={styles.group} data-group={group.id}>
            <SlateGroupHeader label={group.label} />
            {group.games.map((game) => (
              <Link key={game.game_id} href={`/game/${game.game_id}`} className={styles.rowLink}>
                <GameRow game={game} />
              </Link>
            ))}
          </section>
        ))}
      </div>
    </>
  );
}
