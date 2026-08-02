---
name: Clean code, zero tech debt
description: Solomon prioritizes clean code in all projects; avoid speculative tests, unused helpers, dead branches, scope creep, abstractions for hypothetical future needs.
type: feedback
originSessionId: d5ec0d5e-c9a1-434f-9c30-8a4c2a6611be
---
Across every project, write clean, lean code with zero tech debt. Match scope to what's asked — do not bolt on extras "because they might be useful."

**Why:** Solomon explicitly flagged this on 2026-05-01 during the ARDA Phase 2 migration after I asked whether to add small bonus cases to ported tests. He approved adding *obviously useful* cases (e.g. covering a module-level convenience export that has zero coverage) but warned that this is the path to tech debt and asked me to be cautious across all projects.

**How to apply:**
- Don't add extra test cases just because you can imagine an edge case. Add them only when (a) they cover real public surface that has zero coverage, (b) they catch a regression you've actually seen, or (c) the test bar requires them. If you're tempted to add a case "for completeness," ask first.
- Don't add helper functions, base classes, config flags, or abstractions for hypothetical future needs. Three similar lines beats a premature abstraction.
- Don't leave dead code: removed import? delete the line. Removed feature? delete the file, don't leave a `// removed` comment or a backwards-compat shim.
- Don't add error handling, fallbacks, or validation for scenarios that can't happen. Trust framework + internal-code guarantees. Validate at system boundaries only.
- Don't write comments explaining WHAT the code does — names should do that. Reserve comments for WHY (non-obvious constraints, subtle invariants, workarounds for specific bugs).
- Don't smuggle in refactors, renames, or "while I'm here" cleanups during a focused task. Mention it; don't do it.
- When migrating code, prefer minimal mechanical edits. Behavior-changing improvements (even good ones) get their own commit and ideally an ADR.

**Quick test before adding anything to a diff:** "If I delete this line, does anything currently working break or any current requirement go unmet?" If no, don't add it.
