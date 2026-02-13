from __future__ import annotations

import html
import json
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _line_number(value: Any) -> str:
    return "" if value is None else str(value)


def _hunk_range(start: Any, count: Any, prefix: str) -> str:
    if not isinstance(start, int):
        return "?"
    span = count if isinstance(count, int) else 1
    if span < 1:
        span = 1
    end = start + span - 1
    if end == start:
        return f"{prefix}{start}"
    return f"{prefix}{start}-{end}"


def _normalize_file_summary(path: str, summary: Any) -> str | None:
    if not isinstance(summary, str):
        return None
    text = summary.strip()
    if not text:
        return None

    candidates = [path, path.split("/")[-1]]
    lower_text = text.lower()
    for candidate in candidates:
        token = candidate.strip()
        if not token:
            continue
        lower_token = token.lower()
        if lower_text == lower_token:
            return None
        if lower_text.startswith(lower_token):
            remainder = text[len(token) :].lstrip(" \t:-|—–")
            return remainder or None
    return text


def _json_for_html_script(payload: Any) -> str:
    # Avoid closing script tags from embedded JSON text.
    return json.dumps(payload, sort_keys=True).replace("</", "<\\/")


def _comment_range(comment: dict[str, Any]) -> tuple[int | None, int | None]:
    start = comment.get("line_start")
    end = comment.get("line_end", start)
    if isinstance(start, int) and isinstance(end, int):
        if end < start:
            return end, start
        return start, end
    return None, None


def _comments_by_line(file_annotation: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    by_line: dict[int, list[dict[str, Any]]] = {}

    def add_comment(comment: dict[str, Any], source: str) -> None:
        start, end = _comment_range(comment)
        if start is None or end is None:
            return
        comment_with_source = dict(comment)
        comment_with_source["_source"] = source
        by_line.setdefault(start, []).append(comment_with_source)

    for comment in file_annotation.get("comments", []):
        if isinstance(comment, dict):
            add_comment(comment, "file")

    for hunk in file_annotation.get("hunks", []):
        if not isinstance(hunk, dict):
            continue
        for comment in hunk.get("comments", []):
            if isinstance(comment, dict):
                add_comment(comment, "hunk")

    return by_line


def _hunk_annotations(
    file_annotation: dict[str, Any],
    hunk: dict[str, Any],
    allow_split_hunks: bool,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    hunk_id = hunk.get("hunk_id")
    hunk_start = hunk.get("new_start")
    hunk_end = None
    if isinstance(hunk_start, int):
        hunk_end = hunk_start + max(int(hunk.get("new_count", 1)) - 1, 0)

    for hunk_annotation in file_annotation.get("hunks", []):
        if not isinstance(hunk_annotation, dict):
            continue
        if hunk_id and hunk_annotation.get("hunk_id") == hunk_id:
            selected.append(hunk_annotation)
            continue
        if not allow_split_hunks:
            continue

        new_start = hunk_annotation.get("new_start")
        new_end = hunk_annotation.get("new_end")
        if isinstance(new_start, int) and isinstance(new_end, int) and isinstance(hunk_start, int) and isinstance(hunk_end, int):
            if new_end < new_start:
                new_start, new_end = new_end, new_start
            if new_start <= hunk_end and new_end >= hunk_start:
                selected.append(hunk_annotation)

    return selected


def render_html(
    prepared: dict[str, Any],
    annotations: dict[str, Any],
    validation_report: dict[str, Any],
    *,
    title: str,
    max_expanded_lines: int,
    collapse_large_hunks: bool,
    allow_split_hunks: bool,
    embedded_data: dict[str, Any] | None = None,
) -> str:
    prepared_stats = prepared.get("stats", {})
    files = prepared.get("files", [])
    overview = annotations.get("overview", [])

    file_annotations: dict[str, dict[str, Any]] = {}
    for file_annotation in annotations.get("files", []):
        if isinstance(file_annotation, dict) and isinstance(file_annotation.get("path"), str):
            file_annotations[file_annotation["path"]] = file_annotation

    issues = validation_report.get("issues", [])
    error_count = sum(1 for issue in issues if issue.get("level") == "error")
    warning_count = sum(1 for issue in issues if issue.get("level") == "warning")

    html_chunks: list[str] = []
    html_chunks.append("<!doctype html>")
    html_chunks.append("<html lang='en'>")
    html_chunks.append("<head>")
    html_chunks.append("<meta charset='utf-8'>")
    html_chunks.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    html_chunks.append(f"<title>{_esc(title)}</title>")
    html_chunks.append(
        """
<style>
:root {
  --bg: #f4f7fb;
  --panel: #ffffff;
  --ink: #13202d;
  --subtle: #4e6172;
  --border: #d7e0ea;
  --add-bg: #e9f7ef;
  --add-ink: #185f39;
  --del-bg: #fdeeee;
  --del-ink: #81252e;
  --comment-bg: #f8f4df;
  --comment-ink: #5b4a13;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
  color: var(--ink);
  background: radial-gradient(circle at 15% 15%, #ffffff, var(--bg) 45%, #e9eef4);
}
main {
  max-width: 1100px;
  margin: 0 auto;
  padding: 1.25rem;
}
header {
  background: linear-gradient(125deg, #fefefe, #e7eef7);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 1rem;
  margin-bottom: 1rem;
}
.headline-stats {
  margin: 0.2rem 0 0;
  font-size: 0.8rem;
  color: var(--subtle);
}
.overview {
  margin-top: 0.85rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fff;
  padding: 0.65rem 0.85rem;
}
.overview h2 {
  margin: 0;
  font-size: 0.95rem;
}
.overview ul {
  margin: 0.35rem 0 0;
  padding-left: 1.15rem;
}
.files {
  display: grid;
  gap: 1.25rem;
}
.file {
  background: var(--panel);
  border: 1px solid #c4d2e2;
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 3px 10px rgba(17, 36, 56, 0.06);
}
.file-header {
  padding: 0.95rem;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, #fcfeff, #f2f7fc);
}
.file-dir {
  font-family: "IBM Plex Mono", "Consolas", monospace;
  font-size: 0.76rem;
  color: #597087;
  margin-bottom: 0.2rem;
}
.file-name {
  font-family: "IBM Plex Mono", "Consolas", monospace;
  font-size: 1.08rem;
  font-weight: 700;
  letter-spacing: 0.01em;
}
.summary {
  color: #4f3206;
  margin-top: 0.5rem;
  font-size: 0.95rem;
  line-height: 1.45;
  border: 1px solid #e6d8b8;
  border-top: 3px solid #be8b33;
  background: #fff8ea;
  border-radius: 8px;
  padding: 0.48rem 0.66rem;
}
.status {
  float: right;
  font-size: 0.8rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
  text-transform: uppercase;
}
.hunk {
  border-top: 1px solid var(--border);
}
.hunk > summary {
  padding: 0.55rem 0.8rem;
  cursor: pointer;
  font-family: "IBM Plex Mono", "Consolas", monospace;
  background: #f7faff;
  display: flex;
  justify-content: space-between;
  gap: 0.8rem;
  align-items: baseline;
}
.hunk-summary-meta {
  font-size: 0.75rem;
  color: var(--subtle);
}
.hunk-notes {
  margin: 0.5rem 0.65rem 0.35rem;
  border-left: 5px solid #5f89bf;
  background: #edf5ff;
  padding: 0.5rem 0.7rem;
  border-radius: 6px;
  box-shadow: inset 0 0 0 1px #d2e0f0;
}
.hunk-notes div {
  font-size: 0.9rem;
  color: #132536;
}
.diff-scroll {
  max-height: min(70vh, 44rem);
  overflow-y: auto;
  overflow-x: auto;
}
.diff-table {
  width: 100%;
  border-collapse: collapse;
}
.diff-table td {
  vertical-align: top;
  padding: 0.15rem 0.45rem;
  border-top: 1px solid #eef3f8;
  font-family: "IBM Plex Mono", "Consolas", monospace;
  font-size: 0.86rem;
}
.diff-table td.code {
  white-space: pre;
  tab-size: 4;
}
.diff-prefix {
  display: inline-block;
  width: 1ch;
}
.diff-table .num {
  width: 2.2rem;
  text-align: right;
  color: #8ea0b1;
  font-size: 0.72rem;
  font-weight: 400;
  user-select: none;
}
.line-add td { background: var(--add-bg); color: var(--add-ink); }
.line-del td { background: var(--del-bg); color: var(--del-ink); }
.comment-row td {
  background: #fffdf4;
  border-top: none;
  padding: 0.35rem 0.55rem 0.55rem;
}
.comment {
  border: 1px solid #e6dcaa;
  background: var(--comment-bg);
  color: var(--comment-ink);
  border-radius: 8px;
  padding: 0.35rem 0.5rem;
  margin-top: 0.2rem;
}
.comment-meta {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  opacity: 0.95;
  margin-bottom: 0.2rem;
}
.comment-severity-warning { color: #8f4b08; }
.comment-severity-risk { color: #7a1d1d; }
.comment-severity-note,
.comment-severity-info { color: #5b4a13; }
.validation {
  margin-top: 0.8rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fff;
  padding: 0.6rem 0.8rem;
}
.validation ul {
  margin: 0.35rem 0 0;
  padding-left: 1.1rem;
}
@media (max-width: 700px) {
  main { padding: 0.8rem; }
  .diff-table td { font-size: 0.8rem; }
}
</style>
"""
    )
    html_chunks.append("</head>")
    html_chunks.append("<body>")
    html_chunks.append("<main>")
    html_chunks.append("<header>")
    html_chunks.append(f"<h1>{_esc(title)}</h1>")
    html_chunks.append(
        "<p class='headline-stats'>"
        f"{_esc(prepared_stats.get('files_changed', 0))} files "
        f"· +{_esc(prepared_stats.get('additions', 0))} "
        f"· -{_esc(prepared_stats.get('deletions', 0))}"
        f" · {error_count} errors, {warning_count} warnings"
        "</p>"
    )

    overview_lines = []
    if isinstance(overview, list):
        overview_lines = [line for line in overview if isinstance(line, str) and line.strip()]
    if overview_lines:
        html_chunks.append("<section class='overview'>")
        html_chunks.append("<h2>Review Overview</h2>")
        html_chunks.append("<ul>")
        for line in overview_lines[:8]:
            html_chunks.append(f"<li>{_esc(line)}</li>")
        html_chunks.append("</ul>")
        html_chunks.append("</section>")

    if issues:
        html_chunks.append("<section class='validation'>")
        html_chunks.append(
            f"<strong>Validation:</strong> {error_count} errors, {warning_count} warnings"
        )
        html_chunks.append("<ul>")
        for issue in issues[:25]:
            html_chunks.append(
                f"<li><code>{_esc(issue.get('level', 'warning'))}</code> {_esc(issue.get('code', 'issue'))}: {_esc(issue.get('message', ''))} <em>{_esc(issue.get('location', ''))}</em></li>"
            )
        if len(issues) > 25:
            html_chunks.append(f"<li>... {len(issues) - 25} more issues</li>")
        html_chunks.append("</ul></section>")

    html_chunks.append("</header>")

    html_chunks.append("<section class='files'>")
    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        path = str(file_entry.get("path", "unknown"))
        file_annotation = file_annotations.get(path, {})
        path_parts = [part for part in path.split("/") if part]
        file_name = path_parts[-1] if path_parts else path
        file_dir = "/".join(path_parts[:-1])

        html_chunks.append("<article class='file'>")
        html_chunks.append("<div class='file-header'>")
        html_chunks.append(f"<span class='status'>{_esc(file_entry.get('status', 'modified'))}</span>")
        if file_dir:
            html_chunks.append(f"<div class='file-dir'>{_esc(file_dir)}/</div>")
        html_chunks.append(f"<div class='file-name'>{_esc(file_name)}</div>")
        summary_text = _normalize_file_summary(path, file_annotation.get("summary"))
        if summary_text:
            html_chunks.append(f"<div class='summary'>{_esc(summary_text)}</div>")
        html_chunks.append("</div>")

        if file_entry.get("is_binary"):
            html_chunks.append("<div class='summary' style='padding:0.8rem;'>Binary file changed.</div>")
            html_chunks.append("</article>")
            continue

        comments_by_line = _comments_by_line(file_annotation)
        for hunk in file_entry.get("hunks", []):
            if not isinstance(hunk, dict):
                continue
            hunk_annotations = _hunk_annotations(file_annotation, hunk, allow_split_hunks)
            lines = hunk.get("lines", [])
            collapsed = bool(collapse_large_hunks and isinstance(lines, list) and len(lines) > max_expanded_lines)
            added_lines = sum(
                1
                for line in (lines if isinstance(lines, list) else [])
                if isinstance(line, dict) and line.get("type") == "add"
            )
            removed_lines = sum(
                1
                for line in (lines if isinstance(lines, list) else [])
                if isinstance(line, dict) and line.get("type") == "del"
            )
            new_range = _hunk_range(hunk.get("new_start"), hunk.get("new_count"), "+")
            old_range = _hunk_range(hunk.get("old_start"), hunk.get("old_count"), "-")
            summary_label = f"Change {new_range} (from {old_range})"
            for hunk_annotation in hunk_annotations:
                title_text = hunk_annotation.get("title")
                if isinstance(title_text, str) and title_text.strip():
                    summary_label = title_text.strip()
                    break

            details_open = "" if collapsed else " open"
            html_chunks.append(f"<details class='hunk'{details_open}>")
            html_chunks.append(
                f"<summary><span>{_esc(summary_label)}</span>"
                f"<span class='hunk-summary-meta'>+{added_lines} / -{removed_lines}</span></summary>"
            )

            for hunk_annotation in hunk_annotations:
                explanation = hunk_annotation.get("explanation")
                if isinstance(explanation, str) and explanation.strip():
                    html_chunks.append("<section class='hunk-notes'>")
                    html_chunks.append(f"<div>{_esc(explanation)}</div>")
                    html_chunks.append("</section>")

            html_chunks.append("<div class='diff-scroll'>")
            html_chunks.append("<table class='diff-table'>")
            for line in lines if isinstance(lines, list) else []:
                if not isinstance(line, dict):
                    continue
                line_type = str(line.get("type", "context"))
                class_name = ""
                symbol = " "
                if line_type == "add":
                    class_name = "line-add"
                    symbol = "+"
                elif line_type == "del":
                    class_name = "line-del"
                    symbol = "-"

                old_no = _line_number(line.get("old_line"))
                new_no = _line_number(line.get("new_line"))
                content = _esc(line.get("content", ""))
                html_chunks.append(
                    f"<tr class='{class_name}'><td class='num'>{_esc(old_no)}</td><td class='num'>{_esc(new_no)}</td><td class='code'><span class='diff-prefix'>{symbol}</span>{content}</td></tr>"
                )

                new_line = line.get("new_line")
                if isinstance(new_line, int) and new_line in comments_by_line:
                    for comment in comments_by_line[new_line]:
                        severity = str(comment.get("severity", "info")).strip().lower() or "info"
                        text = _esc(comment.get("text", ""))
                        html_chunks.append(
                            "<tr class='comment-row'><td colspan='3'>"
                            f"<div class='comment'><div class='comment-meta comment-severity-{_esc(severity)}'>{_esc(severity)}</div>"
                            f"<div>{text}</div></div></td></tr>"
                        )

            html_chunks.append("</table>")
            html_chunks.append("</div>")
            html_chunks.append("</details>")

        html_chunks.append("</article>")

    html_chunks.append("</section>")
    html_chunks.append("</main>")
    if embedded_data is not None:
        html_chunks.append("<script id='prereview-embedded-data' type='application/json'>")
        html_chunks.append(_json_for_html_script(embedded_data))
        html_chunks.append("</script>")
    html_chunks.append("</body>")
    html_chunks.append("</html>")
    return "\n".join(html_chunks)
