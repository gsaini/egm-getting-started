# Stage 7 · Structural Goldfish (`agents/structural.md`) — FeatureRange 1–260, feature-spec-standard

Raw report from a fresh-context subagent, condensed; severities as given. It verified: no skipped heading levels, every relative link resolves, fences closed, list markers consistent.

**Major**
1. Whole range — **no "Risks & Mitigations" section**, which the standard requires. Risk content is scattered (L169–170 at-least-once, L203–204 head-of-line blocking inside Alt A, L230–244 NFRs); several limits are never framed as risks (unbounded growth, single worker thread, 30-minute worst case, plaintext secrets, downgrade gap). Add an H2 with risk / impact / mitigation entries.
2. L246–257 — "Additional Resources" and "Future Improvements" are H3s that nest under NFRs and sit before the Implementation Plan; promote to H2 and move to the end, or put them under an Appendix.
3. L110–193 — the longest section has no sub-headings; nine bold run-in paragraphs, yet later text refers to "the Envelope section" (L303) and "steps 1–3 of the Delivery section" (L343). Convert to H3s.

**Nit**
4. L24–34 — "The Problem" vs the standard's "Problem Statement"; the solution summary sits inside the problem section.
5. L206–210 — Alt B uses "Reason for Rejection" but was kept as a second path.
6. L62, L165 — backoff schedule defined twice in different units.
7. L139–143 — block tagged `json` isn't valid JSON (placeholders).
8. Horizontal rules group sections in ways the headings don't reflect.
9. L22, L250, L251 — inconsistent link styles; source files not linked.
10. L65–108 — CUJ 5 (registration) is the precondition for CUJs 1–3 but comes last.
