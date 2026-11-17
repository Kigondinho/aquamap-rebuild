# AquaMap end-to-end verification

Run: 2026-11-17 11:41 UTC against `http://127.0.0.1:8051`

**34 passed · 0 failed** (of 34 checks)

| Area | Check | Result | Detail |
|---|---|---|---|
| Database | roads, rivers and results stored | pass | 52,469 roads, 720 rivers, 47,619 results (47,619 with an IVI) |
| Database | every geometry valid, EPSG:32737 | pass | 0 invalid |
| Database | latest pipeline run succeeded and matches the stored results | pass | {"run_id": 1, "status": "succeeded", "model_version": "ivi-wlc-1.0", "config_version": 1, "segments_scored": 47619} |
| Database | every stored IVI = weighted sum of its factor scores; every category matches its IVI | pass | {"formula": 0, "tier": 0, "other_config": 0} |
| Database | scoring re-done in Python from the stored measurements gives the stored results | pass | 2,000 random roads, 0 differ |
| Database | stored river distance = PostGIS nearest-river distance | pass | 500 random roads, worst difference 0.000113 m (stored as REAL) |
| Access | every protected endpoint refuses anonymous requests (401) | pass | /auth/me=401, /roads=401, /roads/search=401, /roads/1=401, /roads/summary=401, /roads/nearby=401, /meta/config=401, /analysis/metadata=401, /admin/layers=401 |
| Access | a wrong password is refused | pass | HTTP 401 |
| Access | sign-in with the account | pass | HTTP 200 |
| Access | administrator routes follow the role | pass | role user, HTTP 403 |
| API | statistics equal the database | pass | Low 4,345, Medium 27,641, High 14,940, Severe 693 |
| API | road details equal the database | pass | 40 random roads, differing: none |
| Dashboard | sign-in through the form opens the dashboard | pass |  |
| Dashboard | statistics shown equal the database | pass | Scored Road Length 7,268 km; Low 669 km; Medium 3,715 km; High 2,708 km; Severe 176 km |
| Dashboard | legend shows the stored category thresholds | pass | Severe 4.0 – 5.0 \| High 3.0 – below 4.0 \| Medium 2.0 – below 3.0 \| Low 1.0 – below 2.0 |
| Map | County overview (zoom 12): drawn roads and their categories equal the database | pass | 45,272 drawn, 45,272 in the database; Low 4345, Medium 27419, High 12891, Severe 617 |
| Map | County overview: roads are painted on the map canvas | pass | 242,516 painted pixels |
| Map | CBD (zoom 16): drawn roads and their categories equal the database | pass | 1,260 drawn, 1,260 in the database; Low 54, Medium 848, High 356, Severe 2 |
| Map | CBD: roads are painted on the map canvas | pass | 223,723 painted pixels |
| Map | Eastlands (zoom 15): drawn roads and their categories equal the database | pass | 2,689 drawn, 2,689 in the database; Low 0, Medium 2143, High 534, Severe 12 |
| Map | Eastlands: roads are painted on the map canvas | pass | 305,413 painted pixels |
| Map | Embakasi (zoom 15): drawn roads and their categories equal the database | pass | 1,921 drawn, 1,921 in the database; Low 0, Medium 1666, High 254, Severe 1 |
| Map | Embakasi: roads are painted on the map canvas | pass | 267,510 painted pixels |
| Map | Westlands (zoom 15): drawn roads and their categories equal the database | pass | 3,935 drawn, 3,935 in the database; Low 73, Medium 2692, High 1164, Severe 6 |
| Map | Westlands: roads are painted on the map canvas | pass | 311,588 painted pixels |
| Road panel | values shown after a real map click equal the database (name, class, surface, IVI, category, elevation, slope, river distance) | pass | 40/40 roads across all four categories match (1 clicks hit an overlapping neighbour, also checked) |
| Road panel | road 51974 without a result says so | pass | IVI and category shown as None |
| Road panel | road 52287 without a result says so | pass | IVI and category shown as None |
| Search | 'ngong': names listed equal the database | pass | 10 shown: ['Ngong Lane', 'Ngong Road', 'Ngong View Drive', 'Ngong View Rise']… |
| Search | 'mombasa': names listed equal the database | pass | 4 shown: ['Mombasa - Nairobi Road', 'Mombasa Road', 'Nairobi - Mombasa Road', 'Old Mombasa Road']… |
| Search | 'bypass': names listed equal the database | pass | 7 shown: ['Eastern Bypass', 'Eastern Bypass Foot Path', 'Fiesta-Bypass Link', 'Northern Bypass']… |
| Search | 'kenyatta': names listed equal the database | pass | 3 shown: ['Kenyatta Avenue', 'Kenyatta Drive', 'Margaret Wambui Kenyatta Road']… |
| Filters | High+Severe, residential, IVI >= 3.2: drawn roads equal the database | pass | 231 drawn, 231 expected |
| Access | after signing out the dashboard sends the browser back to sign-in | pass |  |
