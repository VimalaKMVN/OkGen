# How OkGen Was Built with Claude Code — A Procedure

OkGen went from an **empty git repo** to a **distributable, offline Windows app**
in a single conversational session with Claude Code — no boilerplate hunting, no
framework wrestling. This document captures the *process* so others can repeat it.

The full session is in [docs/SESSION_TRANSCRIPT.md](docs/SESSION_TRANSCRIPT.md)
(raw: `docs/session/session.jsonl`). The technical design is in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) and
[ARCHITECTURE.md](ARCHITECTURE.md).

> **Who this is for:** anyone — including non-developers and domain experts — who
> has a real problem and a folder of example files, and wants working software
> without writing it line by line.

---

## The result, first

A fixed-width "OK file" editor: open a folder, see files iconized by retail
chain, edit fields in friendly forms (dropdowns + width limits), and save —
byte-for-byte safe. Built as a pure-Python Flask app, bundled to install offline
on Windows, with 40 passing tests and a clean rollback point at every step.

---

## The procedure (what actually worked)

### 1. Start by establishing the ground rules
The very first messages weren't about code — they were about *control*:
- "Can you pull/push?" → understood the git + auth setup.
- Set the commit identity.
- "Use main, I don't need branches." → set a working convention.
- Asked for a **`/checkpoint` skill** to commit + push + tag on demand, *and to
  roll back to a tag* when a direction wasn't liked.

**Why it matters:** deciding *how you'll work and how you'll undo* before building
anything makes the rest fearless. Every experiment had an escape hatch.

### 2. Let the assistant read your real data before planning
Instead of describing the file format, the example `.OK` files and `.xlsx`
definitions were handed over: *"parse through these and tell me what you see."*
Claude inspected them at the byte level and discovered the structure (record
markers, fixed-width fields, the position model) directly from the data.

**Tip:** real example files beat a written spec. The tool can see patterns you've
stopped noticing.

### 3. Ask for a plan — and answer the clarifying questions
Before writing code, Claude proposed a phased plan and asked targeted questions
(UI approach, how sections are identified, padding rules). The hard domain
knowledge — the detection rule, which fields repeat — came from the human at the
*right* moment, not all up front.

**Tip:** a good plan surfaces exactly the few decisions only you can make.

### 4. Build in small, verifiable phases — checkpoint every one
Work proceeded in deliberate phases, each **tested and tagged**:

| Phase | Outcome | Checkpoint tag |
|---|---|---|
| 1 | Compile xlsx → validated layouts; detect file type | `phase1-*` |
| 2 | Byte-exact parser/serializer | `phase2-parser-serializer` |
| 2.1 | Show all repeating rows; ignore junk fields | `phase2.1-repeating-records` |
| 3a/3b | Config system + JSON backend | `phase3b-backend` |
| 3c | The Flask editor UI | `phase3c-flask-ui` |
| 3d | Add-rows, limits, list config | `phase3d-*` |
| 3e | Native folder dialog, copy/delete rows | `phase3e-*` |
| — | Offline Windows bundle + docs | `v0.2-offline-windows` |

Each phase ended with: *run the tests, commit, tag, push.* The tags are real
rollback points (and download points on GitHub).

**Why it matters:** small phases keep every step reviewable, and a tag per phase
means "I don't like this" costs one sentence, not a day.

### 5. Verify behavior, not vibes
The correctness backbone — **byte-exact round-trip** — was proven with tests on
real sample files before any UI existed. The test suite grew with each feature
(40 tests by the end) and was run at every checkpoint. When a test accidentally
opened a real dialog and hung, that was caught and fixed.

**Tip:** ask for tests that assert the property you actually care about
(here: "an unedited file re-saves byte-for-byte").

### 6. Change direction in plain language
Mid-project the stack pivoted from **FastAPI + React → pure-Python Flask**, just
by saying so. Because the real logic lived in a framework-agnostic service layer,
the pivot cost almost nothing. Features were added the same way: *"add a delete
row," "Lane maxes at 10," "open the native file explorer,"* — described in
business terms, implemented and tested in one turn.

**Tip:** describe *what you want*, not *how to code it*. Keep the valuable logic
separate from the framework so you can change your mind cheaply.

### 7. Keep a human in the loop for real decisions
Choices that genuinely mattered (desktop vs web, dropdown vs free-text, what the
file actions operate on) were posed as explicit either/or questions with
trade-offs and a recommendation — then the human decided.

### 8. Finish the last mile: packaging & docs
The job wasn't "it runs on my machine." It ended with a **README for the team**,
**one-click `run.bat`**, **bundled dependency wheels for offline Windows install**
(Python 3.9–3.13), an implementation plan, and this process write-up — then a
tagged release the team downloads as a ZIP.

---

## The reusable playbook

1. **Set conventions first** — identity, branch policy, and a checkpoint/rollback
   habit (commit + tag every milestone).
2. **Hand over real examples**, not just descriptions.
3. **Get a plan and answer the few questions** only you can answer.
4. **Work in small phases**; end each with tests + a tagged checkpoint.
5. **Verify the property that matters** with automated tests.
6. **Keep business logic framework-agnostic** so you can pivot cheaply.
7. **Speak in outcomes** ("add a delete button"), not code.
8. **Decide the forks yourself**; let the tool do the building.
9. **Ship the last mile** — docs, one-click run, offline packaging, a release tag.

---

## What this enables

You don't need to know Flask, fixed-width parsing, or YAML config design to *own*
a tool like this. You need to know your **problem**, your **data**, and the few
**decisions** that are genuinely yours. Claude Code handles the engineering; you
stay in control via small checkpoints you can always roll back.

If a domain expert with example files and clear requirements can produce a tested,
documented, offline-installable app in one session — so can your next idea.
