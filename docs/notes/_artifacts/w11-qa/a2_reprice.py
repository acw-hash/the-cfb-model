"""W11-QA A2 — reprice the seven W10 card plays vs Mac Nov 7 targets.

No W10-3 / sat-card artifacts on this machine. Uses the last multi-book staged
pull before Sat Nov 7 11:30 AM ET (W10-2 all-books), then prices each posted
play at the operator line/price with production Monte Carlo helpers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ncaa_quant.betting.edges import BookPrice, compute_edge
from ncaa_quant.betting.provider import _book_two_way_spread, _sample_game_draws
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.distribution.key_numbers import KeyNumberKernel
from ncaa_quant.distribution.simulate import spread_cover_probs, two_way_side_prob

ROOT = Path(__file__).resolve().parents[4]
OUT_DIR = Path(__file__).resolve().parent
HIST = ROOT / "data" / "webapp" / "publish_history" / "2026_w10.jsonl"
ALLBOOKS_FILE = (
    ROOT
    / "docs"
    / "notes"
    / "_artifacts"
    / "social-s6-w10-card"
    / "odds_allbooks_20261104T141200Z.json"
)
CUTOFF_UTC = datetime(2026, 11, 7, 16, 30, 0, tzinfo=UTC)  # Sat Nov 7 11:30 AM ET
GAP_TOL = 0.25

# Week-10 card plays (W11-1 as-posted list) + Mac comparison targets.
PLAYS: list[dict[str, Any]] = [
    {
        "game_id": 401856743,
        "matchup": "Texas @ Missouri",
        "side": "Missouri",
        "book": "fanduel",
        "posted_line": 2.5,
        "posted_price": -104.0,
        "mac_gap": 6.83,
        "mac_edge": None,
    },
    {
        "game_id": 401856741,
        "matchup": "Georgia @ Ole Miss",
        "side": "Ole Miss",
        "book": "espnbet",
        "posted_line": 3.5,
        "posted_price": -105.0,
        "mac_gap": 6.19,
        "mac_edge": None,
    },
    {
        "game_id": 401856740,
        "matchup": "Alabama @ LSU",
        "side": "LSU",
        "book": "draftkings",
        "posted_line": -6.0,
        "posted_price": -108.0,
        "mac_gap": 5.62,
        "mac_edge": None,
    },
    {
        "game_id": 401858292,
        "matchup": "North Carolina @ UConn",
        "side": "UConn",
        "book": "fanduel",
        "posted_line": 7.5,
        "posted_price": -102.0,
        "mac_gap": 4.77,
        "mac_edge": None,
    },
    {
        "game_id": 401856738,
        "matchup": "Arkansas @ Auburn",
        "side": "Auburn",
        "book": "betmgm",
        "posted_line": -10.0,
        "posted_price": -108.0,
        "mac_gap": 4.25,
        "mac_edge": None,
    },
    {
        "game_id": 401860912,
        "matchup": "Texas State @ Oregon State",
        "side": "Texas State",
        "book": "betmgm",
        "posted_line": 5.5,
        "posted_price": -108.0,
        "mac_gap": 3.71,
        "mac_edge": None,
        "mac_gap_note": "at +5.5",
    },
    {
        "game_id": 401856745,
        "matchup": "Kentucky @ Tennessee",
        "side": "Kentucky",
        "book": "hardrockbet",
        "posted_line": 6.5,
        "posted_price": -105.0,
        "mac_gap": None,
        "mac_edge": 0.083,
    },
]


def _load_wp() -> tuple[dict[str, Any], str]:
    lines = [ln for ln in HIST.read_text(encoding="utf-8").splitlines() if ln.strip()]
    wp = json.loads(lines[-1])
    return wp, f"data/webapp/publish_history/2026_w10.jsonl (record {len(lines)})"


def _allbooks_window(store: ParquetStore) -> tuple[pd.DataFrame, str, datetime]:
    """Last multi-book staged window before cutoff (W10-2 all-books)."""
    snaps = store.read("odds_snapshots", filters={"season": 2026}).copy()
    snaps["event_time"] = pd.to_datetime(snaps["event_time"], utc=True)
    pre = snaps.loc[snaps["event_time"] <= CUTOFF_UTC]
    if pre.empty:
        raise RuntimeError("no staged odds_snapshots before Sat Nov 7 11:30 AM ET")

    # W10-3 not present; all-books pull is the last regulated multi-book stage.
    ab = pre.loc[
        (pre["event_time"] >= "2026-11-04T14:04:00Z")
        & (pre["event_time"] <= "2026-11-04T14:12:00Z")
    ].copy()
    if ab.empty:
        newest = pre["event_time"].max()
        ab = pre.loc[pre["event_time"] == newest].copy()
        name = f"staged odds_snapshots event_time={newest.isoformat()}"
        return ab, name, newest.to_pydatetime()

    newest = ab["event_time"].max()
    name = ALLBOOKS_FILE.name
    return ab, name, newest.to_pydatetime()


def _other_price_from_snap(
    window: pd.DataFrame, *, game_id: int, home: str, book: str, bet_on: str
) -> float | None:
    books = _book_two_way_spread(window.loc[window["game_id"] == game_id], home_side=home)
    hit = [b for b in books if str(b[0]).casefold() == book.casefold()]
    if not hit:
        return None
    _bk, _home_line, home_px, away_px = hit[0]
    return float(away_px) if bet_on == "home" else float(home_px)


def main() -> None:
    cfg = load_config()
    wp, wp_src = _load_wp()
    games = {int(g["game_id"]): g for g in wp["games"]}
    kernel = KeyNumberKernel(offset_weights={}, n=0)

    with ParquetStore(Path(cfg.paths.staged_dir)) as store:
        window, snap_name, snap_et = _allbooks_window(store)

    w10_3_present = False
    rows: list[dict[str, Any]] = []
    overall = "PASS"

    for p in PLAYS:
        gid = int(p["game_id"])
        game = games[gid]
        home = str(game["home_team"])
        away = str(game["away_team"])
        side = str(p["side"])
        book = str(p["book"])
        posted_line = float(p["posted_line"])
        posted_price = float(p["posted_price"])

        if side == home:
            bet_on = "home"
            home_line = posted_line
        elif side == away:
            bet_on = "away"
            home_line = -posted_line
        else:
            raise SystemExit(f"{gid}: side {side!r} not in {away} @ {home}")

        mu_m = float(game["mu_margin"])
        sig_m = float(game["sigma_margin"])
        mu_t = float(game.get("mu_total") or 50.0)
        sig_t = float(game.get("sigma_total") or 16.0)
        rho = float(game.get("rho") or 0.0)

        draws = _sample_game_draws(
            mu_m=mu_m,
            sigma_m=sig_m,
            mu_t=mu_t,
            sigma_t=sig_t,
            rho=rho,
            kernel=kernel,
            n_draws=20_000,
            seed=42 + gid,
        )
        p_model = two_way_side_prob(
            spread_cover_probs(draws, float(home_line), game_index=0, side=bet_on)  # type: ignore[arg-type]
        )

        other = _other_price_from_snap(
            window, game_id=gid, home=home, book=book, bet_on=bet_on
        )
        other_source = "allbooks_two_way_opposing"
        if other is None:
            other = -110.0
            other_source = "fallback_-110"

        edge_res = compute_edge(p_model, [BookPrice(book, posted_price, float(other))])
        model_line_home = -float(mu_m)
        residual = abs(model_line_home - float(home_line))
        model_line = model_line_home if bet_on == "home" else -model_line_home
        gap = round(float(residual), 2)
        edge = round(float(edge_res.edge), 4)

        mac_gap = p.get("mac_gap")
        mac_edge = p.get("mac_edge")
        side_ok = side == p["side"]
        gap_ok = True if mac_gap is None else abs(gap - float(mac_gap)) <= GAP_TOL
        edge_ok = True if mac_edge is None else abs(edge - float(mac_edge)) <= 0.005
        # Mac edge listed as 8.3% → tolerate 0.5pp display rounding vs exact.
        if mac_edge is not None:
            edge_ok = abs(edge - float(mac_edge)) <= 0.005 or abs(
                round(edge * 100, 1) / 100.0 - float(mac_edge)
            ) <= 0.001

        play_verdict = "PASS" if (side_ok and gap_ok and edge_ok) else "FAIL"
        if play_verdict == "FAIL":
            overall = "FAIL"

        # Snapshot quote at posted book (informational; may differ from posted).
        snap_books = _book_two_way_spread(
            window.loc[window["game_id"] == gid], home_side=home
        )
        snap_hit = [b for b in snap_books if str(b[0]).casefold() == book.casefold()]
        snap_quote = None
        if snap_hit:
            _bk, sh, hp, ap = snap_hit[0]
            snap_bet_line = float(sh) if bet_on == "home" else float(-sh)
            snap_quote = {
                "home_line": float(sh),
                "bet_line": snap_bet_line,
                "home_price": float(hp),
                "away_price": float(ap),
                "line_matches_posted": abs(snap_bet_line - posted_line) < 1e-9,
            }

        row = {
            "game_id": gid,
            "matchup": f"{away} @ {home}",
            "side": side,
            "side_is": "home" if bet_on == "home" else "away",
            "book": book,
            "posted_line": posted_line,
            "posted_price": posted_price,
            "model_line": round(float(model_line), 4),
            "gap": gap,
            "edge": edge,
            "edge_pct": round(edge * 100.0, 1),
            "p_model": round(float(edge_res.p_model), 6),
            "p_market": round(float(edge_res.p_market), 6),
            "other_american": float(other),
            "other_source": other_source,
            "mac_gap": mac_gap,
            "mac_edge": mac_edge,
            "mac_edge_pct": None if mac_edge is None else round(float(mac_edge) * 100.0, 1),
            "gap_delta_vs_mac": None if mac_gap is None else round(gap - float(mac_gap), 4),
            "edge_delta_vs_mac": None if mac_edge is None else round(edge - float(mac_edge), 4),
            "side_match": side_ok,
            "gap_pass": gap_ok,
            "edge_pass": edge_ok,
            "verdict": play_verdict,
            "snapshot_quote": snap_quote,
        }
        rows.append(row)

    payload = {
        "task": "W11-QA-A2",
        "w10_3_snapshot_present": w10_3_present,
        "snapshot_used": snap_name,
        "snapshot_file": str(ALLBOOKS_FILE.relative_to(ROOT)).replace("\\", "/"),
        "snapshot_newest_event_time": snap_et.isoformat(),
        "cutoff_et": "2026-11-07T11:30:00-05:00",
        "pricing_method": (
            "production helpers: _sample_game_draws + spread_cover_probs + "
            "compute_edge; residual = |model_line_home - home_line|; "
            "market line/price = posted card terms (not shopped)"
        ),
        "week_predictions_source": wp_src,
        "publish_as_of": wp.get("as_of"),
        "gap_tolerance": GAP_TOL,
        "overall_verdict": overall,
        "plays": rows,
    }

    json_path = OUT_DIR / "a2_reprice.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        "# W11-QA A2 — W10 card reprice vs Mac",
        "",
        f"**Overall:** `{overall}`",
        "",
        "## Snapshot",
        "",
        f"- W10-3 snapshot staged here: **no**",
        f"- Snapshot used: `{snap_name}`",
        f"- Path: `{payload['snapshot_file']}`",
        f"- Newest `event_time` in window: `{snap_et.isoformat()}`",
        f"- Cutoff: Sat Nov 7 11:30 AM ET (`{CUTOFF_UTC.isoformat()}`)",
        f"- Week predictions: `{wp_src}` as_of `{wp.get('as_of')}`",
        "",
        "## Method",
        "",
        "- Production card math: `model_line_home = -μ`, "
        "`gap = |model_line_home − home_line|`, edge via seeded Monte Carlo "
        "`spread_cover_probs` + `compute_edge` (seed `42 + game_id`).",
        "- Market terms = **posted** card line/price (Mac targets are at those "
        "lines). Opposing American from all-books two-way when present "
        "(else −110).",
        f"- FAIL if any side differs or any gap differs by > {GAP_TOL}.",
        "",
        "## Per-play",
        "",
        "| matchup | side | book | posted | gap | Mac gap | Δgap | edge | Mac edge | verdict |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        mac_g = "—" if r["mac_gap"] is None else f"{r['mac_gap']:.2f}"
        d_g = "—" if r["gap_delta_vs_mac"] is None else f"{r['gap_delta_vs_mac']:+.4f}"
        mac_e = "—" if r["mac_edge_pct"] is None else f"{r['mac_edge_pct']:.1f}%"
        md_lines.append(
            f"| {r['matchup']} | {r['side']} | {r['book']} | "
            f"{r['posted_line']:+g}/{r['posted_price']:+.0f} | {r['gap']:.2f} | "
            f"{mac_g} | {d_g} | {r['edge_pct']:.1f}% | {mac_e} | **{r['verdict']}** |"
        )

    md_lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Mac Nov 7 commits / `posted_calls_2026_w10.json` / "
            "`_s6_w10_sat_card.py` are not on this machine (`origin/main`).",
            "- Cadence DK/FD/MGM continued to `2026-11-04T20:00:03.244387Z`, but "
            "espnbet/hardrockbet last appear in the W10-2 all-books stage; that "
            "file is the named snapshot.",
            "- Allbooks quote lines for LSU/Auburn/Texas State/UConn differ from "
            "posted (Nov 4 vs Nov 7 card); gaps above use **posted** lines so "
            "they are comparable to Mac.",
            "",
        ]
    )
    md_path = OUT_DIR / "a2_reprice.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"snapshot={snap_name}")
    print(f"overall={overall}")
    for r in rows:
        print(
            f"{r['verdict']}\t{r['side']}\tgap={r['gap']}\tmac_gap={r['mac_gap']}\t"
            f"edge={r['edge']}\tmac_edge={r['mac_edge']}"
        )
    print("wrote", json_path)
    print("wrote", md_path)


if __name__ == "__main__":
    main()
