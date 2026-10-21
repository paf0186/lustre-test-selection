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
