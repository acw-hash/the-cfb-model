"""Fetch local Next preview pages and dump visible text. No production revalidation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

ART = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:3010"
PAGES = {
    "this_week": "/",
    "game_detail": "/game/401858424",
    "results": "/results",
    "about": "/about",
}


def visible_text(html: str) -> str:
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&#x27;|&apos;", "'", html)
    html = re.sub(r"&ldquo;|&rdquo;|&quot;", '"', html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def main() -> None:
    out: dict[str, object] = {}
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for name, path in PAGES.items():
            response = client.get(BASE + path)
            text = visible_text(response.text)
            (ART / f"render_{name}.html").write_text(response.text, encoding="utf-8")
            (ART / f"render_{name}.txt").write_text(text + "\n", encoding="utf-8")
            out[name] = {
                "url": BASE + path,
                "status": response.status_code,
                "n_chars": len(text),
                "has_fixture_banner": "FIXTURE DATA" in response.text,
                "has_maintenance": "Maintenance" in text or "unavailable" in text.lower(),
            }
            print(
                f"{name} status={response.status_code} "
                f"fixture_banner={out[name]['has_fixture_banner']} n_chars={len(text)}",
                flush=True,
            )
    (ART / "render_summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
