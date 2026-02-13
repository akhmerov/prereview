---
name: prereview-pipeline
description: Use this skill when the user wants a reliable staged local preview workflow for code review artifacts: prepare diff, write annotations, validate anchors, and build static HTML.
---

# Prereview Pipeline

Use this workflow when the task is to generate local rich previews from agent-generated code changes.

## Trigger examples

- "Prepare diff first, then annotate, then build review HTML"
- "Generate prereview output from my local changes"
- "Use staged workflow for code preview"

## Required staged flow

1. Run `prereview prepare-diff --out prepared-review.json`.
2. Read `prepared-review.json` and author `annotations.json`.
3. Run `prereview validate-annotations --prepared prepared-review.json --annotations annotations.json --report validation-report.json`.
4. If validation fails, fix `annotations.json` and re-run validation.
5. Run `prereview build --prepared prepared-review.json --annotations annotations.json --output prereview.html`.

Never skip validation when annotations are edited.

## Input source selection

Choose one source for stage 1:

- Patch file: `--patch-file PATH`
- Stdin patch: `--stdin-patch`
- Commit range: `--git-range A..B`
- Default working tree vs `HEAD` if no source flags are given

Use `--include-untracked` only when the user explicitly wants new untracked files included.

## Annotation authoring guidance

- Use strict JSON schema version `1`.
- Set `target_prepared_review` to the exact `prepared_id` from `prepared-review.json`.
- Add top-level `overview` with 2-4 concise lines for the header:
  scope (files/line delta), primary intent, and reviewer focus/risk.
- Anchor comments to changed **new-file lines** using `line_start` and optional `line_end`.
- Write each comment to explain both:
  what changed in concrete terms (logic, API, data flow, or behavior), and
  why it changed (intent, bug fix, reliability, performance, readability, or compatibility).
- Avoid comments that only restate line text without rationale.
- Prefer hunk-level explanations as the default.
- Keep line-level comments rare: add them only for high-importance anchors (behavioral risk, parsing/validation changes, security/safety, or error handling changes).
- Keep comments concise and specific; use `severity` when needed (`info`, `note`, `warning`, `risk`).
- Add optional per-file `breadcrumbs` and `summary` for richer rendering.

Read `references/annotation-schema.md` for exact field rules and `assets/annotations.template.json` for a ready template.

## Recovery rules

- If `validate-annotations` reports unmapped anchors, update line anchors first.
- If prepared target mismatch is reported, regenerate annotations against the current prepared file.
- If build fails, do not patch output HTML directly; fix inputs and rebuild.
