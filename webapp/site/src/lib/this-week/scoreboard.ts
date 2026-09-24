import type { ConvictionTier } from "@/lib/artifacts/types";
import {
  formatConsensusSpreadBook,
  formatMarketWinChance,
  formatUnsignedMarketMargin,
} from "@/lib/formatting/market-margin";
import { formatProbability } from "@/lib/formatting/numbers";
import {
  favoredSideFromMargin,
  favoredWinProbability,
  formatUnsignedMargin,
  type FavoredSide,
} from "@/lib/formatting/team-margin";
import { shortTierAria, shortTierWord } from "@/lib/formatting/tier-copy";
import { formatKickoffTimeOnly } from "@/lib/formatting/time";

export type ScoreboardLineId = "away" | "home" | "mid";

export interface ScoreboardLineFigures {
  margin: string | null;
  win: string | null;
}

/**
 * Placement for one numeric group (model or market).
 * - `away` / `home`: figures on that team line
 * - `mid`: PK (exact 0) centered between lines
 * - `nullDash`: show "—" on the home line only (null / absent)
 */
export interface ScoreboardGroupPlacement {
  away: ScoreboardLineFigures;
  home: ScoreboardLineFigures;
  mid: ScoreboardLineFigures | null;
  /** True when the source is null — home line gets an empty-cell dash. */
  nullDash: boolean;
}

export interface ScoreboardPlacement {
  model: ScoreboardGroupPlacement;
  market: ScoreboardGroupPlacement | null;
  modelSide: FavoredSide | null;
  tierWord: string | null;
  tierSide: FavoredSide | null;
  tierCentered: boolean;
}

function emptyLine(): ScoreboardLineFigures {
  return { margin: null, win: null };
}

function placeGroup(
  unsigned: string | null,
  winLabel: string | null,
  side: FavoredSide | null,
): ScoreboardGroupPlacement {
  if (unsigned == null) {
    return {
      away: emptyLine(),
      home: emptyLine(),
      mid: null,
      nullDash: true,
    };
  }
  if (unsigned === "PK") {
    return {
      away: emptyLine(),
      home: emptyLine(),
      mid: { margin: "PK", win: winLabel },
      nullDash: false,
    };
  }
  const away = emptyLine();
  const home = emptyLine();
  if (side === "away") {
    away.margin = unsigned;
    away.win = winLabel;
  } else {
    // Default home when side is home or unexpectedly null with a magnitude.
    home.margin = unsigned;
    home.win = winLabel;
  }
  return { away, home, mid: null, nullDash: false };
}

export interface BuildScoreboardArgs {
  muMargin: number | null | undefined;
  sigmaMargin?: number | null;
  pWinHome: number | null | undefined;
  pWinHomeCredible: boolean;
  sigmaMarginCredible: boolean;
  marketHomeMargin?: number | null;
  pWinHomeMarket?: number | null;
  convictionTier: ConvictionTier | null;
  showMarket: boolean;
}

/** Compute where model / market / tier figures land on the two-line stack. */
export function buildScoreboardPlacement(args: BuildScoreboardArgs): ScoreboardPlacement {
  const modelSide = favoredSideFromMargin(args.muMargin, args.pWinHome);
  const modelUnsigned = formatUnsignedMargin(args.muMargin, args.sigmaMargin ?? null);
  const modelWinRaw =
    args.sigmaMarginCredible && args.pWinHomeCredible
      ? favoredWinProbability(args.muMargin, args.pWinHome)
      : null;
  const modelWin = formatProbability(modelWinRaw);
  const model = placeGroup(modelUnsigned, modelWin, modelSide);

  let market: ScoreboardGroupPlacement | null = null;
  if (args.showMarket) {
    const marketSide = favoredSideFromMargin(args.marketHomeMargin, args.pWinHomeMarket);
    const marketUnsigned = formatUnsignedMarketMargin(args.marketHomeMargin);
    const marketWinRaw =
      args.pWinHomeMarket != null && Number.isFinite(args.pWinHomeMarket)
        ? formatMarketWinChance(args.marketHomeMargin, args.pWinHomeMarket)
        : null;
    // formatMarketWinChance returns "—" for missing; treat as null for placement.
    const marketWin = marketWinRaw === "—" ? null : marketWinRaw;
    market = placeGroup(marketUnsigned, marketWin, marketSide);
  }

  const tierWord = shortTierWord(args.convictionTier);
  const tierCentered = args.convictionTier === "toss_up";
  const tierSide = tierCentered ? null : modelSide;

  return {
    model,
    market,
    modelSide,
    tierWord,
    tierSide,
    tierCentered,
  };
}

export interface BuildRowAriaArgs {
  awayTeam: string;
  homeTeam: string;
  kickoffUtc: string | null | undefined;
  timeZone?: string;
  muMargin: number | null | undefined;
  sigmaMargin?: number | null;
  pWinHome: number | null | undefined;
  pWinHomeCredible: boolean;
  sigmaMarginCredible: boolean;
  convictionTier: ConvictionTier | null;
  marketHomeMargin?: number | null;
  pWinHomeMarket?: number | null;
  spreadHomePoints?: number | null;
  showMarket: boolean;
}

/** One-sentence aria-label for a scoreboard row. */
export function buildGameRowAriaLabel(args: BuildRowAriaArgs): string {
  const time = formatKickoffTimeOnly(args.kickoffUtc, args.timeZone).local;
  const parts: string[] = [`${args.awayTeam} at ${args.homeTeam}, ${time}.`];

  const modelSide = favoredSideFromMargin(args.muMargin, args.pWinHome);
  const modelMag = formatUnsignedMargin(args.muMargin, args.sigmaMargin ?? null);
  const modelWin =
    args.sigmaMarginCredible && args.pWinHomeCredible
      ? formatProbability(favoredWinProbability(args.muMargin, args.pWinHome))
      : null;
  const tierAria = shortTierAria(args.convictionTier);

  if (modelMag == null) {
    parts.push("Model: unavailable.");
  } else if (modelMag === "PK") {
    const winBit = modelWin ? `, ${modelWin}` : "";
    const tierBit = tierAria ? `, ${tierAria}` : "";
    parts.push(`Model: pick'em${winBit}${tierBit}.`);
  } else {
    const team = modelSide === "away" ? args.awayTeam : args.homeTeam;
    const winBit = modelWin ? `, ${modelWin}` : "";
    const tierBit = tierAria ? `, ${tierAria}` : "";
    parts.push(`Model: ${team} by ${modelMag}${winBit}${tierBit}.`);
  }

  if (args.showMarket) {
    const marketSide = favoredSideFromMargin(args.marketHomeMargin, args.pWinHomeMarket);
    const marketMag = formatUnsignedMarketMargin(args.marketHomeMargin);
    const marketWinRaw = formatMarketWinChance(args.marketHomeMargin, args.pWinHomeMarket);
    const marketWin = marketWinRaw === "—" ? null : marketWinRaw;
    if (marketMag == null) {
      parts.push("Market: unavailable.");
    } else if (marketMag === "PK") {
      const winBit = marketWin ? `, ${marketWin}` : "";
      parts.push(`Market: pick'em${winBit}.`);
    } else {
      const team = marketSide === "away" ? args.awayTeam : args.homeTeam;
      const winBit = marketWin ? `, ${marketWin}` : "";
      parts.push(`Market: ${team} by ${marketMag}${winBit}.`);
    }
    const book = formatConsensusSpreadBook(args.spreadHomePoints, args.homeTeam);
    if (book) {
      parts.push(`Consensus spread: ${book}.`);
    }
  }

  return parts.join(" ");
}
