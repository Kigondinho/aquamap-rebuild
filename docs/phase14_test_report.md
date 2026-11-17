# AquaMap test report

Run: 2026-11-17 11:43 UTC · Python 3.12.3 · `pytest -q -p no:cacheprovider --test-db --require-all --report=docs/phase14_test_report.md`

**994 passed · 0 failed · 0 unverified** (of 994 tests).

| Area | Passed | Failed | Unverified |
|---|---:|---:|---:|
| Unit: IVI, normalisation, weights, classification, missing data | 161 | 0 | 0 |
| Unit: geospatial calculations | 49 | 0 | 0 |
| Unit: data acquisition | 37 | 0 | 0 |
| Authentication | 103 | 0 | 0 |
| Unit: settings, database tooling, test-database guard | 39 | 0 | 0 |
| Integration: database, schema, constraints | 72 | 0 | 0 |
| Integration: ingestion and full pipeline | 23 | 0 | 0 |
| Integration: spatial queries, filtering, road detail | 125 | 0 | 0 |
| Integration: API responses | 148 | 0 | 0 |
| Frontend: in a real browser | 33 | 0 | 0 |
| Frontend: files and API wiring | 56 | 0 | 0 |
| Project: structure and secrets | 148 | 0 | 0 |
| **Total** | **994** | **0** | **0** |

*Passed*: ran and every check held. *Failed*: ran and a check did not hold, or could not be set up. *Unverified*: did not run (skipped), so it proves nothing either way.

## Unverified

None: every collected test ran.
