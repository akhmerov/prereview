# Annotation Schema v1

Top-level object:

- `version` (string, required): must be `"1"`
- `target_prepared_review` (string, required): must match `prepared_id`
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

Hunk annotation object (`files[].hunks[]`):

- `hunk_id` (string, optional)
- `new_start` (int, optional)
- `new_end` (int, optional)
- `title` (string, optional)
- `explanation` (string, optional)
- `comments` (array, optional)

A hunk annotation must include either `hunk_id`, or both `new_start` and `new_end`.
