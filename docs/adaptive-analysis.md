# Experimental adaptive analysis

On the alignment screen, enable **Adaptive analysis (experimental)** to try a
short numeric pass before the complete High cascade. It is off by default and
applies to the current document. The batch preparation dialog retains its
existing Fast/High/Slow controls.

The short pass compares literal and grammar-constrained readings from PP-OCRv5
and PP-OCRv6 on cleaned and ink-isolated views. Every reading must agree, have
confidence of at least 0.95, and be separated from other beam hypotheses by a
likelihood ratio of at least ten. The value must satisfy the template's format
and ranges. Decimal readings require a visible separator; contradictory digit
counts in the original or grid-safe retry crops force complete analysis.
These thresholds are experimental heuristics, not calibrated error probabilities.

If any check fails, the existing complete High cascade runs, reusing recognizer
outputs already computed for identical views. Empty cells, cancellation strokes,
fixed template values and explicit exclusions retain their original behavior.

**Every short-pass value requires human review.** The experimental flag cannot
be cleared by confidence alone. Review explains whether a value used the short
or complete analysis. Optional Slow verification handles unresolved recognition
or table checks; the experimental review marker alone does not invoke the larger
models. A successful Slow response does not remove the experimental review duty.

The queue persists the chosen mode through restart and retry. Checkpoints include
the adaptive policy identity. Diagnostics show short/complete cell counts and
the saved model version identifies the policy. Counts include all recognized
numeric cells, so they are not an accuracy denominator.

Acceptance as a default requires independent documents and a comparison of total
time through verified export. Tests on previously used development scans can
detect regressions but cannot establish accuracy on unseen handwriting.
