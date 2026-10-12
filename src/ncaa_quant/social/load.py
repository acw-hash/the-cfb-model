"""Load + validate published Ridge artifacts for social charts.

Read-only consumer: never recomputes ratings/tiers or mutates artifact values.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.config import load_config

SourceMode = Literal["r2", "local"]

STALE_HOURS = 36.0
REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPORTED_MAJOR_TS = (
    REPO_ROOT / "webapp" / "site" / "src" / "lib" / "artifacts" / "types.ts"
)
_MAJOR_RE = re.compile(
    r"export\s+const\s+SUPPORTED_SCHEMA_MAJOR\s*=\s*(\d+)\s*;",
)


class ArtifactLoadError(RuntimeError):
    """Fail-closed validation / fetch error for published artifacts."""


@dataclass
class ArtifactBlob:
    """One loaded JSON artifact with provenance."""

    name: str
    data: dict[str, Any]
    source: str
    sha256: str
    published_at: str | None


@dataclass
class ResolvedWeeks:
    """Per-chart week resolution."""

    map_week: int
    slate_week: int
    surprise_week: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class LoadedArtifacts:
    """Bundle of artifacts for one chart render run."""

    meta: ArtifactBlob
    team_ratings: ArtifactBlob
    week_predictions: ArtifactBlob
    results: ArtifactBlob
    season: int
    weeks: ResolvedWeeks
    fbs_team_ids: set[int]
    school_to_team_id: dict[str, int]
    fixture_watermark: bool = False
    odds_snapshot: ArtifactBlob | None = None


def read_supported_schema_major(*, types_path: Path | None = None) -> int:
    """Read ``SUPPORTED_SCHEMA_MAJOR`` from the webapp TypeScript constant."""
    path = types_path or SUPPORTED_MAJOR_TS
    if not path.is_file():
        msg = f"Cannot locate webapp schema major constant at {path}"
        raise ArtifactLoadError(msg)
    text = path.read_text(encoding="utf-8")
    match = _MAJOR_RE.search(text)
    if match is None:
        msg = f"SUPPORTED_SCHEMA_MAJOR not found in {path}"
        raise ArtifactLoadError(msg)
    return int(match.group(1))


def schema_major(version: str | None) -> int:
    """Parse major component of a ``schema_version`` string."""
    if not version:
        msg = "Missing schema_version"
        raise ArtifactLoadError(msg)
    head = str(version).split(".", 1)[0]
    if not head.isdigit():
        msg = f"Unparseable schema_version={version!r}"
        raise ArtifactLoadError(msg)
    return int(head)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def resolve_r2_base_url() -> str:
    """HTTPS base for public ``latest/`` objects (no credentials)."""
    cfg = load_config()
    base = (cfg.webapp.r2_public_base_url or "").strip().rstrip("/")
    if not base:
        msg = (
            "source=r2 requires NCAA_QUANT_WEBAPP__R2_PUBLIC_BASE_URL "
            "(plain HTTPS GET of public latest/ objects; no R2 credentials). "
            "Until public-read is enabled, use --source local --artifact-dir <path>."
        )
        raise ArtifactLoadError(msg)
    if not base.lower().startswith("https://"):
        msg = f"r2_public_base_url must be https://, got {base!r}"
        raise ArtifactLoadError(msg)
    return base


def _fetch_bytes_https(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "ridge-social-charts/1.0"})
    try:
        with urlopen(req, timeout=60) as resp:  # noqa: S310 — operator HTTPS only
            return resp.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        msg = f"Failed to fetch {url}: {exc}"
        raise ArtifactLoadError(msg) from exc


def _load_json_bytes(raw: bytes, *, name: str, source: str) -> ArtifactBlob:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"{name}: invalid JSON from {source}: {exc}"
        raise ArtifactLoadError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{name}: expected JSON object from {source}"
        raise ArtifactLoadError(msg)
    published = data.get("published_at")
    return ArtifactBlob(
        name=name,
        data=data,
        source=source,
        sha256=_sha256_bytes(raw),
        published_at=str(published) if published is not None else None,
    )


def load_artifact_file(
    *,
    source: SourceMode,
    name: str,
    artifact_dir: Path | None,
    r2_base: str | None = None,
) -> ArtifactBlob:
    """Load one artifact JSON by name from R2 HTTPS or a local directory."""
    if source == "local":
        if artifact_dir is None:
            msg = "--artifact-dir is required when --source local"
            raise ArtifactLoadError(msg)
        path = artifact_dir / name
        if not path.is_file():
            msg = f"Missing local artifact: {path}"
            raise ArtifactLoadError(msg)
        return _load_json_bytes(path.read_bytes(), name=name, source=str(path))

    base = r2_base or resolve_r2_base_url()
    # Accept either .../latest or a bare public root that already ends with latest.
    if base.endswith("/latest"):
        url = f"{base}/{name}"
    else:
        url = f"{base}/latest/{name}"
    return _load_json_bytes(_fetch_bytes_https(url), name=name, source=url)


def _any_fixture(blobs: list[ArtifactBlob]) -> bool:
    return any(bool(b.data.get("fixture")) for b in blobs)


def validate_schema_major(blobs: list[ArtifactBlob], *, supported: int) -> None:
    """Fail closed when any artifact major ≠ webapp supported major."""
    for blob in blobs:
        major = schema_major(blob.data.get("schema_version"))
        if major != supported:
            msg = (
                f"{blob.name}: schema_version major {major} != "
                f"webapp SUPPORTED_SCHEMA_MAJOR {supported}"
            )
            raise ArtifactLoadError(msg)


def validate_staleness(
    meta: ArtifactBlob,
    *,
    now: datetime,
    allow_stale: bool,
) -> None:
    """Refuse per DESIGN §3.2 site staleness.

    Refuse only when ``now − published_at > 36h`` **and**
    ``now > meta.next_expected_publish_utc``. Either condition alone is not
    enough (mirrors the site banner gate).
    """
    if not meta.published_at:
        msg = "meta.json missing published_at"
        raise ArtifactLoadError(msg)
    now_utc = now.astimezone(UTC)
    published = _parse_utc(meta.published_at)
    age_h = (now_utc - published).total_seconds() / 3600.0
    if age_h <= STALE_HOURS:
        return
    next_raw = meta.data.get("next_expected_publish_utc")
    if not next_raw:
        # Without a next-expected slot, fall back to age-only refusal.
        past_next = True
        next_s = None
    else:
        next_expected = _parse_utc(str(next_raw))
        past_next = now_utc > next_expected
        next_s = str(next_raw)
    if past_next and not allow_stale:
        msg = (
            f"Site stale: meta.published_at={meta.published_at} "
            f"age={age_h:.1f}h > {STALE_HOURS:.0f}h and now past "
            f"next_expected_publish_utc={next_s} "
            "(pass --allow-stale to override)"
        )
        raise ArtifactLoadError(msg)


def validate_team_ratings_populated(
    ratings: ArtifactBlob,
    *,
    target_week: int | None,
) -> None:
    """Hard-fail empty/stub team_ratings (historically a 107-byte stub)."""
    teams = ratings.data.get("teams")
    if not isinstance(teams, dict) or not teams:
        msg = (
            f"{ratings.name}: empty or stub teams object "
            "(refusing to plot; re-publish team_ratings)"
        )
        raise ArtifactLoadError(msg)
    if target_week is not None:
        has_week = False
        for entry in teams.values():
            weeks = entry.get("weeks") or []
            if any(int(w.get("week", -1)) <= target_week for w in weeks):
                # Prefer exact week presence check below.
                pass
            if any(int(w.get("week", -1)) == target_week for w in weeks):
                has_week = True
                break
        if not has_week:
            # Also accept if latest week equals target via resolve path.
            latest = latest_ratings_week(ratings.data)
            if latest is None or latest < target_week:
                msg = (
                    f"{ratings.name}: no weeks ≤ target week {target_week} "
                    f"(latest={latest})"
                )
                raise ArtifactLoadError(msg)


def latest_ratings_week(ratings: dict[str, Any]) -> int | None:
    """Latest ``weeks[].week`` across all teams in team_ratings."""
    weeks: list[int] = []
    for entry in (ratings.get("teams") or {}).values():
        for row in entry.get("weeks") or []:
            if row.get("week") is not None:
                weeks.append(int(row["week"]))
    return max(weeks) if weeks else None


def latest_graded_week(results: dict[str, Any]) -> int | None:
    """Latest week with ≥1 ``grade_status == "graded"``."""
    weeks = [
        int(g["week"])
        for g in (results.get("games") or [])
        if g.get("grade_status") == "graded" and g.get("week") is not None
    ]
    return max(weeks) if weeks else None


def resolve_weeks(
    *,
    meta: dict[str, Any],
    team_ratings: dict[str, Any],
    results: dict[str, Any],
    week: int | Literal["auto"] = "auto",
    map_week: int | None = None,
    slate_week: int | None = None,
    surprise_week: int | None = None,
) -> ResolvedWeeks:
    """Resolve per-chart weeks; warn if ratings lag graded results."""
    warnings: list[str] = []
    auto = week == "auto"

    ratings_latest = latest_ratings_week(team_ratings)
    if ratings_latest is None:
        msg = "team_ratings has no weeks"
        raise ArtifactLoadError(msg)
    graded_latest = latest_graded_week(results)
    if graded_latest is None:
        msg = "results has no graded games"
        raise ArtifactLoadError(msg)

    meta_week = int(meta["week"])

    resolved_map = map_week if map_week is not None else (
        ratings_latest if auto else int(week)
    )
    resolved_slate = slate_week if slate_week is not None else (
        meta_week if auto else int(week)
    )
    resolved_surprise = surprise_week if surprise_week is not None else (
        graded_latest if auto else int(week)
    )

    if resolved_map < resolved_surprise:
        warnings.append(
            "WARNING: ratings week "
            f"{resolved_map} < surprise week {resolved_surprise} — "
            "Sunday ratings update appears missed; do not silently use a "
            "mismatched 'Through Week N' label."
        )

    return ResolvedWeeks(
        map_week=int(resolved_map),
        slate_week=int(resolved_slate),
        surprise_week=int(resolved_surprise),
        warnings=warnings,
    )


def load_fbs_team_ids(season: int, *, staged_root: Path | None = None) -> set[int]:
    """FBS membership from staged ``teams`` classification (not hardcoded)."""
    root = staged_root or (REPO_ROOT / "data" / "staged")
    path = root / "teams" / f"season={season}" / "part.parquet"
    if not path.is_file():
        msg = (
            f"Cannot resolve FBS membership: missing staged teams at {path}"
        )
        raise ArtifactLoadError(msg)
    df = pd.read_parquet(path)
    if "classification" not in df.columns or "team_id" not in df.columns:
        msg = f"Staged teams missing classification/team_id columns: {path}"
        raise ArtifactLoadError(msg)
    mask = df["classification"].astype(str).str.casefold() == "fbs"
    ids = {int(x) for x in df.loc[mask, "team_id"].tolist()}
    if not ids:
        msg = f"No FBS teams found in staged teams for season={season}"
        raise ArtifactLoadError(msg)
    return ids


def school_index_from_ratings(ratings: dict[str, Any]) -> dict[str, int]:
    """Map school display name → CFBD team_id from team_ratings keys."""
    out: dict[str, int] = {}
    for tid_s, entry in (ratings.get("teams") or {}).items():
        school = entry.get("school")
        if school:
            out[str(school)] = int(tid_s)
    return out


def school_index_from_staged(
    season: int,
    *,
    staged_root: Path | None = None,
) -> dict[str, int]:
    """Fallback school → team_id from staged teams."""
    root = staged_root or (REPO_ROOT / "data" / "staged")
    path = root / "teams" / f"season={season}" / "part.parquet"
    df = pd.read_parquet(path)
    return {str(r.school): int(r.team_id) for r in df.itertuples()}


def load_artifacts(
    *,
    season: int,
    source: SourceMode = "r2",
    artifact_dir: Path | None = None,
    week: int | Literal["auto"] = "auto",
    map_week: int | None = None,
    slate_week: int | None = None,
    surprise_week: int | None = None,
    allow_fixture: bool = False,
    allow_stale: bool = False,
    now: datetime | None = None,
    staged_root: Path | None = None,
    types_path: Path | None = None,
) -> LoadedArtifacts:
    """Fetch, validate, and week-resolve the artifact bundle."""
    now_utc = now or datetime.now(UTC)
    names = {
        "meta": "meta.json",
        "week_predictions": "week_predictions.json",
        "team_ratings": f"team_ratings_{season}.json",
        "results": f"results_{season}.json",
    }
    r2_base = resolve_r2_base_url() if source == "r2" else None
    blobs = {
        key: load_artifact_file(
            source=source,
            name=fname,
            artifact_dir=artifact_dir,
            r2_base=r2_base,
        )
        for key, fname in names.items()
    }
    blob_list = list(blobs.values())
    supported = read_supported_schema_major(types_path=types_path)
    validate_schema_major(blob_list, supported=supported)

    fixture = _any_fixture(blob_list)
    if fixture and not allow_fixture:
        msg = (
            "Artifact marked fixture:true — refusing "
            "(pass --allow-fixture to render with watermark)"
        )
        raise ArtifactLoadError(msg)

    validate_staleness(blobs["meta"], now=now_utc, allow_stale=allow_stale)

    meta_season = int(blobs["meta"].data["season"])
    if meta_season != season:
        msg = f"Requested season={season} but meta.season={meta_season}"
        raise ArtifactLoadError(msg)

    weeks = resolve_weeks(
        meta=blobs["meta"].data,
        team_ratings=blobs["team_ratings"].data,
        results=blobs["results"].data,
        week=week,
        map_week=map_week,
        slate_week=slate_week,
        surprise_week=surprise_week,
    )
    validate_team_ratings_populated(
        blobs["team_ratings"],
        target_week=weeks.map_week,
    )

    fbs_ids = load_fbs_team_ids(season, staged_root=staged_root)
    schools = school_index_from_ratings(blobs["team_ratings"].data)
    if not schools:
        schools = school_index_from_staged(season, staged_root=staged_root)

    odds_blob: ArtifactBlob | None = None
    try:
        odds_blob = load_artifact_file(
            source=source,
            name="odds_snapshot.json",
            artifact_dir=artifact_dir,
            r2_base=r2_base,
        )
        # Odds schema is independent (ADR-ODDS-SNAPSHOT); do not pin to
        # SUPPORTED_SCHEMA_MAJOR for week_predictions.
    except ArtifactLoadError:
        odds_blob = None

    return LoadedArtifacts(
        meta=blobs["meta"],
        team_ratings=blobs["team_ratings"],
        week_predictions=blobs["week_predictions"],
        results=blobs["results"],
        season=season,
        weeks=weeks,
        fbs_team_ids=fbs_ids,
        school_to_team_id=schools,
        fixture_watermark=fixture,
        odds_snapshot=odds_blob,
    )
