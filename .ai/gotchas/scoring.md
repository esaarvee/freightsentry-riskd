# Scoring / Float Math Gotchas

## noisy-OR produces drift at small inputs
`noisyOR(p) = 1 - (1-p)` for a single input evaluates correctly, but multi-step noisy-OR
accumulation on small values (≤0.15) produces IEEE 754 drift of ~1e-7 to 1e-9.
Always use `assert.InDelta(t, expected, actual, 1e-6)` — never Equal.

## maturity_k downweighting
maturity_sensitive rules are multiplied by `maturity_k = 0.70` before entering noisy-OR.
When writing test assertions for these rules, account for the downweighted weight, not the raw rule weight.
Example: rule weight 0.20 → effective contribution 0.14 → noisyOR(0.14), not noisyOR(0.20).

## Threshold boundary tests
block_min = 0.60, review_min = 0.30.
Boundary test values: score=0.60 → BLOCK, score=0.5999... → REVIEW, score=0.30 → REVIEW, score=0.2999... → ALLOW.
Use InDelta for score assertions near thresholds; construct inputs that produce exact boundary scores via mocked rule weights.

## Account prior vs transaction signal
Account prior is computed once (continuous, not rule-based). Transaction signal is noisy-OR of active rules.
Combined: `final = noisyOR(prior, signal)`. Do not add prior into the rules slice — it is applied separately in scorer.go.
