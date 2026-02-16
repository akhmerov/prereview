# Annotation Notes Schema v1

Top-level object:

- `version` (string, required): must be `"1"`
- `target_context_id` (string, required): must match `context_id`
- `overview` (array[string], optional): 2-4 reviewer-facing header lines
- `anchors` (array, required)
- `file_summaries` (array, optional)

`anchors[]` object:

- `anchor_id` (string, required): must exist in `review-context.json`
- `title` (string, optional)
- `what_changed` (string, required)
- `why_changed` (string, required)
- `reviewer_focus` (string, optional)
- `risk` (string, optional)
- `severity` (string, optional): one of `info`, `note`, `warning`, `risk`

`file_summaries[]` object:

- `path` (string, required): file path from `review-context.json`
- `summary` (string, required)

Guidance:

- Prefer anchor-level explanations for most cases.
- Keep high-severity notes rare.
- Do not reference line numbers or diff hunk coordinates in explanation text.
- For each anchor, include both **what changed** and **why**.
- Do not restate computable diff facts (counts, line numbers, file statuses).
