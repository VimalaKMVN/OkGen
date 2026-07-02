---
name: close_shop
description: End-of-session close-out for the OkGen repo — update PLAN.md, reconcile persistent memory (add new + fix stale), then checkpoint+push (confirm first). Use when the user says "close shop", "close the session", "wrap up", "we're done for today", or asks to make the next session ramp faster.
---

# close_shop — end-of-session close-out for OkGen

A thin orchestrator over tools that already exist. Its job is to make the **next**
fresh session (human or AI) start fast and correct: the repo's living docs and the
cross-session memory must reflect reality, and the work must be safely checkpointed.

Do NOT reinvent commit/tag/push — delegate that to the **checkpoint** skill.
Only persist things that are NOT already recoverable from the repo/git history
(don't duplicate code structure, file lists, or past fixes into memory).

Run the steps in order. Steps 1–3 gather and propose; **step 4 pushes only after
the user confirms** (that was the chosen default).

## 1. Take stock of the session

- `git -C "$(git rev-parse --show-toplevel)" status --porcelain` and
  `git ... diff --stat` — what actually changed this session.
- Run the tests so the recorded count is real:
  `.venv/bin/python -m pytest tests/ -q` (report pass count).
- Note any durable decisions made, threads opened/closed, or gotchas discovered.

## 2. Update PLAN.md (the read-first tracker)

PLAN.md §4 is the single source of truth for **current top-of-main tag + test
count + feature note**. Update it. Also:
- Add a row to **§3 (decision log)** only if a durable decision with rationale was made.
- Tick / add items in **§6 (next increments / open threads)** if they changed.
Keep it tight; PLAN.md links out to the deeper docs — don't duplicate them.

## 3. Reconcile persistent memory (highest-value step)

Memory dir: `/Users/praveendx/.claude/projects/-Users-praveendx-repos-OkGen/memory/`
(index file `MEMORY.md`). For this session:
- **Add** what's newly non-obvious and not derivable from the repo (a workflow
  gotcha, an environment quirk, a confirmed approach + why). One fact per file,
  with frontmatter; add a one-line pointer to `MEMORY.md`.
- **Reconcile / fix stale** entries — re-read the existing memories and correct any
  that this session made wrong (e.g. a hardcoded tag/state that has moved on).
  This is the step that silently rots if skipped; do it every time.
- **Delete** memories proven wrong.
Prefer updating an existing file over creating a near-duplicate.

## 4. Checkpoint + push (confirm first)

- Propose the version bump + label and show the user exactly what will be
  committed and tagged (`git status` summary + proposed `vMAJOR.MINOR.PATCH-label`).
- **Wait for the user's explicit OK.** Only then invoke the **checkpoint** skill
  with `push` (e.g. `/checkpoint <label> push`), which handles commit + annotated
  tag + push. Pushing this repo requires the **VimalaKMVN** GitHub account, not the
  default `praveendx` — the checkpoint/push must switch accounts:
  `gh auth switch --user VimalaKMVN && gh auth setup-git` → push branch + tag →
  verify with `git ls-remote --tags origin <tag>` → restore
  `gh auth switch --user praveendx && gh auth setup-git`.
  (Symptom if skipped: HTTP 403 "Permission … denied to praveendx".)

## 5. Print the "next session starts here" summary

One short block: the new top-of-main tag, test count, what shipped this session,
and the top open thread from PLAN.md §6 — so the next session's first read is trivial.
