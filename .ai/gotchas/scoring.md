# Scoring / float math gotchas

## Noisy-OR drift on small inputs

`noisyOR(p_i) = 1 - prod(1 - p_i)` evaluates correctly for a single input, but multi-step accumulation on small values (≤0.15) produces IEEE 754 drift of ~1e-7 to 1e-9. **Never** use exact equality on noisy-OR outputs in tests. Use `pytest.approx(expected, abs=1e-6)` or `math.isclose(actual, expected, abs_tol=1e-6)`.

## Maturity downweight applies BEFORE noisy-OR, not after

`maturity_sensitive` rules are downweighted via `effective_weight = weight * (1 - MaturityK * (1 - maturity))` before they enter the Layer 3 noisy-OR. Test assertions must account for the downweighted weight, not the raw rule weight. Example: rule weight 0.30, customer maturity 0.5, MaturityK 0.30 → effective_weight = 0.30 * (1 - 0.30 * 0.5) = 0.30 * 0.85 = 0.255. The noisy-OR input is 0.255, not 0.30.

## Threshold boundary tests

`allow_max = 0.60`, `block_min = 0.80`. Tests at the exact boundary:
- `score == 0.60` → ALLOW (per `<=` semantics)
- `score == 0.5999...` → ALLOW
- `score == 0.6001...` → REVIEW
- `score == 0.80` → BLOCK (per `>=` semantics)
- `score == 0.7999...` → REVIEW

Construct inputs that produce exact boundary scores via mocked rule weights. Use `pytest.approx` for the score itself when the inputs round.

## Account prior is computed once, not per-rule (Phase 2 onwards)

Phase 2's Layer 2 `account_prior` is a continuous value derived from customer state — it is NOT a rule in `rules.yaml`. Do not add it to the rules slice or call the noisy-OR machinery on it; it composes with `signal_score` via `final = noisyOR(account_prior, signal_score)` in the scorer. Tests must mock `account_prior` separately from the rule set.

## `value_n == 0` zeroes the value-novelty signal

`value_zscore = (value - value_mean) / sqrt(value_m2 / value_n)` is undefined when `value_n` is 0. Return 0 (no signal) rather than `nan` or raising — rules conditioned on `value_zscore > X` then stay inert until the customer has observations. Same for `cadence_zscore_hours` when `cadence_n == 0`.

## Hard-block short-circuit means Layer 3 doesn't run

A Phase 1 BLOCK rule (`blacklisted_ip`, `ip2p_threat_botnet_block`) firing returns immediately with `score=1.0`, `decision=BLOCK`. **No** Layer 3 rules evaluate, **no** triggered_rules list beyond the BLOCK rule. Tests for a BLOCK-firing scenario must not assert on Layer 3 rules also firing — they don't.
