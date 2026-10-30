# Stage 7 · Style Goldfish (`agents/style.md`) — FeatureRange 1–260

Raw report from a fresh-context subagent, condensed to one line per finding; severities as given. No Blockers.

- **Major** L46, L83, L224 — "unaffected" and "fast" can't be verified; put a number on the latency goal (e.g. p99 increase ≤ X ms while a receiver times out) and reuse it in CUJ 2 and Alt D.
- **Major** L45, L47 — "healthy" is undefined; define it from exposed fields (2xx within 10 s; `state = active` and `consecutive_failures = 0`).
- **Major** L242 — "delivery behind `--no-webhooks`" reads as opt-in, but the flag opts out; say delivery is on by default and the flag disables the worker.
- **Major** L192–193 vs L243–244 — rollback is defined twice, differently; define once, reference elsewhere.
- **Nit** L21 "six independent reviewers stress-tested" — appeal to authority, jargon.
- **Nit** L32, L38 "real", "actually" — vague and circular; name the calls.
- **Nit** L34 — "retrying until delivered" and "never discarding" say the same thing.
- **Nit** L18, L27, L257 — vague quantifiers ("rare", "reliably", "a handful").
- **Nit** L62 vs L165 — backoff in mixed units vs seconds.
- **Nit** L81 — unclear whether delivery starts or finishes within 30 min.
- **Nit** L106–107 — "this one time only" is triply redundant.
- **Nit** L126 "Events are an outbox." — undefined pattern name.
- **Nit** L134, L208 — absolute claims (UUID4 is probabilistic; pull prevents loss only within delivery).
- **Nit** L165 "It never gives up." — informal.
- **Nit** L199, L204 — "bad event" subjective; "the same guarantees" overstates (Alt A allows out-of-order).
- **Nit** L146 — contraction breaks register.
- **Nit** L239 — 3,000 events/day has no source.
- **Nit** L248, L253 — `###` headings with no `##` parent.
- **Nit** L11 — "small" is subjective.
