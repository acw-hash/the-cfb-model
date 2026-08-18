"""Temporary W9-1 bite runner. Do not commit. Reverts every mutation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SITE = ROOT / "webapp" / "site"
LOG = Path(__file__).with_name("bites.txt")
FIXTURE = ROOT / "webapp" / "fixtures" / "week_predictions.json"


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    use_shell = sys.platform == "win32" and cmd[0] in {"npx", "rg", "uv", "node"}
    proc = subprocess.run(
        cmd if not use_shell else subprocess.list2cmdline(cmd),
        cwd=cwd,
        capture_output=True,
        text=True,
        shell=use_shell,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def git_checkout(*rel: str) -> None:
    subprocess.check_call(["git", "checkout", "--", *rel], cwd=ROOT)


def log(fh, title: str, code: int, out: str) -> None:
    fh.write(f"\n======== {title} EXIT={code} ========\n")
    fh.write(out)
    if not out.endswith("\n"):
        fh.write("\n")
    fh.flush()
    print(f"{title} EXIT={code}", flush=True)


def main() -> int:
    LOG.write_text("W9-1 BITE TESTS\n", encoding="utf-8")
    fh = LOG.open("a", encoding="utf-8")

    # 1. payload projection
    proj = SITE / "src" / "lib" / "this-week" / "project.ts"
    text = proj.read_text(encoding="utf-8")
    needle = "    p_favored: game.conviction_basis?.p_favored ?? null,\n  };"
    poisoned = (
        "    p_favored: game.conviction_basis?.p_favored ?? null,\n"
        "    ...({ p_cover_home: 0.42 } as object),\n  };"
    )
    if needle not in text:
        raise SystemExit("project.ts needle missing")
    proj.write_text(text.replace(needle, poisoned, 1), encoding="utf-8")
    code, out = run(
        ["npx", "vitest", "run", "tests/payload-leak.test.tsx", "tests/this-week-project.test.ts"],
        SITE,
    )
    log(fh, "1 PAYLOAD FAIL", code, out)
    git_checkout("webapp/site/src/lib/this-week/project.ts")
    code, out = run(
        ["npx", "vitest", "run", "tests/payload-leak.test.tsx", "tests/this-week-project.test.ts"],
        SITE,
    )
    log(fh, "1 PAYLOAD PASS", code, out)

    # 2. gallery gate
    gate = SITE / "src" / "app" / "gallery" / "gallery-gate.ts"
    gtxt = gate.read_text(encoding="utf-8")
    gate.write_text(
        gtxt.replace(
            "return process.env.NODE_ENV !== \"production\";",
            "return true;",
        ),
        encoding="utf-8",
    )
    code, out = run(["npx", "vitest", "run", "tests/gallery-gate.test.ts"], SITE)
    log(fh, "2 GALLERY FAIL", code, out)
    git_checkout("webapp/site/src/app/gallery/gallery-gate.ts")
    code, out = run(["npx", "vitest", "run", "tests/gallery-gate.test.ts"], SITE)
    log(fh, "2 GALLERY PASS", code, out)

    # 3. demo-states import walk
    about = SITE / "src" / "app" / "about" / "page.tsx"
    atxt = about.read_text(encoding="utf-8")
    about.write_text(
        'import "@/lib/results/demo-states";\n' + atxt,
        encoding="utf-8",
    )
    code, out = run(
        ["npx", "vitest", "run", "tests/no-demo-states-in-production-routes.test.ts"],
        SITE,
    )
    log(fh, "3 DEMO-STATES FAIL", code, out)
    git_checkout("webapp/site/src/app/about/page.tsx")
    code, out = run(
        ["npx", "vitest", "run", "tests/no-demo-states-in-production-routes.test.ts"],
        SITE,
    )
    log(fh, "3 DEMO-STATES PASS", code, out)

    # 4. token guard
    tokens = SITE / "src" / "styles" / "tokens.css"
    ttxt = tokens.read_text(encoding="utf-8")
    tokens.write_text(ttxt.replace("--text-tertiary: #75757a;", "--text-tertiary: #aeaeb2;"), encoding="utf-8")
    code, out = run(["node", "scripts/check-tokens.mjs"], SITE)
    log(fh, "4 TOKEN FAIL", code, out)
    git_checkout("webapp/site/src/styles/tokens.css")
    code, out = run(["node", "scripts/check-tokens.mjs"], SITE)
    log(fh, "4 TOKEN PASS", code, out)

    # 5. consumed-or-withdrawn — poison 1.2.0 fixture game
    week = json.loads(FIXTURE.read_text(encoding="utf-8"))
    week["games"][0]["unsanctioned_edge"] = 0.03
    FIXTURE.write_text(json.dumps(week, indent=2) + "\n", encoding="utf-8")
    code, out = run(["npx", "vitest", "run", "tests/published-keys.test.ts"], SITE)
    log(fh, "5 CONSUMED-OR-WITHDRAWN FAIL", code, out)
    git_checkout("webapp/fixtures/week_predictions.json")
    code, out = run(["npx", "vitest", "run", "tests/published-keys.test.ts"], SITE)
    log(fh, "5 CONSUMED-OR-WITHDRAWN PASS", code, out)

    # 6. fixture as_of — kickoff before published_at
    week = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for g in week["games"]:
        if str(g["game_id"]) == "401628373":
            g["kickoff_utc"] = "2024-09-24T09:00:00Z"
            break
    FIXTURE.write_text(json.dumps(week, indent=2) + "\n", encoding="utf-8")
    code, out = run(
        [
            "uv",
            "run",
            "pytest",
            "tests/unit/test_webapp_w9r.py::test_fixture_401628373_as_of_precedes_its_kickoff",
            "tests/unit/test_webapp_w9r.py::test_week_predictions_as_of_precedes_every_kickoff",
            "-o",
            "addopts=",
            "-q",
            "--tb=short",
        ],
        ROOT,
    )
    log(fh, "6 FIXTURE AS_OF FAIL", code, out)
    git_checkout("webapp/fixtures/week_predictions.json")
    code, out = run(
        [
            "uv",
            "run",
            "pytest",
            "tests/unit/test_webapp_w9r.py::test_fixture_401628373_as_of_precedes_its_kickoff",
            "tests/unit/test_webapp_w9r.py::test_week_predictions_as_of_precedes_every_kickoff",
            "-o",
            "addopts=",
            "-q",
            "--tb=short",
        ],
        ROOT,
    )
    log(fh, "6 FIXTURE AS_OF PASS", code, out)

    # 7. union grep — site src currently clean; add recommendation copy
    copy = SITE / "src" / "lib" / "results" / "copy.ts"
    ctxt = copy.read_text(encoding="utf-8")
    code, out = run(
        [
            "rg",
            "-n",
            "-i",
            "--pcre2",
            r"best bet|yes bet|\bplay\b|edge vs market|\bunits\b|lock it in|must bet|recommended bet",
            "webapp/site/src",
        ],
        ROOT,
    )
    log(fh, "7 UNION GREP SITE-SRC BASELINE (expect no matches, rg exit 1)", code, out)
    copy.write_text(ctxt.replace("No betting edge", "best bet / recommended bet. No betting edge", 1), encoding="utf-8")
    code, out = run(
        [
            "rg",
            "-n",
            "-i",
            "--pcre2",
            r"best bet|yes bet|\bplay\b|edge vs market|\bunits\b|lock it in|must bet|recommended bet",
            "webapp/site/src",
        ],
        ROOT,
    )
    log(fh, "7 UNION GREP SITE-SRC FAIL (injected best bet)", code, out)
    git_checkout("webapp/site/src/lib/results/copy.ts")
    code, out = run(
        [
            "rg",
            "-n",
            "-i",
            "--pcre2",
            r"best bet|yes bet|\bplay\b|edge vs market|\bunits\b|lock it in|must bet|recommended bet",
            "webapp/site/src",
        ],
        ROOT,
    )
    log(fh, "7 UNION GREP SITE-SRC PASS AFTER REVERT (expect no matches)", code, out)
    code, out = run(
        [
            "uv",
            "run",
            "python",
            "scripts/check_betting_language.py",
        ],
        ROOT,
    )
    log(fh, "7 UNION GREP REPO-WIDE (STOP: existing copy, expect fail)", code, out)

    # 8. push.py allowlist
    week = json.loads(FIXTURE.read_text(encoding="utf-8"))
    week["games"][0]["p_cover_home"] = 0.42
    FIXTURE.write_text(json.dumps(week) + "\n", encoding="utf-8")
    code, out = run(
        [
            "uv",
            "run",
            "pytest",
            "tests/unit/test_webapp_w91.py::test_committed_fixtures_pass_push_allowlist",
            "-o",
            "addopts=",
            "-q",
            "--tb=short",
        ],
        ROOT,
    )
    log(fh, "8 PUSH ALLOWLIST FAIL", code, out)
    git_checkout("webapp/fixtures/week_predictions.json")
    code, out = run(
        [
            "uv",
            "run",
            "pytest",
            "tests/unit/test_webapp_w91.py::test_committed_fixtures_pass_push_allowlist",
            "-o",
            "addopts=",
            "-q",
            "--tb=short",
        ],
        ROOT,
    )
    log(fh, "8 PUSH ALLOWLIST PASS", code, out)

    fh.close()
    print(f"wrote {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
