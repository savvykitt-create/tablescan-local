# TableScan Local 0.7.5

Removed the missing-decimal recovery toggle from Rules and the advanced rule editor. Decimal separator recovery is automatic for numeric rules with a known positive number of decimal places, including saved templates with the old option disabled. Integer, text, and unspecified-precision formats do not guess separator placement. Existing candidate ranking, bounds, ambiguity handling, and review requirements remain in effect.

Validation: 146 tests passed, including automatic recovery with both legacy flag values, template round trips, and formats where separator placement cannot be inferred.
