# Annotation Schema v2

Top-level object:

- `version` (string, required): must be `"2"`
- `target_context_id` (string, required): must match `context_id`
- `overview` (array[string], optional): 2-4 reviewer-facing header lines
- `files` (array, required)

`files[]` object:

- `path` (string, required)
- `summary` (string, optional)
- `anchors` (array, required)

`anchors[]` object:

- `anchor_id` (string, required)
- `title` (string, optional)
- `what_changed` (string, required)
- `why_changed` (string, required)
- `reviewer_focus` (string, optional)
- `risk` (string, optional)
- `severity` (string, optional): one of `info`, `note`, `warning`, `risk`

Guidance:

- Prefer anchor/hunk-level explanations for most cases.
- Keep high-severity notes rare.
- Do not reference line numbers or diff hunk coordinates in explanation text.
- For each anchor, include both **what changed** and **why**.
