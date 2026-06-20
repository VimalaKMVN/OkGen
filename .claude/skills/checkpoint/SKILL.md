---
name: checkpoint
description: Save or restore tagged checkpoints of the OkGen repo. Use when the user wants to commit + push + tag the current state as a named checkpoint, list existing checkpoints, or roll back to a previous tag. Triggers include "checkpoint", "save progress", "tag this", "roll back to <tag>", "restore <tag>".
---

# Checkpoint

Manage tagged checkpoints for the OkGen repo so the user can save progress and roll back when they don't like a direction. This repo works **directly on `main`** — no branches.

Parse the user's intent into one of three modes: **save**, **list**, or **rollback**.

## Mode: save (default)

When the user wants to checkpoint current progress (e.g. `/checkpoint`, `/checkpoint <message>`, "save this", "tag this as v1").

1. Run `git status --short` to see what's changed.
2. If a tag name was given, use it. Otherwise pick a sequential default: find the highest existing `cp-N` tag (`git tag -l 'cp-*'`) and use `cp-<N+1>` (start at `cp-1`). Tag names must be valid git refs (no spaces — kebab-case them).
3. Stage and commit everything on `main`:
   - `git add -A`
   - `git commit -m "<message>"` — use the user's message, or `checkpoint: <tag>` if none given. If there's nothing to commit, skip the commit and just tag the current HEAD (tell the user it's an empty-changes checkpoint pointing at the existing commit).
4. Create an annotated tag: `git tag -a <tag> -m "<message>"`.
5. Push both the commit and the tag: `git push origin main` and `git push origin <tag>`.
6. Report back: the tag name, the commit short SHA, and the one-line message. Remind the user they can roll back with `/checkpoint rollback <tag>`.

## Mode: list

When the user wants to see checkpoints (`/checkpoint list`, "what checkpoints do I have").

Run `git tag -l --sort=-creatordate --format='%(refname:short)  %(creatordate:short)  %(contents:subject)'` and present the tags newest-first with their dates and messages.

## Mode: rollback

When the user wants to restore a previous state (`/checkpoint rollback <tag>`, "roll back to cp-3", "restore <tag>").

1. **Confirm the target exists**: `git rev-parse <tag>` — if it fails, run `git tag -l` and ask the user which tag they meant. Do not guess.
2. **Warn about uncommitted work**: run `git status --short`. If the tree is dirty, tell the user rollback will discard those changes and ask them to confirm (or offer to checkpoint first).
3. **Confirm intent before rewriting history** — rollback is destructive to commits made after the tag. State plainly what will be lost (e.g. "this will move main back to cp-3 and discard the 2 commits after it") and get a clear yes.
4. Perform the rollback by moving `main` to the tag:
   - `git reset --hard <tag>`
   - Push the rewind: `git push --force-with-lease origin main`
5. Report the new HEAD (short SHA + message) and confirm the rollback is on the remote.

### Notes
- Use `--force-with-lease`, never plain `--force`, so a surprise remote update aborts the push instead of clobbering it.
- The commits after the tag aren't truly gone immediately — they're reachable via `git reflog` until garbage-collected. Mention this if the user has second thoughts.
- Never delete tags unless the user explicitly asks.
