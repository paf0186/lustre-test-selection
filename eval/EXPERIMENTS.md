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

Result: (pending)
