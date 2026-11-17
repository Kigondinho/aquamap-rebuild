# Phase 11 — AquaMap frontend

Plain HTML, CSS and JavaScript modules with Leaflet 1.9.4 (vendored). No build step and no framework: the files in
`static/` are served as they are. Start the application (`uvicorn app.main:app --reload`) and open
<http://localhost:8000>.

## Pages

| Page | Address | Purpose |
|---|---|---|
| Sign in | `/login` | Email and password; links to registration and password reset |
| Create account | `/register` | Name, email, password twice, checked before sending |
| Confirm email | `/verify-email` | Six-digit code, "send a new code" |
| Reset password | `/reset-password` | Step 1 email, step 2 code and new password |
| Dashboard | `/` | Everything below; needs a session, otherwise the browser is sent to `/login` |

## Dashboard

| Requirement | Where | Data from |
|---|---|---|
| Interactive road vulnerability map | Centre; roads coloured by category, reloaded when the map moves or a filter changes | `GET /roads` |
| Vulnerability legend | Bottom-left of the map: line sample, category, score range | `GET /meta/config` |
| Road search | Sidebar "Find a road": results as you type, one per road name | `GET /roads/search` |
| Road classification filters | Sidebar checkboxes, one per class that has scored roads, with its count | `GET /analysis/metadata` (choices), `GET /roads` |
| IVI filters | Sidebar: category checkboxes and a from–to score range | `GET /roads` |
| Road information panel | Opens on a click on a road, a search result or a nearby result | `GET /roads/{road_id}` |
| Nearby roads | Sidebar: "Near me" (browser location) or "Pick on map", within 250 m to 2 km | `GET /roads/nearby` |
| Summary statistics | Strip under the top bar: scored length, and length, share and segments per category | `GET /roads/summary` |

**Road panel.** Name, or "Unnamed road" with its OpenStreetMap way number; road classification; surface type; IVI score;
vulnerability category; elevation; slope; distance from the nearest river; length. Below that, the four factor scores
with their weights, plain-language notes for any data flags, and the analysis date and model. A value the analysis does
not have is shown as "Not recorded" or "No data"; nothing is filled in.

**Colours.** Low sky blue, Medium orange, High vermilion, Severe indigo (a palette that stays distinguishable with the
common forms of colour blindness). Categories also differ by line thickness, and by dash pattern from zoom 15, so they
are not told apart by colour alone. Lines are drawn thinner when zoomed out. Higher scores are drawn on top.

## No built-in data

The frontend holds no road, no statistic and no threshold. Category names, score ranges and labels come from
`/meta/config`; road classes and their counts, the score scale and the analysis date from `/analysis/metadata`; every
figure in the statistics strip from `/roads/summary`. `static/js/risk-config.js` only says how each category *looks*.
A test fails if the statistics markup contains a number, if a script embeds GeoJSON or coordinates, or if a category
threshold appears in the JavaScript.

## Behaviour worth knowing

- **How many roads are drawn.** Every scored road in view, in pages of 5,000: the first page (highest scores) appears
  at once, the others are fetched four at a time and drawn underneath in score order. The whole county takes about
  3.5 s. (Until Phase 14 only the 15,000 highest-scoring roads were drawn, which made the city-wide view look almost
  entirely High; see `phase14_final_report.md`.) A notice appears only if a view ever holds more than 60,000 roads.
- **Only scored roads are drawn.** Footways, paths and similar are not analysed and are not on the map. They can still
  be found by name; their panel says "Not scored" and why.
- **Filters.** Category, IVI range and road class apply to the map and to nearby roads. The statistics follow the road
  class and IVI range and always show the four categories side by side. "Reset all filters" restores everything.
- **Search.** At least two characters. Choosing a name zooms to all its segments and opens the one with the highest
  score, with a note when the name has several segments.
- **Nearby.** "Near me" needs permission to use the browser's location; "Pick on map" needs none. The search area is
  drawn as a circle and results are listed nearest first.
- **Session.** If the session ends, the next request sends the browser to the sign-in page.

## Loading, empty and error states

| Situation | What the user sees |
|---|---|
| Roads, statistics, search, nearby or a road loading | A spinner with a short text in that place |
| No category or no road class selected | "Select at least one …" on the map and in the statistics |
| No road matches the filters in view | "No scored roads match the filters in this area." |
| Map moved outside the county | "This view is outside Nairobi County." with "Back to Nairobi" |
| Search finds nothing / text too short | "No road name contains …" / "Type at least 2 characters." |
| Nothing within the nearby distance | Says so, and suggests a larger distance or checking the point is in the county |
| Location refused, timed out or unavailable | The reason, and "Use Pick on map instead" |
| Server unreachable or error | The message from the server (or "Cannot reach the server…") with "Try again", in the part that failed; the rest keeps working |
| Dashboard cannot load its definitions | "The dashboard could not start: …" with "Try again" |
| No analysis stored yet | "No roads have been analysed yet. Run the scoring pipeline…" |
| Form errors on the sign-in pages | The server's message next to the form; the button is disabled while sending |

## Files

```
static/
  index.html, login.html, register.html, verify-email.html, reset-password.html
  css/style.css        layout, components, small-screen rules
  js/api.js            fetch wrapper: session cookie, errors, sign-in redirect on 401
  js/ui.js             DOM helpers, number and date formatting
  js/risk-config.js    how each category looks
  js/auth.js           the four sign-in pages
  js/app.js            dashboard start-up and wiring
  js/map.js            Leaflet map, road layer, selection highlight, map status
  js/filters.js        category, IVI range and road class controls; legend
  js/summary.js        statistics strip
  js/search.js         road search
  js/popup.js          road information panel
  js/nearby.js         nearby roads
```

Text from the API is inserted with `textContent` only, never as HTML. The email address is passed between the sign-in
pages in the tab's session storage, not in the address bar.

## Small screens and accessibility

Below 860 px the sidebar becomes a drawer behind the "Filters" button and the road panel a sheet at the bottom; the
statistics cards scroll sideways. All controls are real buttons, inputs and labels, usable by keyboard, with a visible
focus ring; status texts are announced to screen readers; Escape closes the road panel.

## Verification

In a browser against the running application and the real data (52,471 roads), with a throw-away account that was
removed afterwards: registration with a mismatched password (message shown), registration, wrong and right
confirmation code, wrong and right password, dashboard load, search ("bypass" → Eastern Bypass, 34 segments, panel
with IVI 4.50 Severe), a click on a road (Kenyatta Avenue, all fields), category, class and IVI filters (requests and
statistics change accordingly), nearby by picked point (50 of 103 roads within 500 m), the 375 px layout, each error
and empty state above by cutting off or failing the requests, recovery with "Try again", and sign-out. The browser
console showed no script errors.

Automated (`tests/api/test_frontend.py`, 48 checks): pages and assets are served, the dashboard redirects without a
session, every referenced file and import exists, every script is valid JavaScript, no built-in data or thresholds,
every feature calls its endpoint, no HTML insertion.

## Limitations

- There are no automated browser tests; interaction was checked by hand as listed.
- The base map tiles come from openstreetmap.org and need internet access; without it the roads are drawn on a plain
  background.
- The account pages cover sign-in, registration, confirmation and reset. Changing a password while signed in has an
  API endpoint but no page yet. The admin page is still a placeholder.
- GeoJSON export has no button because the endpoint is not built.
