# UoS missing-value dash regression crops

These 30 crops come from the UoS Data Rescue dataset, Stuart E. Middleton and Loitongbam Gyanendro Singh, University of Southampton, https://zenodo.org/records/15730546, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

They were selected before OCR for the 2026-11-02 local holdout evaluation. Original source labels were dashes. The provenance file records source coordinates and crop hashes. Images are cropped derivatives of the original scans, without pixel retouching.

Visual review for this regression identified 29 lone dashes and one contaminated crop (QC9945R381967_90, cell 305) with neighboring digits at the top. The latter must remain content: a dash label does not justify deleting visible digits. These are development regressions, not an independent validation set. The tests exercise the shared pixel gate and both recognition dispatch paths without downloading OCR models.
