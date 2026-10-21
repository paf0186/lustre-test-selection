# Experiments

Each experiment is written down before it runs: what is tested, and what result
counts as a win.  Results are added after, against that, without moving the bar.
Decisions use cross-validation on the training months (`eval/wowy.py`); the
held-out test months are a lockbox, scored only to confirm.

## E1. WOWY round 2 (2026-10-21)

Setup: LambdaMART reference without base-rate features; two folds of the
training months; three seeds; grading counts any failure of a bug's subtest in
a session as reproduction; custom sessions are not evidence.

Hypotheses and bars (gain differences, paired, 95% interval):

1. Noise control: "with noise" is unclear (interval spans zero).  If it is
   not, the study is broken and nothing else from this run is used.
2. History stays essential: "without history" below zero, interval clear.
3. Size and risk: spending by risk (alpha 1), or the size gate, beats the
   reference by at least 1 point with the interval clear of zero.  Otherwise
   size is not worth a stage of its own.
4. Expensive signals (LLM, embeddings): a family is kept only if "without" it
   is below zero with the interval clear.  Otherwise it is dropped from the
   default stack (it can be retested on the four-year data).
5. LambdaMART stays at least as good as boosted trees and logistic.

Result (2026-10-21; cross-validation, 1,272 cases in two folds; lockbox, the 605
test-month cases; 1,162 bugs under the new grading):

1. Noise control: passed.  "With noise" against the reference: +0.2 (-1.5 to
   +2.0) in cross-validation, +1.6 (-1.1 to +4.5) in the lockbox.  Caution:
   "base + noise" came to +0.9 (-0.0 to +1.9), so the intervals run slightly
   optimistic; read a lower bound near zero as unclear.
2. History stays essential: FAILED.  "Without history" -0.5 (-3.4 to +2.4).
   Under the new grading the family that carries the model is the change's
   own earlier CI results: "without change_history" -2.6 (-4.8 to -0.5) in
   cross-validation, confirmed in the lockbox at -2.9 (-5.7 to -0.5).
   Directory history now overlaps with it.
3. Size and risk: FAILED the bar.  Spending by risk at alpha 0.5 +0.5 (-0.9
   to +1.9), at alpha 1 -0.6; the size gate +0.5 (-0.9 to +2.0).  The lockbox
   has alpha 1 hurting, -2.6 (-5.2 to -0.2).  Size predicts which patches are
   faulty (1% of the smallest fifth against 14% of the largest), but spending
   by it does not catch more bugs per hour than the model already does.
4. Expensive signals: embeddings dropped ("without embed" +1.0, -0.9 to
   +2.8).  The LLM passed in cross-validation ("without llm" -1.5, -3.0 to
   -0.1) but not in the lockbox (+0.3, -1.4 to +2.1): kept as a lead, to be
   retested on the four-year data before it is paid for.
5. LambdaMART holds: boosted trees -0.5 (-4.0 to +3.1), logistic +2.4 (-1.4 to
   +6.4) in cross-validation; both slightly worse in the lockbox.

Not pre-registered, so a lead only: the hybrid (principles first, then the
model) is the one variant clearly above the blind line in cross-validation,
+5.0 (+0.8 to +9.7), +3.0 (-0.4 to +6.6) over the reference.  Its lockbox run
follows.  The reference itself is +2.1 (-1.9 to +6.1) over blind in
cross-validation and +0.5 (-4.5 to +5.4) in the lockbox: not yet clearly better
than the same tests for every patch.

Lockbox for the hybrid: not confirmed.  -2.9 (-6.3 to +0.8) against the
reference on the test months; on the recent months -0.7 (-7.5 to +7.0).  The
cross-validation lead did not hold across periods.  The overlap discount is
also worse in the lockbox (-2.2, -5.4 to +0.6).  Neither is adopted.

## E2. WOWY on 2024-10-20 to 2026-10-19 (registered 2026-10-21, before the data is loaded)

Setup: as E1, unchanged, on cases from 2024-10-20 on (two years: about three
times E1's training data).  Same reference (LambdaMART without base-rate
features), folds, seeds, grading, noise control and bars.  Same lockbox role
for the held-out test months.

Hypotheses and bars, as E1's, plus:

6. More data helps the model: the reference's gain over the patch-blind line,
   in the lockbox, rises above E1's +0.5 with its lower bound above zero.
7. The leads E1 could not settle are settled one way: directory history, the
   LLM and the principles each come out either "keep" (interval clear below
   zero when removed) in both cross-validation and lockbox, or unclear in
   both, in which case they leave the default stack.

Result (2026-10-21; 4,142 cases from 2024-10-20, 3,134 bugs; cross-validation on
2,941 cases in two folds, lockbox on the 1,201 test-month cases):

1. Noise control: passed in cross-validation (-0.1, -1.1 to +0.9) but FAILED in
   the lockbox (+1.0, +0.1 to +2.0): pure noise "helped".  The paired intervals
   understate the real variance by about a point, so no lockbox difference
   under about 2 points is evidence of anything.
2. The change's own earlier CI results: keep, and the strongest part.
   Removing them costs 5.6 (-7.1 to -4.0) in cross-validation, 3.5 (-5.2 to
   -1.9) in the lockbox.  Directory history: -1.3 (-2.6 to -0.1) in
   cross-validation, +0.3 in the lockbox: not confirmed.
3. Size and risk: rejected.  Spending by risk at alpha 1 hurts in both (-2.9
   and -2.9); alpha 0.5 and the size gate do not help.
4. The LLM: unclear in both (-0.9, then -0.5): leaves the default stack.
   Embeddings, BM25 links and the principles as features: unclear in
   cross-validation and, in the lockbox, removing them "helps" by 1.1 to 2.1
   points, inside the noise control's error: they leave the default stack.
5. LambdaMART holds: boosted trees +1.1 (-1.1 to +3.2) and -1.7 in the lockbox;
   logistic hurts (-3.2) in cross-validation.
6. More data helps the model beat blind: FAILED.  The reference's gain over the
   patch-blind line is +1.2 (-1.6 to +4.3) in cross-validation and -0.2 (-4.1
   to +3.8) in the lockbox.
7. Settled: directory history, the LLM and the principles all leave the
   default stack (unclear in both, or confirmed only in one).

Also: the hybrid is +0.7 (-1.5 to +3.1) and -1.7 in the lockbox, rejected;
the overlap discount hurts in both (-2.1, -4.9).

## E3. Learning curve (registered 2026-10-21)

Train the reference on the training months of the last 6, 12, 18 and 24
months only; score each on the same evaluation set (the test months from
2026-03-20 on).  If the gain over blind still rises from 18 to 24 months by
more than its interval's half width, extend the data back to 2023 (and then
consider 2019); if it is flat, do not, and weight recent months instead.

Result (2026-10-21; evaluation: 2026-03-20 on, test months): gain over blind
-3.5 (-8.9 to +1.6) on 6 months, -0.1 (-5.2 to +5.0) on 12, +3.1 (-1.8 to
+8.1) on 18, +2.0 (-3.3 to +7.8) on 24.  Rising to 18 months, flat from 18 to
24 (a change of -1.1 against a half width near 5): do not extend the data
back for this model; weight recent months instead.

## E4. The simple candidate (registered 2026-10-21)

Selection: `trivial`, the scripts the patch edits, the first n test sets of the
patch-blind order (ranked on the training months), and every test set that
caught a bug on an earlier patchset of the same change.  Only parts that held
up in E1 and E2.  Scored as a curve over n, on the two-year cases.

Bars: in cross-validation and in the lockbox, its mean gain over the blind line
across 15 to 40% cost is at least +2 points (the noise control's error) with
the lower bound above zero; and it is not worse than the LambdaMART reference
by more than 2 points.  If it passes, it is the version to shadow-run.

## E5. Patch risk (registered 2026-10-21)

A patch-level estimate of the chance a patch has a bug any test would catch,
for uses beyond test selection (routing extra AI review).  Features: size,
Gerrit's kind, span, functions changed, test-only and docs-only, the change's
earlier CI results, and each component's past fault rate in the training months.
Fitted on the training months to the population (the random sample, faulty
patchsets outside it weighted down).

Bars, on the population (sampled cases only), in cross-validation and the
lockbox: ROC AUC at least 0.65 with the lower bound above 0.6; the riskiest
fifth of patches at least twice the base rate of faults; and the safest fifth
at most half of it.
