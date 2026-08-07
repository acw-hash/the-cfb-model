# Note artifacts — evidence, not a code path

Everything under this directory is the **output** of a diagnostic that has
already been written up in the corresponding `docs/notes/D*.md`. None of it is a
supported entry point.

Specifically:

- The `run_d*.py` / `build_canonical.py` scripts here are one-shot runners from
  the D1-D7 diagnostic phase. They contain inline analysis logic that is not
  covered by `make test`. Do not extend them and do not re-run them to produce
  new numbers.
- They read `data/backtests/task23_*`, which **ADR 0005** rules non-citable: the
  commit those runs pin does not exist in this repository, so their outputs
  cannot be regenerated and may not be cited as evidence about model
  performance.
- The reusable parts of that work already live in
  `src/ncaa_quant/evaluation/d*_eval.py`, under test.

New evaluation code goes in `src/ncaa_quant/evaluation` and is invoked through
the CLI, so that its results carry a verifiable manifest
(`ncaa-quant backtest verify`).
