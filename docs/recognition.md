# Recognition implementation

This describes the implementation shipped in 0.7.13. The
[README diagrams](../README.md#how-recognition-works) show the model interactions.
Model files and hashes are listed in the [model manifest](../src/tablescan_local/models/README.md).

## 1. Page preparation and routing

`process_document` builds one `LocalOcrEngine` and processes pages sequentially.
Slow mode forces `high_accuracy=True` before the engine is created. The current
template supplies geometry, ignored columns, headers, identifier columns and
cell-specific value rules.

OpenCV crops each cell with alternative grid-aware boundaries. Blank detection
checks all available source crops so a clipped primary crop does not alone erase
a digit. A numeric cell containing only empty/strike evidence returns an empty
proposal before numeric decoding. Geometric cancellation detection precedes OCR;
non-numeric mark consensus across a row is evaluated after OCR. Excluded
measurements retain their OCR proposals for reversible review.

Text cells use PP-OCRv5 Server on normal and thresholded crops. Free-text fields
use RapidOCR's bundled `ch_PP-OCRv4_det_infer.onnx` detector followed by the
configured PP-OCRv5 Server recognizer, then spatial reading order. The field
orientation classifier is disabled. Fixed fields use the saved literal value;
numeric fields follow numeric recognition. The table grid comes from geometry,
not from asking a language model to discover the cells.

Source: [pipeline.py](../src/tablescan_local/pipeline.py),
[imaging.py](../src/tablescan_local/imaging.py),
[ocr.py](../src/tablescan_local/ocr.py).

## 2. Standard numeric recognition

Two recognizers run on prepared cell images:

- PP-OCRv5 Server: cleaned, thresholded and tight ink views.
- PP-OCRv5 English Mobile: cleaned and tight ink views.

Where a visible decimal mark establishes a split, both models also read the
integer and fractional parts separately. Candidate scores accumulate weighted
confidence; decimal evidence and numeric format affect selection. The chosen
value and alternatives then pass through `constrain_reading`, which applies
current rules and retains evidence of rejected or inferred readings. Numeric
results carry a review requirement.

## 3. Maximum accuracy

This path uses all three OCR recognizers, including PP-OCRv6 Medium. It has two
distinct decisions: **candidate discovery** and **final candidate ranking**.

### Candidate discovery

1. Build cleaned, blue-ink, tight-ink and contrast-enhanced views; add rotations
   of −3°, −1.5°, +1.5° and +3° and four threshold variants. Alternative grid
   crops receive only cleaned and tight-ink views.
2. Cache identical prepared images per model to avoid redundant inference.
3. Keep the raw recognition and decode the CTC character probabilities with
   rule-constrained prefix beam search: beam width **64**, up to **6** hypotheses.
   CTC represents possible text sequences, including repeated characters and blanks.
4. Accumulate provisional scores with model weights **1.0 / 0.7 / 1.0** for
   PP-OCRv5 Server / English Mobile / PP-OCRv6 Medium. Hard-rule violations are
   excluded from the numeric candidate pool but literal readings remain in audit data.
5. Use visible decimal evidence, separately read decimal parts and the minimum
   visible digit count. Penalize candidates that omit distinct digit-sized groups.
6. Where segmentation is suitable, use `emnist_digit_cnn.onnx` to support existing
   candidates. This is auxiliary evidence, not an unrestricted replacement engine.

If no admissible candidates survive, fall back to standard numeric recognition
and flag the lack of consensus.

### Final ranking

`CandidateEvidence.rank` evaluates the candidate pool against CTC sequences from
**layout-preserving** views: cleaned, ink-isolated, contrast-enhanced and cleaned
alternative crops. Tight crops, rotations and thresholds help discover candidates
but do not each become independent final votes.

For each candidate, sequence log-likelihood is normalized by text length. Support
is averaged across distinct views **within each model**; the model means are
combined with a geometric mean, with a 0.01 floor. This score is a ranking statistic,
not a measured probability of correct recognition.

Selection also applies the visible-digit penalty, a **1.10** winning margin and
corroboration safeguards. Strong split readings from at least two models can
participate under additional evidence checks. If ranking is too weak or close,
the provisional candidate is retained with disagreement flags.

For an unresolved pair differing in exactly one digit, the code can segment and
reread that glyph using **all three OCR models**. It accepts a candidate only if
all agree on one of the two digits and pass the confidence checks. The digit CNN
alone cannot settle this comparison.

Source: [ocr.py](../src/tablescan_local/ocr.py),
[numeric_decoder.py](../src/tablescan_local/numeric_decoder.py),
[candidate_ranking.py](../src/tablescan_local/candidate_ranking.py),
[digit_verifier.py](../src/tablescan_local/digit_verifier.py).

## 4. Page-level evidence

The writer profile is a temporary comparison of stable digit examples from the
current page. It excludes a cell from its own reference evidence and ranks
existing alternatives. It writes a **suggestion requiring human confirmation**, not a new
final transcription. No weights are trained or saved by this step.

The pipeline then flags table outliers. These checks and current value constraints
can identify suspicious results; a plausible range is not evidence of the written
digit. Human corrections do not become hidden training data for recognition.

Source: [writer_adapter.py](../src/tablescan_local/writer_adapter.py),
[pipeline.py](../src/tablescan_local/pipeline.py).

## 5. Slow mode agreement policy

Eligible cells must still need review, have numeric or integer rules, and lie
outside headers, identifier columns, ignored columns and excluded rows. Complex
numeric expressions and free-text fields are not part of this stage.

1. Qwen3.5-4B reads a table image without identifier columns, with synthetic
   row positions. Its prompt requests row-indexed JSON and preserves empty rows.
2. Normalize each eligible Qwen proposal with the **current cell rules**. Select
   rows only where an admissible proposal differs numerically from baseline OCR.
3. GLM-OCR receives the selected **original row images** with a text-recognition
   prompt. It does not receive Qwen's proposed numbers for confirmation.
4. Normalize both readings; change a cell only when both are valid, agree
   numerically and differ from baseline. Retain the baseline, alternatives and
   model evidence, and add a review flag.
5. Recheck outliers. Invalid/missing responses preserve baseline values; partial
   or failed verification is reported. Review and export checks remain required.

## 6. Execution and storage boundaries

The Qt application uses CPU ONNX inference through RapidOCR. Slow mode workers
run in a separate Python environment and load one additional model at a time;
Qwen and GLM execute sequentially. Requests contain images and prompts, not the
user's validation answers.

| Platform/backend | Qwen | GLM | Device |
| --- | --- | --- | --- |
| Apple Silicon / MLX | `mlx-community/Qwen3.5-4B-MLX-4bit` | `mlx-community/GLM-OCR-bf16` | Metal |
| Transformers | `Qwen/Qwen3.5-4B` | `zai-org/GLM-OCR` | CPU or NVIDIA CUDA |

Transformers uses BF16 on CPU by default, with explicit FP32 compatibility mode.
CUDA uses BF16 when supported, otherwise FP16. Auto requires sufficient free GPU
memory (12 GiB for Qwen, 4 GiB for GLM) and retries on CPU after CUDA out-of-memory.
Explicit CUDA reports an error rather than silently switching devices. Windows
caps oneDNN instructions below AMX to avoid the observed VM compatibility crash.

Revisions are pinned in [slow_runtime.py](../src/tablescan_local/slow_runtime.py).
Inference loads local files only. Worker requests, crops, responses and execution
metadata stay with the local job; the application can stop the active worker.

Source: [slow_mode.py](../src/tablescan_local/slow_mode.py),
[slow_runner.py](../src/tablescan_local/slow_runner.py).
