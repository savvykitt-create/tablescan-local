# Numeric candidate ranking

Version 0.7.8 uses the same shipped OCR and digit-verification models. No weights
are trained from corrections. Reference workbooks are used only by the external
validation scripts, never by recognition.

The cascade discovers rule-compatible candidates from whole-cell views and visible
separator splits. The final comparison considers **all existing numeric shapes**:
a previously selected short answer cannot exclude a longer reading. Connected ink
groups provide a lower bound on the number of digits, not an exact length. Missing
separate glyphs are penalized; matching touching groups gets no bonus.

Only layout-preserving whole-cell views contribute to the final sequence score:
cleaned original, ink, contrast enhancement and cleaned alternative grid crops.
Prepared images are deduplicated per model. For every candidate, an exact CTC forward
sum evaluates the original model probabilities, even when it was absent from that
view's top-six discovery beam. Decimal/sign spelling variants are summed as distinct
literal paths, preserving CTC repeated-character semantics. No new inference is
needed for this comparison. Numeric probabilities are never renormalized to erase
non-numeric evidence.

Sequence support is exp(log likelihood / output length), capped at 1. Distinct views
are averaged within each model, then model scores are combined by an equal-weight
geometric mean with a 0.01 floor. This is an engineering ranking statistic, **not a
calibrated probability of correctness**. Changed whole-cell decisions require a
winner/runner-up ratio of 1.10. If aggregate support is below 0.5, changing the answer
also requires either two models individually preferring it by a ratio >= 1.10 with support >= 0.05,
or digit-verifier support >= 0.90. Uncorroborated weak and close decisions are flagged even when
the selected value remains unchanged. A stable, independently corroborated incumbent
is not sent to review solely because its uncalibrated sequence statistic is below 0.5.

Two models independently reading the same integer and fraction with confidence
>= 0.90 can resolve a close whole-cell ambiguity. Their candidate must retain at
least half the best whole-cell support and must also be the whole-cell winner or
receive digit-verifier support >= 0.90. One model's repeated transformed views are
not additional independent votes. All existing candidate shapes are available to
the digit verifier; equivalent segmentations share a single inference.

When the two leading complete readings differ at exactly one digit and their score
ratio is below 1.10, the same three OCR models can inspect just that segmented glyph.
The current answer must already be one of those two readings. Selection changes only
if all three literal readings agree with one of the candidate digits; sorted model
confidences must be at least 0.25, 0.50 and 0.90. This bounded check introduces no new
numeric strings and skips stable cells. Unresolved disagreement remains reviewable.

`candidate_scores` records the cascade evidence; `ranking_scores` records the final
whole-cell comparison. Decision flags distinguish sequence reranking, split agreement
and separate-glyph agreement. A changed answer always requires review. Existing human
corrections and saved source documents are never overwritten. Thresholds are tested
engineering choices, not measured error guarantees for unseen documents.

Field OCR keeps the supplied page orientation and groups text into lines. Cancellation
detection excludes row-border connections and supports dark and blue cancellation ink.
Export gives explicit numeric regions the same precedence over generic header rows as
OCR, so reviewed first-row measurements are retained on the Data sheet.
