---
name: prereview-pipeline
description: Use this skill when the user wants a reliable staged local preview workflow for code review artifacts: prepare reviewer context, write anchor-based annotations, validate against current diff, and build static HTML.
---

# Prereview Pipeline

Use this workflow when the task is to generate local rich previews from agent-generated code changes.

## Trigger examples

- "Prepare context first, then annotate, then build review HTML"
- "Generate prereview output from my local changes"
- "Use staged workflow for code preview"

## Required staged flow

1. Run `prereview prepare-context --out review-context.json`.
2. Read `review-context.json` and author `annotations.json`.
3. Run `prereview validate-annotations --context review-context.json --annotations annotations.json --report validation-report.json`.
4. If validation fails, fix `annotations.json` and re-run validation.
5. Run `prereview build --context review-context.json --annotations annotations.json --output prereview.html`.

Never skip validation when annotations are edited.

## Input source selection

Choose one source for stage 1:

- Patch file: `--patch-file PATH`
- Commit range: `--git-range A..B`
- Default working tree vs `HEAD` if no source flags are given

Use `--include-untracked` only when the user explicitly wants new untracked files included.
Use `--exclude-path` to remove generated or irrelevant paths (for example `showcase/**`).
Binary diffs are excluded by default; only use `--include-binary` when the binary change itself is review-critical.
If context preparation fails due diff-size safeguards, narrow scope with `--exclude-path` before retrying.

## Annotation authoring guidance

- Use strict JSON schema version `2`.
- Set `target_context_id` to the exact `context_id` from `review-context.json`.
- Write reviewer-facing explanations against `anchor_id` only; do not reference line numbers/hunk coordinates.
- Add top-level `overview` with 2-4 concise lines for the header:
  scope, primary intent, and reviewer focus/risk.
- For every anchor include both:
  `what_changed` (concrete behavior/data/API change), and
  `why_changed` (intent, fix, reliability, maintainability, compatibility, etc.).
- Prefer hunk-level anchor explanations as the default.
- Keep high-severity notes rare (`warning`/`risk`) and reserve them for genuinely important reviewer attention points.

Read `references/annotation-schema.md` for exact field rules and `assets/annotations.template.json` for a ready template.

## Recovery rules

- If `validate-annotations` reports unknown anchors, regenerate context and update annotations.
- If context fingerprint mismatch is reported, regenerate context before validating/building.
- If build fails, do not patch output HTML directly; fix inputs and rebuild.
