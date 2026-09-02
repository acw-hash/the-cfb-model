"""S2 — thread + reply-bank rendering and CLI (social.render / ridge_social)."""

from __future__ import annotations

import importlib.util
import json
import re
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ncaa_quant.config import BettingConfig, SocialConfig
from ncaa_quant.social.render import (
    FIXTURE_BANNER,
    POST_CHAR_LIMIT,
    dominant_rejection_reason,
    extract_post_bodies,
    fmt_interval_bound,
    fmt_line,
    join_reason_plain,
    kick_et_label,
    ordered_reply_reasons,
    render_no_bet_post,
    render_replies,
    render_thread,
)
from ncaa_quant.social.select import BestBet, select_best_bets

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ridge_social.py"


def _load_ridge_social() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ridge_social", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


FIXTURE_PREDICTIONS = ROOT / "webapp" / "fixtures" / "week_predictions.json"
FIXTURE_SIDECAR = ROOT / "tests" / "fixtures" / "social" / "w2024_w5_candidates.json"
GOLDEN_THREAD = ROOT / "tests" / "fixtures" / "social" / "golden" / "thread_w5.md"
GOLDEN_REPLIES = ROOT / "tests" / "fixtures" / "social" / "golden" / "replies_w5.md"

SITE = "https://ridge.example.com"
NOW = datetime(2024, 9, 24, 10, 0, 0, tzinfo=UTC)
RECORD = {"season_record": "0-0", "season_clv": 0.0}


def _load_fixture_week() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    wp = json.loads(FIXTURE_PREDICTIONS.read_text(encoding="utf-8"))
    side = json.loads(FIXTURE_SIDECAR.read_text(encoding="utf-8"))
    return wp, list(side["accepted"]), list(side["rejected"])


def _fixture_bets() -> tuple[list[dict[str, Any]], list[BestBet], list[dict[str, Any]]]:
    wp, accepted, rejected = _load_fixture_week()
    games = list(wp["games"])
    games_by_id = {str(g["game_id"]): g for g in games}
    bets, _ = select_best_bets(
        games_by_id,
        accepted,
        NOW,
        social=SocialConfig(),
        betting=BettingConfig(),
    )
    return games, bets, rejected


def test_golden_thread_and_replies_2024_fixture_week() -> None:
    games, bets, rejected = _fixture_bets()
    thread = render_thread(5, bets, RECORD, SITE, fixture=True)
    replies = render_replies(games, bets, rejected, SITE, fixture=True)
    assert thread == GOLDEN_THREAD.read_text(encoding="utf-8")
    assert replies == GOLDEN_REPLIES.read_text(encoding="utf-8")


def test_every_fixture_thread_post_within_280() -> None:
    _, bets, _ = _fixture_bets()
    thread = render_thread(5, bets, RECORD, SITE, fixture=True)
    bodies = extract_post_bodies(thread)
    assert bodies, "expected at least one post"
    for i, body in enumerate(bodies, 1):
        assert len(body) <= POST_CHAR_LIMIT, f"post {i} is {len(body)} chars:\n{body}"


def test_n0_renders_only_no_bet_post() -> None:
    thread = render_thread(5, [], RECORD, SITE, fixture=False)
    bodies = extract_post_bodies(thread)
    assert len(bodies) == 1
    assert "ZERO bets" in bodies[0]
    assert "Best Bet #" not in thread
    # No rejected rows → fallback mid; never invent a market claim.
    assert "market priced this slate tight" not in bodies[0]


def _rejected(reason: str) -> list[dict[str, Any]]:
    return [{"game_id": "g1", "reasons": [reason]}]


@pytest.mark.parametrize(
    ("reason", "must_contain", "must_not_contain"),
    [
        (
            "qb_status_unknown",
            "QB status is unclear",
            "market priced this slate tight",
        ),
        (
            "stale_inputs",
            "odds feed was stale",
            "market priced this slate tight",
        ),
        (
            "edge_too_small",
            "Edges that survived our filters were too small",
            "market priced this slate tight",
        ),
        (
            "non_positive_ev",
            "positive EV",
            "market priced this slate tight",
        ),
        (
            "model_market_disagree",
            "too far apart",
            "market priced this slate tight",
        ),
        (
            "no_snapshot",
            "usable odds snapshot",
            "market priced this slate tight",
        ),
    ],
)
def test_no_bet_golden_per_dominant_reason(
    reason: str,
    must_contain: str,
    must_not_contain: str,
) -> None:
    rejected = _rejected(reason)
    assert dominant_rejection_reason(rejected) == reason
    body = render_no_bet_post(1, SITE, rejected=rejected)
    assert "ZERO bets that clear our bar" in body
    assert must_contain in body
    assert must_not_contain not in body
    assert len(body) <= POST_CHAR_LIMIT

    thread = render_thread(1, [], None, SITE, rejected=rejected)
    bodies = extract_post_bodies(thread)
    assert bodies == [body]


def test_no_bet_dominant_reason_uses_primary_per_row() -> None:
    rejected = [
        {"game_id": "a", "reasons": ["qb_status_unknown", "model_market_disagree"]},
        {"game_id": "b", "reasons": ["qb_status_unknown"]},
        {"game_id": "c", "reasons": ["stale_inputs"]},
    ]
    assert dominant_rejection_reason(rejected) == "qb_status_unknown"
    body = render_no_bet_post(1, SITE, rejected=rejected)
    assert "QB status is unclear" in body
    assert "market priced this slate tight" not in body


def test_fixture_true_requires_warning_banner() -> None:
    thread = render_thread(5, [], None, SITE, fixture=True)
    replies = render_replies([], [], [], SITE, fixture=True)
    assert FIXTURE_BANNER.strip() in thread
    assert FIXTURE_BANNER.strip() in replies
    bare = render_thread(5, [], None, SITE, fixture=False)
    assert FIXTURE_BANNER.strip() not in bare


def test_stale_and_sigma_excluded_from_card_correct_in_reply_bank() -> None:
    games = [
        {
            "game_id": "stale1",
            "away_team": "Stale Away",
            "home_team": "Stale Home",
            "is_stale": True,
            "sigma_margin_credible": True,
            "kickoff_utc": "2024-09-28T19:30:00Z",
            "mu_margin": 4.0,
            "margin_interval_lo": -8.0,
            "margin_interval_hi": 16.0,
        },
        {
            "game_id": "sigma1",
            "away_team": "Sigma Away",
            "home_team": "Sigma Home",
            "is_stale": False,
            "sigma_margin_credible": False,
            "kickoff_utc": "2024-09-28T20:00:00Z",
            "mu_margin": 3.0,
            "margin_interval_lo": -5.0,
            "margin_interval_hi": 11.0,
        },
        {
            "game_id": "play1",
            "away_team": "Play Away",
            "home_team": "Play Home",
            "is_stale": False,
            "sigma_margin_credible": True,
            "kickoff_utc": "2024-09-28T21:00:00Z",
            "mu_margin": 10.0,
            "margin_interval_lo": -2.0,
            "margin_interval_hi": 22.0,
        },
    ]
    accepted = [
        {
            "game_id": "stale1",
            "market": "side",
            "side_team": "Stale Home",
            "market_line": -3.5,
            "model_line": -10.0,
            "edge": 0.08,
            "expected_value": 0.05,
            "american_odds": -110,
            "p_win": 0.6,
            "stake_fraction": 0.01,
            "model_market_residual_points": 4.0,
        },
        {
            "game_id": "sigma1",
            "market": "side",
            "side_team": "Sigma Home",
            "market_line": -2.5,
            "model_line": -9.0,
            "edge": 0.09,
            "expected_value": 0.06,
            "american_odds": -110,
            "p_win": 0.61,
            "stake_fraction": 0.01,
            "model_market_residual_points": 4.0,
        },
        {
            "game_id": "play1",
            "market": "side",
            "side_team": "Play Home",
            "market_line": -7.0,
            "model_line": -14.0,
            "edge": 0.07,
            "expected_value": 0.05,
            "american_odds": -110,
            "p_win": 0.6,
            "stake_fraction": 0.01,
            "model_market_residual_points": 4.0,
        },
    ]
    rejected = [
        {
            "game_id": "stale1",
            "market": "side",
            "side_team": "Stale Home",
            "market_line": -3.5,
            "model_line": -10.0,
            "edge": 0.08,
            "expected_value": 0.05,
            "american_odds": -110,
            "p_win": 0.6,
            "stake_fraction": 0.0,
            "model_market_residual_points": 4.0,
            "reasons": ["stale_inputs"],
        }
    ]
    games_by_id = {str(g["game_id"]): g for g in games}
    bets, skipped = select_best_bets(
        games_by_id,
        accepted,
        NOW,
        social=SocialConfig(),
        betting=BettingConfig(),
    )
    assert [b.cand["game_id"] for b in bets] == ["play1"]
    skipped_ids = {c["game_id"] for c, _ in skipped}
    assert skipped_ids == {"stale1", "sigma1"}

    replies = render_replies(games, bets, rejected, SITE)
    assert "Best Bet #1" in replies
    assert "Play Away @ Play Home" in replies
    # Stale-only: forecast with no bet rationale (staleness is a run property)
    assert "Stale Away @ Stale Home" in replies
    assert "Stale Home by 4.0" in replies
    assert "80% range: -8 to +16" in replies
    assert "stale at decision time" not in replies
    assert "No bet though" not in replies.split("## Sigma Away")[0]
    assert "Forecast ≠ edge" in replies.split("## Sigma Away")[0]
    # Sigma refused branch (not forecast-only)
    assert "Sigma Away @ Sigma Home" in replies
    assert "not enough signal" in replies
    assert "Sigma Home by" not in replies


def test_script_no_network_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Script path must never open sockets (human-in-the-loop paste only)."""

    def _deny(*_a: Any, **_k: Any) -> None:
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "socket", _deny)
    monkeypatch.setattr(socket, "create_connection", _deny)

    ridge_social = _load_ridge_social()
    out = tmp_path / "social"
    rc = ridge_social.main(
        [
            "--predictions",
            str(FIXTURE_PREDICTIONS),
            "--sidecar",
            str(FIXTURE_SIDECAR),
            "--record",
            str(tmp_path / "missing_record.json"),
            "--site-url",
            SITE,
            "--out",
            str(out),
            "--now",
            "2024-09-24T10:00:00Z",
        ]
    )
    assert rc == 0
    thread = (out / "thread_w5.md").read_text(encoding="utf-8")
    replies = (out / "replies_w5.md").read_text(encoding="utf-8")
    assert FIXTURE_BANNER.strip() in thread
    assert (out / "replies_w5.md").is_file()
    assert extract_post_bodies(thread)
    assert "# Reply bank" in replies


def test_script_fallback_separate_candidate_files(tmp_path: Path) -> None:
    ridge_social = _load_ridge_social()
    wp, accepted, rejected = _load_fixture_week()
    mini = {
        "week": 5,
        "season": 2024,
        "fixture": False,
        "games": [g for g in wp["games"] if g["game_id"] == "401628373"],
    }
    pred = tmp_path / "week_predictions.json"
    cand = tmp_path / "accepted.json"
    rej = tmp_path / "rejected.json"
    pred.write_text(json.dumps(mini), encoding="utf-8")
    cand.write_text(json.dumps(accepted[:1]), encoding="utf-8")
    rej.write_text(json.dumps(rejected[:1]), encoding="utf-8")
    out = tmp_path / "out"
    rc = ridge_social.main(
        [
            "--predictions",
            str(pred),
            "--candidates",
            str(cand),
            "--rejected",
            str(rej),
            "--out",
            str(out),
            "--now",
            "2024-09-24T10:00:00Z",
            "--site-url",
            SITE,
        ]
    )
    assert rc == 0
    thread = (out / "thread_w5.md").read_text(encoding="utf-8")
    assert FIXTURE_BANNER.strip() not in thread
    assert "Best Bet #1" in thread


def test_fmt_line_and_kick_label_helpers() -> None:
    assert fmt_line(-3.5) == "-3.5"
    assert fmt_line("x") == "?"
    assert fmt_interval_bound(-10.6196) == "-11"
    assert fmt_interval_bound(45.7377) == "+46"
    assert fmt_interval_bound("x") == "?"
    assert kick_et_label(None) == ""
    assert kick_et_label("not-iso") == ""
    assert kick_et_label("2024-09-28T19:30:00Z") == "Sat"


def _forecast_game(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "game_id": "g1",
        "away_team": "Away U",
        "home_team": "Home U",
        "kickoff_utc": "2024-09-28T19:30:00Z",
        "mu_margin": 10.0,
        "sigma_margin_credible": True,
        "margin_interval_lo": -10.6196,
        "margin_interval_hi": 45.7377,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("reasons", "must_contain", "must_not_contain"),
    [
        (
            ["qb_status_unknown"],
            ["QB situation is unclear", "No bet though"],
            ["stale at decision time", "priced about right", "so far apart"],
        ),
        (
            ["model_market_disagree"],
            ["so far apart", "No bet though"],
            ["QB situation", "stale at decision time"],
        ),
        (
            ["stale_inputs"],
            ["Home U by 10.0", "80% range: -11 to +46", "Forecast ≠ edge"],
            ["No bet though", "stale at decision time"],
        ),
        (
            ["edge_too_small"],
            ["priced about right", "No bet though"],
            ["QB situation", "stale at decision time"],
        ),
        (
            ["non_positive_ev"],
            ["no positive EV", "No bet though"],
            ["QB situation"],
        ),
        (
            ["qb_status_unknown", "model_market_disagree"],
            [
                "QB situation is unclear",
                "so far apart",
                "No bet though",
            ],
            ["stale at decision time"],
        ),
        (
            ["stale_inputs", "qb_status_unknown"],
            [
                "QB situation is unclear",
                "stale at decision time",
                "No bet though",
            ],
            ["so far apart"],
        ),
        (
            ["stale_inputs", "model_market_disagree"],
            [
                "so far apart",
                "stale at decision time",
                "No bet though",
            ],
            ["QB situation"],
        ),
        (
            ["stale_inputs", "qb_status_unknown", "model_market_disagree"],
            [
                "QB situation is unclear",
                "so far apart",
                "stale at decision time",
                "No bet though",
            ],
            ["priced about right"],
        ),
        (
            ["model_market_disagree", "stale_inputs", "qb_status_unknown"],
            [
                "QB situation is unclear",
                "so far apart",
                "stale at decision time",
            ],
            [],
        ),
    ],
)
def test_forecast_reply_golden_per_reason_combination(
    reasons: list[str],
    must_contain: list[str],
    must_not_contain: list[str],
) -> None:
    games = [_forecast_game()]
    rejected = [{"game_id": "g1", "reasons": reasons}]
    replies = render_replies(games, [], rejected, SITE)
    body = replies.split("## Away U @ Home U", 1)[1].split("## Game not", 1)[0]
    for needle in must_contain:
        assert needle in body, f"missing {needle!r} in:\n{body}"
    for needle in must_not_contain:
        assert needle not in body, f"unexpected {needle!r} in:\n{body}"
    # Multi-reason: QB before disagree before stale (reader order)
    if "qb_status_unknown" in reasons and "model_market_disagree" in reasons:
        assert body.index("QB situation") < body.index("so far apart")
    if "qb_status_unknown" in reasons and "stale_inputs" in reasons and "No bet though" in body:
        assert body.index("QB situation") < body.index("stale at decision time")
    if "model_market_disagree" in reasons and "stale_inputs" in reasons and "No bet though" in body:
        assert body.index("so far apart") < body.index("stale at decision time")


def test_ordered_reply_reasons_and_join() -> None:
    assert ordered_reply_reasons(
        ["stale_inputs", "pass", "qb_status_unknown", "stale_inputs", "model_market_disagree"]
    ) == ["qb_status_unknown", "model_market_disagree", "stale_inputs"]
    joined = join_reason_plain(["stale_inputs", "qb_status_unknown"])
    assert joined.startswith("QB situation")
    assert "stale at decision time" in joined
    assert "QB situation" in joined.split(";")[0]


def test_variable_count_n_posts_match_n_bets() -> None:
    games, bets, _ = _fixture_bets()
    thread = render_thread(5, bets, RECORD, SITE, fixture=True)
    bodies = extract_post_bodies(thread)
    # hook + N bets + methodology + CTA
    assert len(bodies) == len(bets) + 3
    bet_posts = [b for b in bodies if b.startswith("Best Bet #")]
    assert len(bet_posts) == len(bets)
    replies = render_replies(games, bets, [], SITE)
    headers = re.findall(r"^## .+", replies, flags=re.M)
    # +1 for the "Game not in the slate" macro
    assert len(headers) == len(games) + 1
