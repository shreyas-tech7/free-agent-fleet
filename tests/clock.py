"""One fixed clock for the tests that depend on quiet hours.

This lives in its own module, not in conftest.py. The gate runs the policy, unit, and injection tests in one pytest session.
That session loads more than one conftest.py, and `from conftest import ...` then finds the wrong one. See D-056.
"""

NOON_CHICAGO = 1_794_074_400          # 2026-11-06 12:00 America/Chicago. Quiet hours never apply to a test by accident.
