"""Package marker — ``helpers`` must stay a REGULAR package.

As an implicit namespace package it lost to the same-named fixture module
``tests/helpers.py`` on script runs from tests/ (``python3
tests/bench_*.py`` puts ``tests/`` on sys.path): ``No module named
'helpers.graph'; 'helpers' is not a package`` — both ``make perf`` bench
legs FAILED(rc) 2026-09-04. A regular package at the sys.path root wins
over a later same-named module; pytest imports are unaffected (tests
address the fixture module as ``tests.helpers``).
"""
