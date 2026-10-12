"""Team logo fetch, normalize, and chart-side loading (keyed by CFBD team_id)."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd  # type: ignore[import-untyped]
from PIL import Image

from ncaa_quant.config import load_secrets
from ncaa_quant.ingestion.cfbd import BASE_URL

logger = logging.getLogger(__name__)

Variant = Literal["standard", "dark"]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOGO_DIR = REPO_ROOT / "data" / "social" / "logos"
OVERRIDE_DIRNAME = "overrides"
MANIFEST_NAME = "manifest.json"
TARGET_LONGEST = 512


@dataclass(frozen=True)
class LogoRecord:
    """One logo file on disk."""

    team_id: int
    school: str
    variant: Variant
    path: Path
    source_url: str | None
    sha256: str
    fetched_at: str
    from_override: bool = False


@dataclass(frozen=True)
class LogoAsset:
    """In-memory RGBA logo ready for plotting."""

    team_id: int
    variant: Variant
    image: Image.Image
    mean_luminance: float
    needs_plate: bool
    from_override: bool


class LogoError(RuntimeError):
    """Logo fetch / resolve failure."""


def override_path(logo_dir: Path, team_id: int, *, dark: bool = False) -> Path:
    """Exact path the owner should drop a manual override at."""
    name = f"{team_id}_dark.png" if dark else f"{team_id}.png"
    return logo_dir / OVERRIDE_DIRNAME / name


def cached_path(logo_dir: Path, team_id: int, *, dark: bool = False) -> Path:
    """Path for a fetched (non-override) logo file."""
    name = f"{team_id}_dark.png" if dark else f"{team_id}.png"
    return logo_dir / name


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _download(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "ridge-social-charts/1.0"})
    with urlopen(req, timeout=60) as resp:  # noqa: S310 — CDN / CFBD HTTPS
        return resp.read()


def normalize_logo(raw: bytes) -> Image.Image:
    """RGBA, trim transparent padding, longest side ``TARGET_LONGEST``."""
    img = Image.open(BytesIO(raw)).convert("RGBA")
    # Trim transparent padding.
    bbox = img.split()[-1].getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    longest = max(w, h)
    if longest <= 0:
        msg = "Logo has zero size after trim"
        raise LogoError(msg)
    if longest != TARGET_LONGEST:
        scale = TARGET_LONGEST / float(longest)
        img = img.resize(
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            Image.Resampling.LANCZOS,
        )
    return img


def mean_opaque_luminance(img: Image.Image) -> float:
    """Mean relative luminance of pixels with alpha > 16."""
    import numpy as np

    arr = np.asarray(img.convert("RGBA"), dtype=np.float64)
    alpha = arr[:, :, 3]
    mask = alpha > 16
    if not np.any(mask):
        return 0.0
    rgb = arr[:, :, :3][mask] / 255.0
    # Rec. 709 luminance.
    lum = 0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1] + 0.0722 * rgb[:, 2]
    return float(lum.mean())


def _pick_logo_urls(logos: list[str] | None) -> tuple[str | None, str | None]:
    """Pick preferred standard + dark CDN URLs from a CFBD logos array."""
    if not logos:
        return None, None
    standard: str | None = None
    dark: str | None = None
    # Prefer 500px variants when present.
    for url in logos:
        u = str(url)
        if "logos-dark" in u and "/500/" in u:
            dark = u
        elif "logos-dark" not in u and "/500/" in u and "logos/" in u:
            standard = u
    if standard is None:
        for url in logos:
            u = str(url)
            if "logos-dark" not in u and "logos/" in u:
                standard = u
                break
    if dark is None:
        for url in logos:
            u = str(url)
            if "logos-dark" in u:
                dark = u
                break
    return standard, dark


def load_logo_urls_from_raw_cfbd(
    season: int,
    *,
    raw_root: Path | None = None,
) -> dict[int, dict[str, Any]]:
    """Scan archived CFBD ``teams_s{season}_*.json`` for logo URLs (no API call)."""
    root = raw_root or (REPO_ROOT / "data" / "raw" / "cfbd")
    files = sorted(root.glob(f"**/teams_s{season}_*.json"))
    if not files:
        return {}
    path = files[-1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for row in payload:
        tid = int(row["id"])
        std, dark = _pick_logo_urls(row.get("logos"))
        out[tid] = {
            "school": str(row.get("school") or ""),
            "classification": str(row.get("classification") or ""),
            "standard_url": std,
            "dark_url": dark,
            "source": str(path),
        }
    return out


def fetch_teams_from_cfbd(season: int) -> list[dict[str, Any]]:
    """One CFBD ``/teams`` call for the season (logs that a credit was spent)."""
    secrets = load_secrets()
    key = secrets.cfbd_api_key.get_secret_value()
    url = f"{BASE_URL}/teams?year={season}"
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "ridge-social-charts/1.0",
        },
    )
    logger.warning("CFBD credit spent: GET /teams?year=%s", season)
    print(f"CFBD credit spent: GET /teams?year={season}")
    with urlopen(req, timeout=60) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        msg = "CFBD /teams did not return a list"
        raise LogoError(msg)
    return payload


def fbs_teams_from_staged(
    season: int,
    *,
    staged_root: Path | None = None,
) -> pd.DataFrame:
    """FBS team_id / school / abbreviation from staged teams."""
    root = staged_root or (REPO_ROOT / "data" / "staged")
    path = root / "teams" / f"season={season}" / "part.parquet"
    if not path.is_file():
        msg = f"Missing staged teams: {path}"
        raise LogoError(msg)
    df = pd.read_parquet(path)
    mask = df["classification"].astype(str).str.casefold() == "fbs"
    cols = [
        c
        for c in ("team_id", "school", "abbreviation", "classification", "conference")
        if c in df.columns
    ]
    return df.loc[mask, cols].copy()


def conference_by_team_id(
    season: int,
    *,
    staged_root: Path | None = None,
) -> dict[int, str]:
    """FBS ``team_id → conference`` from staged teams."""
    fbs = fbs_teams_from_staged(season, staged_root=staged_root)
    if "conference" not in fbs.columns:
        return {}
    out: dict[int, str] = {}
    for row in fbs.itertuples(index=False):
        tid = int(row.team_id)
        conf = str(getattr(row, "conference", "") or "")
        if conf:
            out[tid] = conf
    return out


def _write_normalized(path: Path, raw: bytes) -> tuple[str, Image.Image]:
    img = normalize_logo(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    path.write_bytes(data)
    return _sha256_bytes(data), img


def fetch_team_logos(
    *,
    season: int,
    logo_dir: Path | None = None,
    refresh: bool = False,
    staged_root: Path | None = None,
    raw_root: Path | None = None,
) -> tuple[list[LogoRecord], list[dict[str, Any]]]:
    """Download/normalize FBS logos; return records + missing table rows."""
    logo_dir = logo_dir or DEFAULT_LOGO_DIR
    logo_dir.mkdir(parents=True, exist_ok=True)
    (logo_dir / OVERRIDE_DIRNAME).mkdir(parents=True, exist_ok=True)

    fbs = fbs_teams_from_staged(season, staged_root=staged_root)
    url_map = load_logo_urls_from_raw_cfbd(season, raw_root=raw_root)
    if not url_map:
        teams = fetch_teams_from_cfbd(season)
        for row in teams:
            tid = int(row["id"])
            std, dark = _pick_logo_urls(row.get("logos"))
            url_map[tid] = {
                "school": str(row.get("school") or ""),
                "classification": str(row.get("classification") or ""),
                "standard_url": std,
                "dark_url": dark,
                "source": "cfbd:/teams",
            }

    records: list[LogoRecord] = []
    missing: list[dict[str, Any]] = []
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    abbr_by_id: dict[int, str] = {}
    for row in fbs.itertuples(index=False):
        tid = int(row.team_id)
        school = str(row.school)
        abbr = str(getattr(row, "abbreviation", "") or "")
        if abbr:
            abbr_by_id[tid] = abbr
        info = url_map.get(tid, {})
        got_any = False
        for variant, dark, url_key in (
            ("standard", False, "standard_url"),
            ("dark", True, "dark_url"),
        ):
            ov = override_path(logo_dir, tid, dark=dark)
            dest = cached_path(logo_dir, tid, dark=dark)

            if ov.is_file():
                sha, _ = _write_normalized(dest, ov.read_bytes())
                records.append(
                    LogoRecord(
                        team_id=tid,
                        school=school,
                        variant=variant,  # type: ignore[arg-type]
                        path=dest,
                        source_url=f"override:{ov}",
                        sha256=sha,
                        fetched_at=now,
                        from_override=True,
                    )
                )
                got_any = True
                continue

            if dest.is_file() and not refresh:
                records.append(
                    LogoRecord(
                        team_id=tid,
                        school=school,
                        variant=variant,  # type: ignore[arg-type]
                        path=dest,
                        source_url=info.get(url_key),
                        sha256=_sha256_bytes(dest.read_bytes()),
                        fetched_at=now,
                    )
                )
                got_any = True
                continue

            url = info.get(url_key)
            if not url:
                continue
            try:
                raw = _download(str(url))
                sha, _ = _write_normalized(dest, raw)
                records.append(
                    LogoRecord(
                        team_id=tid,
                        school=school,
                        variant=variant,  # type: ignore[arg-type]
                        path=dest,
                        source_url=str(url),
                        sha256=sha,
                        fetched_at=now,
                    )
                )
                got_any = True
            except (HTTPError, URLError, OSError, LogoError) as exc:
                logger.warning("Logo download failed team_id=%s %s: %s", tid, url, exc)

        has_std = cached_path(logo_dir, tid, dark=False).is_file()
        has_dark = cached_path(logo_dir, tid, dark=True).is_file()
        if not (has_std or has_dark):
            missing.append(
                {
                    "team_id": tid,
                    "school": school,
                    "override_path": str(override_path(logo_dir, tid, dark=False)),
                    "override_path_dark": str(override_path(logo_dir, tid, dark=True)),
                }
            )
        _ = got_any

    manifest_path = logo_dir / MANIFEST_NAME
    manifest = [
        {
            "team_id": r.team_id,
            "school": r.school,
            "abbreviation": abbr_by_id.get(r.team_id),
            "source_url": r.source_url,
            "variant": r.variant,
            "sha256": r.sha256,
            "fetched_at": r.fetched_at,
            "path": str(r.path.relative_to(logo_dir)) if r.path.is_relative_to(logo_dir) else str(r.path),
            "from_override": r.from_override,
        }
        for r in records
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return records, missing


def resolve_logo_path(
    team_id: int,
    *,
    logo_dir: Path | None = None,
    prefer_dark: bool = True,
) -> tuple[Path | None, bool]:
    """Resolve active logo path; overrides win. Returns (path, from_override)."""
    logo_dir = logo_dir or DEFAULT_LOGO_DIR
    if prefer_dark:
        for dark in (True, False):
            ov = override_path(logo_dir, team_id, dark=dark)
            if ov.is_file():
                return ov, True
        for dark in (True, False):
            p = cached_path(logo_dir, team_id, dark=dark)
            if p.is_file():
                return p, False
    else:
        for dark in (False, True):
            ov = override_path(logo_dir, team_id, dark=dark)
            if ov.is_file():
                return ov, True
        for dark in (False, True):
            p = cached_path(logo_dir, team_id, dark=dark)
            if p.is_file():
                return p, False
    return None, False


def load_logo_asset(
    team_id: int,
    *,
    logo_dir: Path | None = None,
    luminance_threshold: float | None = None,
) -> LogoAsset | None:
    """Load chart logo (dark preferred) with luminance plate flag."""
    from ncaa_quant.social.theme import LOGO_LUMINANCE_THRESHOLD

    thresh = LOGO_LUMINANCE_THRESHOLD if luminance_threshold is None else luminance_threshold
    path, from_override = resolve_logo_path(team_id, logo_dir=logo_dir, prefer_dark=True)
    if path is None:
        return None
    img = Image.open(path).convert("RGBA")
    # Ensure normalized size even for raw overrides.
    buf = BytesIO()
    img.save(buf, format="PNG")
    img = normalize_logo(buf.getvalue())
    lum = mean_opaque_luminance(img)
    variant: Variant = "dark" if path.name.endswith("_dark.png") else "standard"
    return LogoAsset(
        team_id=team_id,
        variant=variant,
        image=img,
        mean_luminance=lum,
        needs_plate=lum < thresh,
        from_override=from_override,
    )


def load_abbrev_map(logo_dir: Path | None = None) -> dict[int, str]:
    """Team_id → abbreviation from ``manifest.json`` when present."""
    logo_dir = logo_dir or DEFAULT_LOGO_DIR
    path = logo_dir / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, list):
        return {}
    out: dict[int, str] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        abbr = row.get("abbreviation")
        tid = row.get("team_id")
        if abbr and tid is not None:
            out[int(tid)] = str(abbr)
    return out


#: Cap display aspect ratio at width ≤ 1.6× height after trim.
LOGO_MAX_ASPECT = 1.6
#: Slot box: width ≤ 1.4× slot, height ≤ 1.0× slot (script logos stay capped).
LOGO_SLOT_WIDTH_MULT = 1.4
LOGO_SLOT_HEIGHT_MULT = 1.0
_ALPHA_OPAQUE = 16


@dataclass(frozen=True)
class LogoDisplaySize:
    """CSS-px display box after opaque-area normalization + slot cap."""

    team_id: int
    width: float
    height: float
    scale: float
    opaque_area_px: float
    native_w: int
    native_h: int
    slot_px: float | None = None


def opaque_pixel_area(img: Image.Image) -> float:
    """Count of pixels with alpha > 16 (native image pixels)."""
    import numpy as np

    arr = np.asarray(img.convert("RGBA"), dtype=np.uint8)
    return float(np.count_nonzero(arr[:, :, 3] > _ALPHA_OPAQUE))


def logo_display_size(
    path: Path,
    *,
    team_id: int,
    target_opaque_area: float,
    max_aspect: float = LOGO_MAX_ASPECT,
    slot_px: float | None = None,
) -> LogoDisplaySize:
    """Trim, area-normalize, then cap to a slot bounding box.

    Slot rule (when ``slot_px`` set): width ≤ 1.4×slot, height ≤ 1.0×slot so
    script logos cannot outsize block logos.
    """
    img = Image.open(path).convert("RGBA")
    bbox = img.split()[-1].getbbox()
    if bbox:
        img = img.crop(bbox)
    native_w, native_h = img.size
    if native_w <= 0 or native_h <= 0:
        msg = f"Logo {path} has zero size after trim"
        raise LogoError(msg)

    aspect = native_w / float(native_h)
    if aspect > max_aspect:
        eff_w = max_aspect * native_h
        eff_h = float(native_h)
    else:
        eff_w = float(native_w)
        eff_h = float(native_h)

    opaque = opaque_pixel_area(img)
    if opaque <= 0:
        opaque = eff_w * eff_h
    scale = (target_opaque_area / opaque) ** 0.5
    disp_w = native_w * scale
    disp_h = native_h * scale
    if disp_w > max_aspect * disp_h:
        disp_w = max_aspect * disp_h
        scale = disp_h / float(native_h)

    if slot_px is not None and slot_px > 0:
        max_w = LOGO_SLOT_WIDTH_MULT * slot_px
        max_h = LOGO_SLOT_HEIGHT_MULT * slot_px
        fit = min(max_w / max(disp_w, 1e-9), max_h / max(disp_h, 1e-9), 1.0)
        disp_w *= fit
        disp_h *= fit
        scale *= fit

    return LogoDisplaySize(
        team_id=team_id,
        width=float(disp_w),
        height=float(disp_h),
        scale=float(scale),
        opaque_area_px=opaque,
        native_w=native_w,
        native_h=native_h,
        slot_px=slot_px,
    )


def store_logo_scales_in_manifest(
    logo_dir: Path,
    scales: dict[int, float],
) -> None:
    """Merge per-team display ``scale`` into ``manifest.json`` when present."""
    path = logo_dir / MANIFEST_NAME
    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, list):
        return
    for row in payload:
        if not isinstance(row, dict):
            continue
        tid = row.get("team_id")
        if tid is None:
            continue
        if int(tid) in scales:
            row["display_scale"] = scales[int(tid)]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
