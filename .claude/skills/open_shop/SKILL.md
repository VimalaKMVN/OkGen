---
name: open_shop
description: Start-of-session ramp-up for the OkGen repo — load PLAN.md, VERIFY the repo/docs/tests actually match reality (clean tree, on main, recorded tag + test count are real, no remote drift), absorb the durable decisions/gotchas, then print a "you are here" briefing and propose next work. Use when the user says "open shop", "start the session", "ramp me up", "where are we", "what's next", or at the top of a fresh session before taking on an enhancement.
---

# open_shop — start-of-session ramp-up for OkGen

The symmetric partner to **close_shop**. `close_shop` guarantees a session *ends*
with PLAN.md + memory + checkpoint in sync; `open_shop` guarantees a session
*starts* by loading that state, **verifying it is actually true**, and briefing
you — so you never begin work from a doc that has drifted from the real repo.

Its value is NOT re-reading what already auto-loads (CLAUDE.md, memory index).
It is the **active verification + briefing** a fresh session usually skips.
Read-only by design: `open_shop` inspects and runs tests, but NEVER commits,
pushes, tags, or edits files. If it finds drift, it surfaces it loudly instead of
quietly proceeding.

Run the steps in order. Steps 1–4 gather and verify; step 5 briefs and proposes.

## 1. Load the map (PLAN.md is read-first)

- Read `PLAN.md` end to end — §1 what it is, §2 architecture seam + key files,
  §3 decision log (the durable "why"), §4 current state (top-of-main tag + test
  count + feature inventory), §6 next increments / open threads.
- PLAN.md links deeper docs (ARCHITECTURE / IMPLEMENTATION_PLAN / DEVELOPMENT_PROCESS);
  only open those if the task at hand needs them.

## 2. Verify the repo matches the doc

```bash
ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" rev-parse --abbrev-ref HEAD     # expect: main (this repo works directly on main)
git -C "$ROOT" status --porcelain              # expect: clean; if not, report what's dirty
git -C "$ROOT" tag --points-at HEAD            # the top-of-main tag(s)
git -C "$ROOT" log --oneline -5
```
- Confirm the tag on HEAD **matches** the tag PLAN.md §4 claims. If they differ,
  the doc drifted — flag it and trust git, not §4.
- If the tree is dirty or HEAD is detached / not on `main`, say so up front — that
  is the first thing to reconcile before new work.

## 3. Set the GitHub account to VimalaKMVN (always) + check remote drift

**OkGen uses the `VimalaKMVN` GitHub account ONLY — always, every session.** The
other logged-in account, `praveendx`, has NO access to this repo (404/403 —
"Repository not found"). Make VimalaKMVN active at the start of the session and
**leave it active** — do NOT switch back to praveendx at any point (an older
version of this skill did; that was wrong — see the github-account memory):
```bash
gh auth switch --user VimalaKMVN && gh auth setup-git   # set once; leave it active
```
`switch` alone is not enough — always run `setup-git` too so git's credential
helper picks up the token.

Then, if another machine/session might have advanced the remote, check drift
(read-only):
```bash
git -C "$ROOT" fetch origin --tags --quiet
git -C "$ROOT" rev-list --left-right --count origin/main...main   # behind<TAB>ahead
```
Report if local `main` is behind (pull before working) or ahead (unpushed commits).
Skip the fetch for a quick solo ramp; do it when collaboration/other machines are
in play. Either way, still set VimalaKMVN active — it is the standing account for
every push/pull/PR this session.

## 4. Prove the baseline is green + absorb the "why"

- **Run the tests — don't trust the recorded count:**
  `.venv/bin/python -m pytest tests/ -q` (report pass count; compare to PLAN §4).
  A mismatch or any failure is the real starting point — surface it before anything else.
- Sanity-check the env can serve if the task will need it:
  `PYTHONPATH=src .venv/bin/python -c "import okgen"` (deeper: `okgen serve`).
- Skim the memory index (`MEMORY.md`) and internalize the **workflow gotchas** that
  are easy to violate: commit directly to `main` (no branches); push/PR only as
  **VimalaKMVN**; edit layout `.xlsx` via targeted XML (or fresh openpyxl for the
  delimited EU layouts) — see [[okgen-xlsx-edit-technique]]; the stack is Flask +
  vanilla JS (not FastAPI/React).
- Note the §3 decision log so new work respects prior rationale (byte-exact
  round-trip, config-driven behavior, delimited engine, chain isolation, …).

## 5. Brief + propose (the payoff)

Print ONE tight "you are here" block:
- **Top of main**: tag + short commit (verified from git, not the doc).
- **Tests**: the count you actually just ran.
- **Last shipped**: the one-line feature note from PLAN §4.
- **Open threads**: the top 1–3 items from PLAN §6, most actionable first.
- **Drift/issues found**: anything from steps 2–4 that needs attention (dirty tree,
  tag mismatch, failing tests, behind remote) — or "none, clean start."

Then ask what enhancement to take on (or, if the user already said, restate the
scope and confirm before diving in). Do NOT start editing during ramp-up — end by
handing the user a clear starting point and a proposed next action.

## Guardrails

- **Read-only.** open_shop may run tests and read git/files; it must not commit,
  push, tag, branch, or edit. (Setting the gh account to VimalaKMVN and the remote
  *fetch* in step 3 are the only side effects — the account is left ON VimalaKMVN,
  never restored to praveendx.)
- Trust **git + a real test run** over what any doc claims; when they disagree, the
  doc is stale — report it (and it becomes a close_shop fix later).
- Keep the briefing short. The goal is a fast, correct start — not a wall of text.
