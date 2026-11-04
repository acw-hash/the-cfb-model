"""W9-2: thread + ledger from qb_known_passing card after set-qb overrides."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
ANALYSIS = ART / "analysis.json"
SITE = "https://www.ridgecfb.com"
STAKE = 0.014
UNITS = round(STAKE / 0.005, 1)
SEASON_RECORD = "21-17-1"

BOOK_DISPLAY = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "williamhill_us": "Caesars",
    "espnbet": "theScore Bet",
    "fanatics": "Fanatics",
    "hardrockbet": "Hard Rock Bet",
    "ballybet": "Bally Bet",
    "betrivers": "BetRivers",
}

# Expected card after ND/Ole Miss STARTER overrides (edge order, max 7).
EXPECT_SIDES = {
    "Auburn",
    "Navy",
    "Minnesota",
    "Florida State",
    "South Carolina",
    "Army",
    "UCF",
}


def twitter_len(text: str) -> int:
    url_re = re.compile(r"https?://\S+")
    n = 0
    pos = 0
    for m in url_re.finditer(text):
        n += len(text[pos : m.start()])
        n += 23
        pos = m.end()
    n += len(text[pos:])
    return n


def fmt_line(side: str, line: float) -> str:
    if abs(line) < 1e-9:
        return f"{side} PK"
    if line > 0:
        return f"{side} +{line:g}"
    return f"{side} {line:g}"


def day_label(kickoff_et: str) -> str:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        dt = datetime.strptime(kickoff_et[:16], "%Y-%m-%d %H:%M").replace(tzinfo=et)
        return dt.strftime("%a")
    except ValueError:
        return "?"


def plain_why(side: str, gap: float, side_is: str) -> str:
    g = abs(float(gap))
    _ = (side, side_is)
    return f"Model has this {g:.1f} points off the market."


def main() -> None:
    d = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    by_gid = {str(r["game_id"]): r for r in d["betting_rows"]}
    card = sorted(
        d["gate"]["qb_known_passing"],
        key=lambda r: float(r.get("edge") or 0),
        reverse=True,
    )
    card = card[:7]
    assert card, "empty card"

    got_sides = {str(r["side"]) for r in card}
    print("n_qb_known_pass", d["gate"]["qb_known_pass_count"])
    print("expect_sides", sorted(EXPECT_SIDES))
    print("got_sides", sorted(got_sides))
    print("missing_vs_expect", sorted(EXPECT_SIDES - got_sides))
    print("extra_vs_expect", sorted(got_sides - EXPECT_SIDES))

    posts: list[str] = []
    n = len(card)
    posts.append(
        f"week 9 card \U0001f3c8\n\n"
        f"ran every game on the slate and these are the {n} where ridge and the "
        f"books disagree the most (QBs confirmed on both sides)\n\n"
        f"1u each, {SEASON_RECORD} on the year\n\n"
        f"line + book on each one below \U0001f447"
    )
    for i, r in enumerate(card, 1):
        full = by_gid[str(r["game_id"])]
        book = str(r.get("book") or "")
        book_lab = r.get("book_display") or BOOK_DISPLAY.get(book, book)
        line = float(r["line"])
        price = float(r["price"])
        px = f"+{int(price)}" if price > 0 else str(int(price))
        model = round(float(r["model_line"]), 1)
        gap = float(r["residual"])
        day = day_label(str(r.get("kickoff_et") or ""))
        away, _, home = str(r["matchup"]).partition(" @ ")
        posts.append(
            f"Best Bet #{i}\n\n"
            f"{away} @ {home} ({day})\n"
            f"\u25b8 Market: {fmt_line(str(r['side']), line)} {px} ({book_lab})\n"
            f"\u25b8 Ridge: {fmt_line(str(r['side']), model)}\n"
            f"\u25b8 1u\n\n"
            f"{plain_why(str(r['side']), gap, str(r.get('side_is') or ''))}"
        )
    posts.append(
        "what ridge actually is:\n\n"
        "\u25b8 rates every offense + defense off play-by-play EPA, not final scores\n"
        "\u25b8 kalman filter updates them weekly, uncertainty included\n"
        "\u25b8 projects a margin + range, compares to the vig-free market\n"
        "\u25b8 only plays 4.5%+ edges with both QBs confirmed"
    )
    posts.append(
        "that's the card\n\n"
        "want the model's number on a game that's not here? reply with the "
        "matchup and i'll send it\n\n"
        f"every forecast and every result is on the site: {SITE}"
    )
    total = len(posts)
    blocks = []
    for i, p in enumerate(posts, 1):
        blocks.append(f"--- POST {i}/{total} ({twitter_len(p)} chars) ---\n{p}")
    thread = "\n".join(blocks) + "\n"
    out_dir = ROOT / "out" / "social" / "2026_w9"
    out_dir.mkdir(parents=True, exist_ok=True)
    thread_path = out_dir / "thread_w9.md"
    thread_path.write_text(thread, encoding="utf-8")
    tracked_thread_dir = ROOT / "docs" / "notes" / "_artifacts" / "w9-posted-calls"
    tracked_thread_dir.mkdir(parents=True, exist_ok=True)
    (tracked_thread_dir / "thread_w9.md").write_text(thread, encoding="utf-8")
    print("wrote", thread_path)

    calls = []
    for r in card:
        full = by_gid[str(r["game_id"])]
        calls.append(
            {
                "game_id": str(r["game_id"]),
                "matchup": r["matchup"],
                "side": r["side"],
                "side_is": r.get("side_is") or full.get("side_is"),
                "book": r["book"],
                "book_display": r.get("book_display")
                or BOOK_DISPLAY.get(str(r.get("book") or ""), r.get("book")),
                "line": float(r["line"]),
                "price": float(r["price"]),
                "snapshot_event_time": r.get("snapshot_event_time")
                or full.get("snapshot_event_time"),
                "posted_at_utc": None,
                "edge": float(r["edge"]),
                "model_line": float(r["model_line"]),
                "model_market_residual_points": float(r["residual"]),
                "stake": STAKE,
                "units": UNITS,
                "ladder_rung": full.get("ladder_rung")
                or "odds_api_snapshot_fallback+per_book_latest",
            }
        )
    ledger = {
        "season": 2026,
        "week": 9,
        "label": "posted_calls",
        "n": len(calls),
        "probe_book": "regulated_mixed",
        "pricing_note": (
            "W9-2: HFA=NONE; stake 0.014; max 7; regulated books only "
            "(draftkings/fanduel/betmgm/williamhill_us/espnbet/fanatics/"
            "hardrockbet/ballybet/betrivers); per-book latest as-of window; "
            "espnbet displayed as theScore Bet. QB overrides: Notre Dame "
            "STARTER (Carr), Ole Miss STARTER (Chambliss). "
            "posted_at_utc null (not stamped)."
        ),
        "decisions": {
            "hfa": "NONE",
            "stake": STAKE,
            "max_bets": 7,
            "weekly_cap": 0.10,
            "min_edge": 0.045,
            "max_gap": 7.0,
            "qb_overrides": {
                "Notre Dame": "starter (CJ Carr confirmed)",
                "Ole Miss": "starter (Trinidad Chambliss confirmed)",
            },
        },
        "posted_calls": calls,
    }
    text = json.dumps(ledger, indent=2) + "\n"
    social = ROOT / "data" / "social" / "posted_calls_2026_w9.json"
    tracked = tracked_thread_dir / "posted_calls_2026_w9.json"
    social.parent.mkdir(parents=True, exist_ok=True)
    social.write_text(text, encoding="utf-8")
    tracked.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print("wrote", social)
    print("wrote", tracked)
    print("ledger_sha256", digest)
    print("CARD")
    for r in calls:
        flag = (
            " GAP_NEAR_CAP"
            if float(r["model_market_residual_points"]) >= 6.5
            else ""
        )
        print(
            f"{r['matchup']} | {r['side']} | {r['book_display']} "
            f"{r['line']}/{r['price']} ridge={r['model_line']:.2f} "
            f"gap={r['model_market_residual_points']} edge={r['edge']}{flag}"
        )
    if got_sides != EXPECT_SIDES:
        raise SystemExit("card sides != expect")


if __name__ == "__main__":
    main()
