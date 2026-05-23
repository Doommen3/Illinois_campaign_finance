---
description: Run the fast pre-deploy pytest subset documented in CLAUDE.md and report pass/fail with timing.
---

Run the fast pre-deploy test subset:

```bash
pytest -q tests/test_federal_fec.py tests/test_lobbying_routes.py tests/test_uiux_improvements.py tests/test_webapp.py::TestWebApp::test_federal_finance_page_loads_with_synced_rows
```

Report:
- Wall-clock elapsed time
- Pass/fail counts
- If anything fails, show the failure output verbatim (do not re-run, do not "try to fix")
- If all pass, confirm "ready for deploy" in one line

Do not run the full suite. Do not modify any files. This command is read-only.
