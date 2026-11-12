"""POST odds Worker /run once; never print the secret."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent / "worker_run.out"
ROOT = Path(__file__).resolve().parents[4]
ENV = ROOT / ".env"
URL = "https://ridge-odds-refresh.ridge-cfb.workers.dev/run"


def main() -> None:
    env: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    secret = env["MANUAL_RUN_SECRET"]
    now = datetime.now(tz=UTC).isoformat()
    print("now_utc", now)
    print("host", "ridge-odds-refresh.ridge-cfb.workers.dev")
    r = httpx.post(URL, headers={"Authorization": f"Bearer {secret}"}, timeout=120)
    print("http_status", r.status_code)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:800]}
    print(json.dumps(body, indent=2, default=str))
    OUT.write_text(
        f"now_utc {now}\nhttp_status {r.status_code}\nhost ridge-odds-refresh.ridge-cfb.workers.dev\n"
        + json.dumps(body, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
