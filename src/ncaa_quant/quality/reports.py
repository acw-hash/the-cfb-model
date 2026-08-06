"""Markdown / HTML quality summary reports."""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ncaa_quant.quality.runner import QualityRunResult


def write_reports(result: QualityRunResult, output_dir: Path | str) -> tuple[Path, Path]:
    """Write docs-friendly markdown and HTML summaries; return ``(md_path, html_path)``."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"quality_{result.run_id}"
    md_path = out / f"{stem}.md"
    html_path = out / f"{stem}.html"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    html_path.write_text(render_html(result), encoding="utf-8")
    return md_path, html_path


def render_markdown(result: QualityRunResult) -> str:
    """Render a markdown summary of a quality run."""
    lines: list[str] = [
        f"# Quality run `{result.run_id}`",
        "",
        f"- Seasons: {', '.join(str(s) for s in result.seasons)}",
        f"- Partitions checked: {result.partitions_checked}",
        f"- Passed: {result.partitions_passed}",
        f"- Quarantined: {result.partitions_quarantined}",
        f"- Flagged (soft): {result.partitions_flagged}",
        f"- Hard failures: {result.hard_failure_count}",
        f"- Soft flags: {result.flag_count}",
        "",
        "## Partition outcomes",
        "",
        "| table | season | week | status | hard | flags |",
        "|---|---:|---:|---|---:|---:|",
    ]
    for part in result.partitions:
        week = "" if part.week is None else str(part.week)
        lines.append(
            f"| {part.table} | {part.season} | {week} | {part.status} | "
            f"{part.hard_failures} | {part.flag_count} |"
        )

    lines.extend(["", "## Findings", ""])
    if not result.findings:
        lines.append("No findings.")
    else:
        for finding in result.findings:
            lines.append(
                f"- **[{finding.severity}]** `{finding.table}` "
                f"s{finding.season}"
                f"{'' if finding.week is None else f' w{finding.week}'} — "
                f"`{finding.expectation}`: {finding.message}"
            )
    lines.append("")
    return "\n".join(lines)


def render_html(result: QualityRunResult) -> str:
    """Render a minimal HTML summary of a quality run."""
    rows = []
    for part in result.partitions:
        week = "" if part.week is None else str(part.week)
        rows.append(
            "<tr>"
            f"<td>{html.escape(part.table)}</td>"
            f"<td>{part.season}</td>"
            f"<td>{html.escape(week)}</td>"
            f"<td>{html.escape(part.status)}</td>"
            f"<td>{part.hard_failures}</td>"
            f"<td>{part.flag_count}</td>"
            "</tr>"
        )
    finding_items = []
    for finding in result.findings:
        finding_items.append(
            "<li><strong>"
            f"[{html.escape(finding.severity)}]</strong> "
            f"<code>{html.escape(finding.table)}</code> "
            f"s{finding.season}"
            f"{'' if finding.week is None else f' w{finding.week}'} — "
            f"<code>{html.escape(finding.expectation)}</code>: "
            f"{html.escape(finding.message)}</li>"
        )
    findings_html = (
        "<p>No findings.</p>" if not finding_items else "<ul>" + "".join(finding_items) + "</ul>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Quality run {html.escape(result.run_id)}</title>
  <style>
    body {{ font-family: Georgia, serif; margin: 2rem; color: #1a1a1a; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }}
    th {{ background: #f3f3f3; }}
    code {{ font-family: Consolas, monospace; }}
  </style>
</head>
<body>
  <h1>Quality run <code>{html.escape(result.run_id)}</code></h1>
  <ul>
    <li>Seasons: {html.escape(", ".join(str(s) for s in result.seasons))}</li>
    <li>Partitions checked: {result.partitions_checked}</li>
    <li>Passed: {result.partitions_passed}</li>
    <li>Quarantined: {result.partitions_quarantined}</li>
    <li>Flagged (soft): {result.partitions_flagged}</li>
    <li>Hard failures: {result.hard_failure_count}</li>
    <li>Soft flags: {result.flag_count}</li>
  </ul>
  <h2>Partition outcomes</h2>
  <table>
    <thead><tr><th>table</th><th>season</th><th>week</th><th>status</th>
    <th>hard</th><th>flags</th></tr></thead>
    <tbody>
      {"".join(rows)}
    </tbody>
  </table>
  <h2>Findings</h2>
  {findings_html}
</body>
</html>
"""
