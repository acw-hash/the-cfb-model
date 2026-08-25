"""Persist bet candidates to a local social sidecar (S1).

Workstation filesystem only — never written under ``v*/`` or ``latest/`` on R2,
and never mixed into webapp artifact schemas.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ncaa_quant.betting.filters import BetCandidate, FilterReason
from ncaa_quant.betting.kelly import recommended_stake
from ncaa_quant.config import AppConfig, BettingConfig, SocialConfig, load_config

BetOn = Literal["home", "away", "over", "under"]
MarketKind = Literal["side", "total"]

_ORIENTATION_KEYS = (
    "home_team",
    "away_team",
    "bet_on",
    "market_line_home",
    "model_line_home",
    "american_odds",
    "p_win",
)


class SocialCandidateError(ValueError):
    """Missing or invalid fields for a social CandidateRecord."""


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """One bet candidate ready for the social generator (oriented lines).

    ``side_team`` / ``market_line`` / ``model_line`` are from the bet-side
    perspective. Home-anchored inputs are oriented via :func:`orient_bet_lines`
    (CFBD designated home, same convention as ``filter_home_side_spreads``).
    """

    game_id: str
    market: MarketKind
    side_team: str
    market_line: float
    model_line: float
    edge: float
    expected_value: float
    american_odds: float
    p_win: float
    stake_fraction: float
    model_market_residual_points: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def orient_bet_lines(
    *,
    market: MarketKind,
    home_team: str,
    away_team: str,
    bet_on: BetOn,
    market_line_home: float,
    model_line_home: float,
) -> tuple[str, float, float]:
    """Derive ``(side_team, market_line, model_line)`` from home-anchored inputs.

    Parameters
    ----------
    market:
        ``side`` (spread) or ``total``.
    home_team / away_team:
        CFBD designated home / away (not Odds listing home).
    bet_on:
        Which side of the market the candidate bets.
    market_line_home / model_line_home:
        Lines oriented to the CFBD home team (spread: home points; total: the
        shared number). Away bets negate both spread lines.
    """
    if market == "total":
        if bet_on not in ("over", "under"):
            raise SocialCandidateError(f"totals bet_on must be 'over' or 'under', got {bet_on!r}")
        side = "Over" if bet_on == "over" else "Under"
        return side, float(market_line_home), float(model_line_home)

    if bet_on == "home":
        return str(home_team), float(market_line_home), float(model_line_home)
    if bet_on == "away":
        return str(away_team), float(-market_line_home), float(-model_line_home)
    raise SocialCandidateError(f"side bet_on must be 'home' or 'away', got {bet_on!r}")


def build_candidate_record(
    *,
    game_id: str,
    market: MarketKind,
    edge: float,
    expected_value: float,
    model_market_residual_points: float,
    american_odds: float,
    p_win: float,
    home_team: str,
    away_team: str,
    bet_on: BetOn,
    market_line_home: float,
    model_line_home: float,
    betting: BettingConfig,
    bankroll: float = 1.0,
) -> CandidateRecord:
    """Build a :class:`CandidateRecord` with oriented lines and Kelly stake."""
    side_team, market_line, model_line = orient_bet_lines(
        market=market,
        home_team=home_team,
        away_team=away_team,
        bet_on=bet_on,
        market_line_home=market_line_home,
        model_line_home=model_line_home,
    )
    stake = recommended_stake(
        float(p_win),
        float(american_odds),
        float(bankroll),
        betting,
    )
    return CandidateRecord(
        game_id=str(game_id),
        market=market,
        side_team=side_team,
        market_line=market_line,
        model_line=model_line,
        edge=float(edge),
        expected_value=float(expected_value),
        american_odds=float(american_odds),
        p_win=float(p_win),
        stake_fraction=float(stake.stake_fraction),
        model_market_residual_points=float(model_market_residual_points),
    )


def _detail_key(game_id: str, market: str) -> str:
    return f"{game_id}:{market}"


def _record_from_mapping(
    raw: Mapping[str, Any],
    *,
    betting: BettingConfig,
) -> CandidateRecord:
    """Normalize a draft or complete candidate mapping into a CandidateRecord."""
    if all(k in raw for k in ("side_team", "market_line", "model_line", "stake_fraction")):
        market = raw["market"]
        if market not in ("side", "total"):
            raise SocialCandidateError(f"invalid market: {market!r}")
        return CandidateRecord(
            game_id=str(raw["game_id"]),
            market=market,
            side_team=str(raw["side_team"]),
            market_line=float(raw["market_line"]),
            model_line=float(raw["model_line"]),
            edge=float(raw["edge"]),
            expected_value=float(raw["expected_value"]),
            american_odds=float(raw["american_odds"]),
            p_win=float(raw["p_win"]),
            stake_fraction=float(raw["stake_fraction"]),
            model_market_residual_points=float(raw["model_market_residual_points"]),
        )

    missing = [k for k in _ORIENTATION_KEYS if k not in raw]
    if missing:
        raise SocialCandidateError(
            f"candidate {raw.get('game_id')!r} missing orientation fields: {missing}"
        )
    market = raw["market"]
    if market not in ("side", "total"):
        raise SocialCandidateError(f"invalid market: {market!r}")
    bet_on = raw["bet_on"]
    if bet_on not in ("home", "away", "over", "under"):
        raise SocialCandidateError(f"invalid bet_on: {bet_on!r}")
    return build_candidate_record(
        game_id=str(raw["game_id"]),
        market=market,
        edge=float(raw["edge"]),
        expected_value=float(raw["expected_value"]),
        model_market_residual_points=float(raw["model_market_residual_points"]),
        american_odds=float(raw["american_odds"]),
        p_win=float(raw["p_win"]),
        home_team=str(raw["home_team"]),
        away_team=str(raw["away_team"]),
        bet_on=bet_on,
        market_line_home=float(raw["market_line_home"]),
        model_line_home=float(raw["model_line_home"]),
        betting=betting,
    )


def _record_from_bet(
    cand: BetCandidate,
    detail: Mapping[str, Any],
    *,
    betting: BettingConfig,
) -> CandidateRecord:
    merged: dict[str, Any] = {
        "game_id": cand.game_id,
        "market": cand.market,
        "edge": cand.edge,
        "expected_value": cand.expected_value,
        "model_market_residual_points": cand.model_market_residual_points,
        **dict(detail),
    }
    return _record_from_mapping(merged, betting=betting)


def records_from_filter_result(
    accepted: Sequence[BetCandidate],
    rejected: Sequence[tuple[BetCandidate, tuple[FilterReason, ...]]],
    *,
    details: Mapping[str, Mapping[str, Any]] | None = None,
    betting: BettingConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert filter output to CandidateRecord dicts (rejected include reasons).

    ``details`` is keyed by ``\"{game_id}:{market}\"`` and must supply orientation
    inputs (or already-oriented CandidateRecord fields) for every candidate.
    Empty accepted/rejected lists require no details.
    """
    detail_map = details or {}
    out_accepted: list[dict[str, Any]] = []
    for cand in accepted:
        key = _detail_key(cand.game_id, cand.market)
        if key not in detail_map:
            raise SocialCandidateError(
                f"missing social orientation details for accepted candidate {key}"
            )
        out_accepted.append(_record_from_bet(cand, detail_map[key], betting=betting).to_dict())

    out_rejected: list[dict[str, Any]] = []
    for cand, reasons in rejected:
        key = _detail_key(cand.game_id, cand.market)
        if key not in detail_map:
            raise SocialCandidateError(
                f"missing social orientation details for rejected candidate {key}"
            )
        rec = _record_from_bet(cand, detail_map[key], betting=betting).to_dict()
        # Machine-readable FilterReason values only (no prose).
        rec["reasons"] = [r.value if isinstance(r, FilterReason) else str(r) for r in reasons]
        out_rejected.append(rec)

    return out_accepted, out_rejected


def candidates_sidecar_path(
    social: SocialConfig,
    *,
    season: int,
    week: int,
    refresh_kind: str,
) -> Path:
    """``{output_dir}/{season}/w{week}/{refresh_kind}/candidates.json``."""
    return (
        Path(social.output_dir)
        / str(int(season))
        / f"w{int(week)}"
        / str(refresh_kind)
        / "candidates.json"
    )


def export_social_candidates(
    publish_result: Mapping[str, Any],
    config: AppConfig | None = None,
) -> Path | None:
    """Write the local social candidates sidecar. No-op when social is disabled.

    Expects ``publish_result["accepted"]`` / ``["rejected"]`` to be lists of
    CandidateRecord-shaped (or orientation-draft) dicts. Rejected entries carry
    ``reasons`` as :class:`~ncaa_quant.betting.filters.FilterReason` values.
    """
    cfg = config or load_config()
    if not cfg.social.enabled:
        return None

    season = int(publish_result["season"])
    week = int(publish_result["week"])
    refresh_kind = str(publish_result["refresh_kind"])

    accepted_raw = list(publish_result.get("accepted") or [])
    rejected_raw = list(publish_result.get("rejected") or [])

    accepted_out: list[dict[str, Any]] = [
        _record_from_mapping(row, betting=cfg.betting).to_dict() for row in accepted_raw
    ]
    rejected_out: list[dict[str, Any]] = []
    for row in rejected_raw:
        reasons = row.get("reasons")
        if reasons is None:
            raise SocialCandidateError("rejected candidate missing reasons")
        body = {k: v for k, v in row.items() if k != "reasons"}
        rec = _record_from_mapping(body, betting=cfg.betting).to_dict()
        rec["reasons"] = [r.value if isinstance(r, FilterReason) else str(r) for r in reasons]
        rejected_out.append(rec)

    published_at = publish_result.get("published_at")
    if published_at is None:
        published_at = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    elif isinstance(published_at, datetime):
        published_at = (
            published_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if published_at.tzinfo
            else published_at.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
        )

    payload: dict[str, Any] = {
        "season": season,
        "week": week,
        "refresh_kind": refresh_kind,
        "published_at": published_at,
        "fixture": bool(publish_result.get("fixture", False)),
        "accepted": accepted_out,
        "rejected": rejected_out,
    }

    path = candidates_sidecar_path(cfg.social, season=season, week=week, refresh_kind=refresh_kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
