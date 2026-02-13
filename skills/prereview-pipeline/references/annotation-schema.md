# Annotation Schema v1

Top-level object:

- `version` (string, required): must be `"1"`
- `target_prepared_review` (string, required): must match `prepared_id`
- `overview` (array[string], optional): 2-4 reviewer-facing header lines (scope, intent, focus/risk)
- `files` (array, required)

`files[]` object:

- `path` (string, required)
- `breadcrumbs` (array[string], optional)
- `summary` (string, optional)
- `comments` (array, optional)
- `hunks` (array, optional)

Comment object (`files[].comments[]`, `files[].hunks[].comments[]`):

- `line_start` (int, required)
- `line_end` (int, optional)
- `text` (string, required)
- `severity` (string, optional): one of `info`, `note`, `warning`, `risk`
- `author` (string, optional)
- `tags` (array[string], optional)

Guidance:

- Prefer hunk-level explanations for most cases.
- Use line-level comments only for rare high-importance anchors.
- For each explanation/comment, include both **what changed** and **why**.

Hunk annotation object (`files[].hunks[]`):

- `hunk_id` (string, optional)
- `new_start` (int, optional)
- `new_end` (int, optional)
- `title` (string, optional)
- `explanation` (string, optional)
- `comments` (array, optional)

A hunk annotation must include either `hunk_id`, or both `new_start` and `new_end`.
