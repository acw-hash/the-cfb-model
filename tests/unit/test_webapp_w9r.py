"""W9-R: restamp literals are the amended 23-reval memo; Phase 1 fixture gates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ncaa_quant.webapp.export import (
    SCHEMA_VERSION,
    TRACK_RECORD_VINTAGE_LABEL,
    WITHDRAWN_FIELDS,
    build_track_record,
)

ROOT = Path(__file__).resolve().parents[2]
MEMO = ROOT / "docs" / "notes" / "23-reval.md"
FIXTURE_DIR = ROOT / "webapp" / "fixtures"
COPY_TS = ROOT / "webapp" / "site" / "src" / "lib" / "results" / "copy.ts"
ABOUT_COPY = ROOT / "webapp" / "site" / "src" / "lib" / "about" / "copy.ts"
EXPORT_PY = ROOT / "src" / "ncaa_quant" / "webapp" / "export.py"

# Current published figures — W9-G / Amendment 1, never W9-A first pass.
FUND_ATS_2019 = "49.9%"
FUND_ATS_2019_CI = "[46.9%, 52.3%]"
FUND_ATS_2019_N = "n=553"
SNAPSHOT_ATS = "48.9%"
SNAPSHOT_ATS_CI = "[47.5%, 50.5%]"
LOGLOSS_BAND = "0.78–0.93 vs 0.693"
SCORECARD_FUND_ATS = "48.9% / 49.9%"
FIRST_PASS_ATS_2019 = "47.8%"
FIRST_PASS_LOGLOSS = "0.93–1.35"


def _memo() -> str:
    return MEMO.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    j = text.find(end)
    assert i >= 0, f"missing {start!r}"
    assert j > i, f"missing {end!r} after {start!r}"
    return text[i:j]


def _folded(text: str) -> str:
    return " ".join(text.split())


def _new_cells(text: str) -> dict[str, str]:
    """Parse the 13-id old-vs-new table; return id -> new-column text."""
    table = _between(text, "## Old vs new", "**Champion / registry.**")
    out: dict[str, str] = {}
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) < 3:
            continue
        metric_id = parts[0].strip("`")
        out[metric_id] = parts[2]
    return out


def test_section_2_uses_accurate_logloss_comparison_not_much_greater() -> None:
    section = _between(_memo(), "## 2. MARKET", "## 3. SIDES")
    folded = _folded(section)
    assert "all ≫" not in section
    assert "all >>" not in section
    assert LOGLOSS_BAND in folded
    assert FIRST_PASS_LOGLOSS not in folded


def test_verdict_cites_amended_figures_label_unchanged() -> None:
    section = _between(
        _memo(),
        "### Verdict (one recommendation)",
        "### `/results` restamp",
    )
    folded = _folded(section)
    assert "**NOT CURRENTLY FIT TO BET.**" in section
    assert f"{SNAPSHOT_ATS} {SNAPSHOT_ATS_CI}" in folded
    assert f"{FUND_ATS_2019} {FUND_ATS_2019_CI}" in folded
    assert LOGLOSS_BAND in folded
    assert "log-loss loses to 0.693" not in folded


def test_old_vs_new_table_new_column_is_w9g_not_first_pass() -> None:
    cells = _new_cells(_memo())
    ats = cells["fund_ats_2019"]
    assert ats.startswith(FUND_ATS_2019)
    assert FUND_ATS_2019_CI in ats
    assert FUND_ATS_2019_N in ats
    assert not ats.startswith(FIRST_PASS_ATS_2019)

    band = cells["ats_logloss_band"]
    assert band.startswith("0.78–0.93 vs 0.693")
    assert not band.startswith(FIRST_PASS_LOGLOSS)

    scorecard_ats = cells["scorecard_fund_ats"]
    assert SCORECARD_FUND_ATS in scorecard_ats
    assert "47.8%" not in scorecard_ats

    scorecard_ll = cells["scorecard_logloss"]
    assert LOGLOSS_BAND in scorecard_ll
    assert FIRST_PASS_LOGLOSS not in scorecard_ll


def test_sample_basis_notes_are_one_sentence_each() -> None:
    notes = _folded(_between(_memo(), "### Sample-basis notes", "**Champion / registry.**"))
    assert "90 rows (2019 weeks 2–4) carry no credible ensemble member" in notes
    assert "on the matched sample point accuracy is essentially unchanged" in notes
    assert "sample excludes rows where the model recorded no ATS probability" in notes
    assert "no probability is imputed" in notes
    assert "same basis" in notes
    assert "partially degraded cohort per ADR 0014" in notes


# Amended-memo literals that must appear on the restamped track record (verbatim).
EXPECTED_TRACK_RECORD: dict[str, dict[str, object]] = {
    "fund_ats_snapshots": {
        "value": 48.9,
        "ci_lower": 47.5,
        "ci_upper": 50.5,
        "n": 3496,
        "vintage": "W9G_REGRADE",
    },
    "fund_ats_2019": {
        "value": 49.9,
        "ci_lower": 46.9,
        "ci_upper": 52.3,
        "n": 553,
        "vintage": "W9G_REGRADE",
    },
    "fund_ou_snapshots": {
        "value": 51.5,
        "ci_lower": 49.7,
        "ci_upper": 53.5,
        "n": 3136,
        "vintage": "W9A_REVAL",
    },
    "fund_ou_2019": {
        "value": 51.4,
        "ci_lower": 46.5,
        "ci_upper": 55.3,
        "n": 551,
        "vintage": "W9A_REVAL",
    },
    "mae_margin_fund": {"value": 14.53, "n": 4285, "vintage": "W9A_REVAL"},
    "mae_margin_a2": {"value": 15.51, "n": 4290, "vintage": "W9A_REVAL"},
    "crps_margin_fund": {"value": 10.02, "n": 4175, "vintage": "W9A_REVAL"},
    "crps_margin_a2": {"value": 10.75, "n": 4175, "vintage": "W9A_REVAL"},
    "ats_logloss_band": {"value": "0.78–0.93", "vintage": "W9G_REGRADE"},
    "scorecard_clv": {"value": "UNMEASURABLE"},
    "scorecard_fund_ats": {"value": "MISSED"},
    "scorecard_fund_ou": {"value": "MISSED"},
    "scorecard_logloss": {"value": "MISSED"},
}

PUBLISHED_SURFACES = (
    EXPORT_PY,
    COPY_TS,
    ABOUT_COPY,
    FIXTURE_DIR / "track_record.json",
    FIXTURE_DIR / "week_predictions.json",
    FIXTURE_DIR / "results_2024.json",
)


def _load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _metrics_by_id(track: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = track["metrics"]
    assert isinstance(rows, list)
    return {str(row["id"]): row for row in rows}


def test_build_track_record_matches_amended_memo() -> None:
    track = build_track_record()
    by_id = _metrics_by_id(track)
    assert list(by_id) == list(EXPECTED_TRACK_RECORD)
    for metric_id, expected in EXPECTED_TRACK_RECORD.items():
        row = by_id[metric_id]
        for key, value in expected.items():
            assert row[key] == value, f"{metric_id}.{key}: {row[key]!r} != {value!r}"
    assert track["source_memo"] == "docs/notes/23-reval.md"
    assert track["vintage_labels"] == [TRACK_RECORD_VINTAGE_LABEL]
    assert track["ensemble_scope_label"] == "REDUCED_PER_ADR_0013"
    assert track["verdict"]["label"] == "NOT CURRENTLY FIT TO BET"
    plain = str(track["verdict"]["plain_language"])
    assert "48.9% [47.5%, 50.5%]" in plain
    assert "49.9% [46.9%, 52.3%]" in plain
    assert LOGLOSS_BAND in plain
    assert FIRST_PASS_ATS_2019 not in plain
    assert FIRST_PASS_LOGLOSS not in plain


def test_committed_track_record_fixture_matches_builder() -> None:
    fixture = _load_fixture("track_record.json")
    built = build_track_record(
        published_at=datetime(2024, 9, 24, 10, 0, tzinfo=UTC),
        fixture=True,
    )
    assert fixture["schema_version"] == "1.2.0"  # frozen fixture (pre-1.3.0)
    assert SCHEMA_VERSION == "1.3.0"
    assert fixture["source_memo"] == built["source_memo"]
    assert fixture["verdict"] == built["verdict"]
    assert fixture["vintage_labels"] == built["vintage_labels"]
    assert _metrics_by_id(fixture) == _metrics_by_id(built)


def test_week_predictions_as_of_precedes_every_kickoff() -> None:
    week = _load_fixture("week_predictions.json")
    games = week["games"]
    assert isinstance(games, list)
    assert week["schema_version"] == "1.2.0"
    assert week["vintage_label"] == TRACK_RECORD_VINTAGE_LABEL
    published = datetime.fromisoformat(str(week["published_at"]).replace("Z", "+00:00"))
    assert published == datetime(2024, 9, 24, 10, 0, tzinfo=UTC)
    assert len(games) == 56
    for game in games:
        assert isinstance(game, dict)
        kickoff = datetime.fromisoformat(str(game["kickoff_utc"]).replace("Z", "+00:00"))
        row_published = datetime.fromisoformat(str(game["published_at"]).replace("Z", "+00:00"))
        assert row_published == published
        assert published < kickoff, (
            f"{game['game_id']}: {published.isoformat()} >= {kickoff.isoformat()}"
        )
        for withdrawn in WITHDRAWN_FIELDS:
            assert withdrawn not in game


def test_fixture_401628373_as_of_precedes_its_kickoff() -> None:
    week = _load_fixture("week_predictions.json")
    games = {str(g["game_id"]): g for g in week["games"]}  # type: ignore[index]
    game = games["401628373"]
    assert game["published_at"] == "2024-09-24T10:00:00Z"
    assert game["kickoff_utc"] == "2024-09-28T19:30:00Z"


def test_no_first_pass_figures_as_current_published_values() -> None:
    forbidden = (FIRST_PASS_ATS_2019, FIRST_PASS_LOGLOSS, "0.93–1.35", "n=657")
    for path in PUBLISHED_SURFACES:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path} still publishes first-pass token {token!r}"


def test_copy_cites_amended_numbers_and_reval_memo() -> None:
    copy = COPY_TS.read_text(encoding="utf-8")
    assert SNAPSHOT_ATS in copy
    assert FUND_ATS_2019 in copy
    assert "0.78–0.93" in copy
    assert FIRST_PASS_ATS_2019 not in copy
    scope = (
        ROOT / "webapp" / "site" / "src" / "components" / "Results" / "ScopeSection.tsx"
    ).read_text(encoding="utf-8")
    assert "docs/notes/23-reval.md" in scope
    about = ABOUT_COPY.read_text(encoding="utf-8")
    assert "NOT CURRENTLY FIT TO BET" in about
    assert "50.7" not in about
    assert "14.85" not in about
