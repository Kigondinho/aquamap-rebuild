"""Frontend verification in a real browser: API integration, search, filtering, map rendering, road details.

Every expectation is taken from the API through the browser's own session (``api()`` below) or from the
seeded network, and compared with what the page shows or has drawn. Tests run in file order and share one
signed-in browser; each starts from a reset dashboard.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.e2e]

ORIGIN = (-1.29, 36.82)  # lat, lon of the seeded network
COLOURS = {"Low": "#56B4E9", "Medium": "#E69F00", "High": "#D55E00", "Severe": "#332288"}


# ---------------------------------------------------------------- helpers
def wait(driver, condition: str, timeout: float = 15.0, message: str = "") -> None:
    from selenium.webdriver.support.ui import WebDriverWait

    WebDriverWait(driver, timeout, poll_frequency=0.1).until(
        lambda d: d.execute_script(f"return Boolean({condition});"), message or f"timed out waiting for: {condition}")


def idle(driver) -> None:
    """Wait until the map has finished loading and the statistics strip is no longer loading."""
    wait(driver, "window.aquamap && !window.aquamap.roads.loading && !document.querySelector('#stats .spinner')")


def settle(driver, quiet: float = 0.7, timeout: float = 20.0) -> None:
    """After changing a filter or the view: wait until the map has stopped animating, nothing is loading,
    and what is drawn has stayed the same for ``quiet`` seconds (longer than the 200-300 ms debounces), so a
    late reload cannot start after the test has looked."""
    import time

    snapshot = ("const r = window.aquamap.roads, m = window.aquamap.map;"
                "return JSON.stringify([r.loading, Boolean(m._animatingZoom || m._panAnim?._inProgress), r.drawn.total,"
                " [...r.drawn.ids].sort(), Boolean(document.querySelector('#stats .spinner')), m.getZoom(), m.getCenter()]);")
    deadline = time.time() + timeout
    last, since = None, time.time()
    while time.time() < deadline:
        state = driver.execute_script(snapshot)   # starts "[false,false," when not loading and not animating
        if state != last:
            last, since = state, time.time()
        elif state.startswith("[false,false,") and time.time() - since >= quiet:
            return
        time.sleep(0.05)
    raise AssertionError(f"the dashboard did not settle: {last}")


def api(driver, path: str):
    """Call the API from inside the page (same origin, same session cookie) and return (status, json)."""
    return driver.execute_async_script(
        "const done = arguments[arguments.length - 1];"
        "fetch('/api/v1' + arguments[0], {credentials: 'same-origin'})"
        ".then(async (r) => done([r.status, await r.json()])).catch((e) => done([0, String(e)]));", path)


def text(driver, selector: str) -> str:
    return driver.execute_script("const e = document.querySelector(arguments[0]); return e ? e.innerText : null;", selector)


def css(driver, selector: str):
    from selenium.webdriver.common.by import By

    return driver.find_element(By.CSS_SELECTOR, selector)


def drawn(driver) -> dict:
    return driver.execute_script("return window.aquamap.roads.drawn;")


def view_bbox(driver) -> str:
    return driver.execute_script(
        "const b = window.aquamap.map.getBounds();"
        "return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((n) => n.toFixed(5)).join(',');")


def km(value: float) -> str:
    return f"{round(value):,} km" if value >= 100 else f"{value:.1f} km"


def metres(value: float) -> str:
    return f"{value / 1000:.1f} km" if value >= 1000 else f"{round(value):,} m"


def click_road(driver, road_id: int) -> None:
    """A real mouse click on the middle of a road as drawn on the map."""
    from selenium.webdriver.common.action_chains import ActionChains

    offset = driver.execute_script(
        "let found = null; window.aquamap.roads.layer.eachLayer((g) => g.eachLayer((l) => { if (l.feature.properties.id === arguments[0]) found = l; }));"
        "if (!found) return null; const pts = found.getLatLngs();"
        "const mid = L.latLng(pts[0].lat * 0.5 + pts[1].lat * 0.5, pts[0].lng * 0.5 + pts[1].lng * 0.5);"
        "const p = window.aquamap.map.latLngToContainerPoint(mid); const box = document.getElementById('map').getBoundingClientRect();"
        "return [p.x - box.width / 2, p.y - box.height / 2];", road_id)
    assert offset is not None, f"road {road_id} is not drawn on the map"
    ActionChains(driver).move_to_element_with_offset(css(driver, "#map"), round(offset[0]), round(offset[1])).click().perform()


def facts(driver) -> dict[str, str]:
    """The label/value pairs of the road panel."""
    return driver.execute_script(
        "const out = {}; const dl = document.querySelector('#road-panel dl.facts'); if (!dl) return out;"
        "const dts = dl.querySelectorAll('dt'), dds = dl.querySelectorAll('dd');"
        "dts.forEach((dt, i) => { out[dt.innerText] = dds[i].innerText; }); return out;")


@pytest.fixture(scope="module")
def dashboard(browser, server: str, seeded: dict):
    """Sign in through the real form and open the dashboard on the seeded network."""
    browser.get(f"{server}/login")
    css(browser, "#email").send_keys(seeded["email"])
    css(browser, "#password").send_keys(seeded["password"])
    css(browser, 'button[type="submit"]').click()
    wait(browser, "location.pathname === '/'", message="sign-in did not reach the dashboard")
    idle(browser)
    return browser


@pytest.fixture
def page(dashboard, server: str):
    """A reset dashboard: default filters, no panel, no search text, the network in view at zoom 16."""
    driver = dashboard
    center = (ORIGIN[0] + 0.0009, ORIGIN[1] + 0.0015)
    settle(driver)  # let any zoom animation left by the previous test finish, or it would move the map afterwards
    driver.execute_script(
        "document.getElementById('reset-filters').click();"
        "const s = document.getElementById('search'); s.value = ''; s.dispatchEvent(new Event('input'));"
        "document.getElementById('nearby-clear').click();"
        "if (!document.getElementById('road-panel').hidden) window.aquamap.panel.close();"
        "window.aquamap.map.setView([arguments[0], arguments[1]], 16, {animate: false});", *center)
    settle(driver)
    view = driver.execute_script("const m = window.aquamap.map, c = m.getCenter(); return [c.lat, c.lng, m.getZoom()];")
    # Leaflet rounds the centre to whole screen pixels (about 2.4 m at zoom 16): allow two pixels.
    assert view == [pytest.approx(center[0], abs=5e-5), pytest.approx(center[1], abs=5e-5), 16], f"map did not stay at the start view: {view}"
    return driver


# ================================================================ access
def test_anonymous_visitor_is_sent_to_the_sign_in_page(browser, server: str) -> None:
    browser.delete_all_cookies()
    browser.get(f"{server}/")
    wait(browser, "location.pathname === '/login'")
    assert "Sign in" in browser.title


def test_wrong_password_shows_the_servers_message_and_stays_on_the_page(browser, server: str, seeded: dict) -> None:
    browser.get(f"{server}/login")
    css(browser, "#email").send_keys(seeded["email"])
    css(browser, "#password").send_keys("Wrong-pass-123")
    css(browser, 'button[type="submit"]').click()
    wait(browser, "document.querySelector('#form-message .msg.error')")
    assert text(browser, "#form-message") == "Incorrect email or password."
    assert browser.execute_script("return location.pathname") == "/login"


def test_registration_form_checks_input_before_sending(browser, server: str) -> None:
    browser.get(f"{server}/register")
    css(browser, "#full_name").send_keys("Someone New")
    css(browser, "#email").send_keys("someone@example.com")
    css(browser, "#password").send_keys("Valid-pass-1")
    css(browser, "#confirm_password").send_keys("Different-1")
    css(browser, 'button[type="submit"]').click()
    wait(browser, "document.querySelector('#form-message .msg.error')")
    assert text(browser, "#form-message") == "The two passwords do not match."


# ================================================================ API integration
def test_sign_in_opens_the_dashboard_for_the_signed_in_user(page) -> None:
    status, me = api(page, "/auth/me")
    assert status == 200
    assert text(page, "#user-name") == me["full_name"] == "Browser Tester"


def test_statistics_show_exactly_what_the_api_reports(page) -> None:
    _, summary = api(page, "/roads/summary")
    cards = page.execute_script(
        "return [...document.querySelectorAll('#stats .stat')].map((c) => "
        "[c.querySelector('.label').innerText, c.querySelector('.value').innerText, c.querySelector('.sub').innerText]);")
    scored_km = sum(t["length_km"] for t in summary["by_tier"])
    scored_segments = sum(t["segments"] for t in summary["by_tier"])
    assert cards[0] == ["SCORED ROAD LENGTH", km(scored_km), f"{scored_segments:,} road segments"]
    assert scored_segments == 6 and summary["unscored_segments"] == 2      # the seeded network, nothing else
    for card, tier in zip(cards[1:], summary["by_tier"], strict=True):
        share = tier["length_km"] / scored_km * 100
        assert card == [tier["tier"].upper(), km(tier["length_km"]), f"{share:.1f} % of length · {tier['segments']:,} segments"]
    note = text(page, "#stats .stats-note")
    assert f"{summary['unscored_segments']} other roads" in note and "Totals for all of Nairobi County." in note


def test_legend_shows_the_categories_and_ranges_of_the_active_configuration(page) -> None:
    _, meta = api(page, "/meta/config")
    rows = page.execute_script("return [...document.querySelectorAll('#legend li')].map((li) => li.innerText.replace(/\\s+/g, ' ').trim());")
    expected = []
    for tier in reversed(meta["tiers"]):
        upper = f"below {tier['upper_bound']:.1f}" if tier["upper_bound"] is not None else f"{meta['scale_max']:.1f}"
        expected.append(f"{tier['tier']} {tier['lower_bound']:.1f} – {upper}")
    assert rows == expected == ["Severe 4.0 – 5.0", "High 3.0 – below 4.0", "Medium 2.0 – below 3.0", "Low 1.0 – below 2.0"]
    assert "Not a flood forecast" in text(page, "#legend")


def test_filter_choices_come_from_the_analysis_metadata(page) -> None:
    _, meta = api(page, "/analysis/metadata")
    scored_classes = {c["highway_type"]: c["with_index"] for c in meta["coverage"]["road_classes"] if c["with_index"] > 0}
    shown = dict(page.execute_script(
        "return [...document.querySelectorAll('#class-filters label')].map((l) => "
        "[l.querySelector('input').value, Number(l.querySelector('.count').innerText.replace(/,/g, ''))]);"))
    assert shown == scored_classes == {"primary": 1, "residential": 2, "tertiary": 2, "service": 1}
    assert "footway" not in shown                                          # a class with no scored road is not offered
    assert page.execute_script("return [...document.querySelectorAll('#tier-filters input')].map((i) => i.value);") == \
        [t["tier"] for t in api(page, "/meta/config")[1]["tiers"]]


# ================================================================ map rendering
def test_map_draws_every_scored_road_in_view_in_its_category_style(page, seeded: dict) -> None:
    _, expected = api(page, f"/roads?bbox={view_bbox(page)}&limit=5000&tier=Low&tier=Medium&tier=High&tier=Severe")
    state = drawn(page)
    assert state["total"] == expected["pagination"]["total"] == 6
    assert sorted(state["ids"]) == sorted(f["properties"]["id"] for f in expected["features"])
    assert state["byCategory"] == {"Severe": 1, "High": 1, "Medium": 3, "Low": 1}
    assert seeded["roads"][None]["id"] not in state["ids"] and seeded["roads"]["Giza Road"]["id"] not in state["ids"]

    layers = page.execute_script(
        "const out = []; window.aquamap.roads.layer.eachLayer((group) => group.eachLayer((line) => out.push("
        "[line.feature.properties.id, line.feature.properties.risk_category, line.options.color, line.options.weight,"
        " line.options.dashArray, line.getLatLngs().length]))); return out;")
    assert len(layers) == 6
    by_id = {f["properties"]["id"]: f for f in expected["features"]}
    for road_id, category, colour, weight, _dash, points in layers:
        assert category == by_id[road_id]["properties"]["risk_category"]
        assert colour == COLOURS[category] and points == len(by_id[road_id]["geometry"]["coordinates"])
        assert weight > 0
    weights = {category: weight for _id, category, _c, weight, _d, _p in layers}
    assert weights["Low"] < weights["Medium"] < weights["High"] < weights["Severe"]     # not colour alone
    dashes = {category: dash for _id, category, _c, _w, dash, _p in layers}
    assert dashes["Low"] is None and dashes["High"] and dashes["Severe"]              # zoom 16: dash patterns on


def test_roads_are_actually_painted_on_the_canvas_in_their_colours(page) -> None:
    """Read the pixels Leaflet painted: each category's colour must be present, at the place of its road."""
    result = page.execute_script("""
        const map = window.aquamap.map, canvas = document.querySelector('#map canvas');
        const ctx = canvas.getContext('2d'), ratio = canvas.width / canvas.clientWidth;
        const box = canvas.getBoundingClientRect(), host = document.getElementById('map').getBoundingClientRect();
        const near = (rgb, hex) => { const n = parseInt(hex.slice(1), 16);
          return Math.abs(rgb[0] - (n >> 16)) + Math.abs(rgb[1] - ((n >> 8) & 255)) + Math.abs(rgb[2] - (n & 255)) < 90; };
        const out = {painted: 0, roads: []};
        const all = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 3; i < all.length; i += 4) if (all[i] > 0) out.painted += 1;
        window.aquamap.roads.layer.eachLayer((group) => group.eachLayer((line) => {
          const pts = line.getLatLngs(); const mid = L.latLng((pts[0].lat + pts[1].lat) / 2, (pts[0].lng + pts[1].lng) / 2);
          const p = map.latLngToContainerPoint(mid);
          const x = Math.round((p.x + host.left - box.left) * ratio), y = Math.round((p.y + host.top - box.top) * ratio);
          let hit = false;                       // dashed lines have gaps: look along a short stretch of the road
          for (let dy = -40; dy <= 40 && !hit; dy += 1) for (let dx = -3; dx <= 3 && !hit; dx += 1) {
            const px = ctx.getImageData(x + dx, y + dy, 1, 1).data;
            if (px[3] > 0 && near(px, line.options.color)) hit = true;
          }
          out.roads.push([line.feature.properties.risk_category, hit]);
        }));
        return out;""")
    assert result["painted"] > 500, "nothing was painted on the map canvas"
    assert len(result["roads"]) == 6 and all(hit for _category, hit in result["roads"]), result["roads"]


def test_zooming_out_keeps_the_roads_and_thins_the_lines(page) -> None:
    page.execute_script("window.aquamap.map.setView([arguments[0], arguments[1]], 12, {animate: false});", *ORIGIN)
    settle(page)
    assert drawn(page)["total"] == 6
    styles = page.execute_script(
        "const out = []; window.aquamap.roads.layer.eachLayer((g) => g.eachLayer((l) => out.push([l.options.weight, l.options.dashArray]))); return out;")
    assert all(dash is None for _weight, dash in styles) and max(weight for weight, _dash in styles) < 3


def test_moving_the_map_away_shows_the_outside_county_notice(page) -> None:
    page.execute_script("window.aquamap.map.setView([-2.6, 38.2], 13, {animate: false});")
    settle(page)
    assert drawn(page)["total"] == 0
    assert "outside Nairobi County" in text(page, "#map-status")
    css(page, "#map-status button").click()                       # "Back to Nairobi"
    settle(page)
    assert page.execute_script("return window.aquamap.map.getCenter().lat") == pytest.approx(-1.286, abs=0.01)


# ================================================================ filtering
def test_category_filter_changes_what_is_drawn_to_match_the_api(page) -> None:
    css(page, '#tier-filters input[value="Severe"]').click()
    css(page, '#tier-filters input[value="Low"]').click()
    settle(page)
    _, expected = api(page, f"/roads?bbox={view_bbox(page)}&limit=5000&tier=Medium&tier=High")
    state = drawn(page)
    assert state["byCategory"] == {"High": 1, "Medium": 3}
    assert sorted(state["ids"]) == sorted(f["properties"]["id"] for f in expected["features"])
    css(page, '#tier-filters input[value="Medium"]').click()
    css(page, '#tier-filters input[value="High"]').click()
    settle(page)
    assert drawn(page)["total"] == 0 and text(page, "#map-status") == "Select at least one vulnerability category."


def test_ivi_range_filter_updates_the_map_and_the_statistics(page, seeded: dict) -> None:
    page.execute_script("const s = document.getElementById('ivi-min'); s.value = '2.8'; s.dispatchEvent(new Event('input'));"
                        "const m = document.getElementById('ivi-max'); m.value = '4.0'; m.dispatchEvent(new Event('input'));")
    settle(page)
    assert (text(page, "#ivi-min-value"), text(page, "#ivi-max-value")) == ("2.8", "4.0")
    names = {name: r["id"] for name, r in seeded["roads"].items()}
    assert sorted(drawn(page)["ids"]) == sorted([names["Bonde Lane"], names["Kati Street"]])   # 3.85 and 2.85
    _, summary = api(page, "/roads/summary?min_ivi=2.8&max_ivi=4")
    assert text(page, "#stats .stat .sub") == f"{sum(t['segments'] for t in summary['by_tier'])} road segments" == "2 road segments"
    assert "selected road classes and IVI range" in text(page, "#stats .stats-note")


def test_ivi_range_handles_cannot_cross(page) -> None:
    page.execute_script("const s = document.getElementById('ivi-min'); s.value = '4.5'; s.dispatchEvent(new Event('input'));"
                        "const m = document.getElementById('ivi-max'); m.value = '3.0'; m.dispatchEvent(new Event('input'));")
    settle(page)
    assert (text(page, "#ivi-min-value"), text(page, "#ivi-max-value")) == ("3.0", "3.0")   # the minimum followed the maximum


def test_road_class_filter(page, seeded: dict) -> None:
    css(page, "#classes-none").click()
    settle(page)
    assert drawn(page)["total"] == 0
    assert text(page, "#map-status") == "Select at least one road classification."
    assert text(page, "#stats") == "Select at least one road classification to see statistics."
    css(page, '#class-filters input[value="residential"]').click()
    settle(page)
    _, expected = api(page, f"/roads?bbox={view_bbox(page)}&limit=5000&tier=Low&tier=Medium&tier=High&tier=Severe&highway=residential")
    assert sorted(drawn(page)["ids"]) == sorted(f["properties"]["id"] for f in expected["features"])
    assert len(expected["features"]) == 2                                   # Giza Road is residential too, but has no index
    _, summary = api(page, "/roads/summary?highway=residential")
    assert text(page, "#stats .stat .value") == km(sum(t["length_km"] for t in summary["by_tier"]))
    css(page, "#classes-all").click()
    settle(page)
    assert drawn(page)["total"] == 6


def test_reset_restores_all_filters(page) -> None:
    css(page, '#tier-filters input[value="Medium"]').click()
    css(page, "#classes-none").click()
    settle(page)
    css(page, "#reset-filters").click()
    settle(page)
    assert drawn(page)["total"] == 6 and page.execute_script("return document.getElementById('map-status').hidden") is True


# ================================================================ search
def test_search_lists_matching_names_from_the_api(page) -> None:
    css(page, "#search").send_keys("kati")
    wait(page, "document.querySelectorAll('#search-results li').length > 0")
    _, expected = api(page, "/roads/search?q=kati&limit=10")
    shown = page.execute_script("return [...document.querySelectorAll('#search-results li')].map((li) => li.innerText.replace(/\\s+/g, ' ').trim());")
    assert len(shown) == len(expected["features"]) == 1
    p = expected["features"][0]["properties"]
    assert p["segments"] == 2 and p["vulnerability_index"] == 2.85
    assert shown[0] == f"{p['name']} Medium 2 segments · {metres(p['length_m'])} · highest IVI 2.85"


def test_choosing_a_search_result_opens_that_road(page, seeded: dict) -> None:
    css(page, "#search").send_keys("mto")
    wait(page, "document.querySelectorAll('#search-results li').length > 0")
    css(page, "#search-results button").click()
    wait(page, "document.querySelector('#road-panel dl.facts')")
    assert text(page, "#road-title") == "Mto Road"
    assert page.execute_script("return window.aquamap.panel.currentId") == seeded["roads"]["Mto Road"]["id"]
    assert page.execute_script("let n = 0; window.aquamap.roads.highlight.eachLayer(() => { n += 1; }); return n;") == 2  # halo + line


def test_search_states_for_short_text_and_no_match(page) -> None:
    box = css(page, "#search")
    box.send_keys("k")
    wait(page, "!document.getElementById('search-state').hidden")
    assert text(page, "#search-state") == "Type at least 2 characters."
    box.send_keys("zzzz")
    wait(page, "document.getElementById('search-state').innerText.includes('No road name')")
    assert text(page, "#search-state") == "No road name contains “kzzzz”."
    assert page.execute_script("return document.querySelectorAll('#search-results li').length") == 0


def test_search_text_with_wildcards_is_literal(page) -> None:
    css(page, "#search").send_keys("100%")
    wait(page, "document.querySelectorAll('#search-results li').length > 0")
    assert page.execute_script("return [...document.querySelectorAll('#search-results .title span:first-child')].map((e) => e.innerText);") == \
        ["Njia 100% Way"]


def test_an_unscored_road_is_found_by_name_and_says_why_it_has_no_score(page) -> None:
    css(page, "#search").send_keys("giza")
    wait(page, "document.querySelectorAll('#search-results li').length > 0")
    assert "Not scored" in text(page, "#search-results li")
    css(page, "#search-results button").click()
    wait(page, "document.querySelector('#road-panel dl.facts')")
    shown = facts(page)
    assert shown["IVI score"] == "None" and shown["Vulnerability category"] == "None"
    assert shown["Elevation"] == "No data" and shown["Slope"] == "No data" and shown["Distance from nearest river"] == "150 m"
    assert "No elevation data under this road" in text(page, "#road-panel")


# ================================================================ road detail inspection
@pytest.mark.parametrize("name", ["Mto Road", "Bonde Lane", "Juu Avenue"])
def test_clicking_a_road_on_the_map_shows_its_stored_details(page, seeded: dict, name: str) -> None:
    road = seeded["roads"][name]
    click_road(page, road["id"])   # a real mouse click on the map canvas, where the road is drawn
    wait(page, "document.querySelector('#road-panel dl.facts')", message=f"clicking {name} did not open its panel")

    _, detail = api(page, f"/roads/{road['id']}")
    p = detail["properties"]
    assert page.execute_script("return window.aquamap.panel.currentId") == road["id"]
    assert text(page, "#road-title") == name == p["name"]
    assert f"OpenStreetMap way {p['osm_id']}" in text(page, "#road-panel .id")
    shown = facts(page)
    assert shown == {
        "Road classification": p["highway_type"].capitalize(),
        "Surface type": p["surface"].capitalize() if p["surface"] else "Not recorded",
        "IVI score": f"{p['vulnerability_index']:.2f}",
        "Vulnerability category": p["risk_category"],
        "Elevation": f"{round(p['elevation_m']):,} m",
        "Slope": f"{p['slope_pct']:.1f} %",
        "Distance from nearest river": metres(p["river_dist_m"]),
        "Length": metres(p["length_m"]),
    }
    # ... and those are the values that were stored for this road
    assert (p["vulnerability_index"], p["risk_category"]) == (road["ivi"], road["tier"])
    assert (p["elevation_m"], p["slope_pct"], p["river_dist_m"], p["surface"]) == (road["elevation"], road["slope"], road["river"], road["surface"])
    assert text(page, "#road-panel .score .number") == f"{road['ivi']:.2f}" and text(page, "#road-panel .score .chip") == road["tier"]
    factor_rows = page.execute_script("return [...document.querySelectorAll('#road-panel .factor .val')].map((e) => e.innerText);")
    assert factor_rows == [f"{p['factor_scores'][k]:g} / 5" for k in ("elevation", "slope", "river", "surface")]


def test_roads_stay_clickable_after_a_selection_and_a_nearby_search(page, seeded: dict) -> None:
    """Regression: the selection outline and the nearby circle once covered the roads and swallowed every later click."""
    roads = seeded["roads"]
    click_road(page, roads["Mto Road"]["id"])
    wait(page, f"window.aquamap.panel.currentId === {roads['Mto Road']['id']} && document.querySelector('#road-panel dl.facts')")
    click_road(page, roads["Juu Avenue"]["id"])                  # a second road while the first is outlined
    wait(page, f"window.aquamap.panel.currentId === {roads['Juu Avenue']['id']} && document.querySelector('#road-panel dl.facts')",
         message="a second road could not be selected while another was outlined")
    assert text(page, "#road-title") == "Juu Avenue"
    page.execute_script("window.aquamap.panel.close();")
    css(page, "#near-pick").click()                              # draw the nearby circle over the roads
    page.execute_script("window.aquamap.map.fire('click', {latlng: L.latLng(arguments[0], arguments[1])});", *ORIGIN)
    wait(page, "document.querySelectorAll('#nearby-results li').length > 0")
    settle(page)
    click_road(page, roads["Bonde Lane"]["id"])                  # inside the circle
    wait(page, f"window.aquamap.panel.currentId === {roads['Bonde Lane']['id']}",
         message="a road inside the nearby circle could not be selected")


def test_closing_the_panel_clears_the_selection(page, seeded: dict) -> None:
    page.execute_script("window.aquamap.panel.open(arguments[0]);", seeded["roads"]["Mto Road"]["id"])
    wait(page, "document.querySelector('#road-panel dl.facts')")
    css(page, '#road-panel button[aria-label="Close road information"]').click()
    wait(page, "document.getElementById('road-panel').hidden")
    assert page.execute_script("let n = 0; window.aquamap.roads.highlight.eachLayer(() => { n += 1; }); return n;") == 0


def test_a_road_that_no_longer_exists_gives_a_clear_message(page) -> None:
    page.execute_script("window.aquamap.panel.open(999999999);")
    wait(page, "document.querySelector('#road-panel .state.error')")
    assert text(page, "#road-panel .state.error").strip() == "This road is no longer in the database."


# ================================================================ nearby roads
def test_nearby_roads_around_a_picked_point(page) -> None:
    from selenium.webdriver.common.action_chains import ActionChains

    css(page, "#near-pick").click()
    assert text(page, "#nearby-state") == "Click a place on the map."
    offset = page.execute_script(
        "const p = window.aquamap.map.latLngToContainerPoint(L.latLng(arguments[0], arguments[1]));"
        "const box = document.getElementById('map').getBoundingClientRect(); return [p.x - box.width / 2, p.y - box.height / 2];", *ORIGIN)
    ActionChains(page).move_to_element_with_offset(css(page, "#map"), round(offset[0]), round(offset[1])).click().perform()
    wait(page, "document.querySelectorAll('#nearby-results li').length > 0")
    listed = page.execute_script("return [...document.querySelectorAll('#nearby-results .title span:first-child')].map((e) => e.innerText);")
    # roads within 500 m of the origin, nearest first: 100 m, 200 m, 250 m, 350 m, 400 m (the click is exact to a pixel or two)
    assert listed == ["Mto Road", "Juu Avenue", "Bonde Lane", "Njia 100% Way", "Kati Street"]
    assert text(page, "#nearby-state") == "5 roads within 500 m of the chosen point."
    first = text(page, "#nearby-results li")
    assert "Severe" in first and "IVI 5.00" in first and "m away" in first


def test_location_refusal_is_explained(page) -> None:
    # Headless Firefox has no location service: stand in for the browser's answer "the user refused".
    page.execute_script("Object.defineProperty(navigator, 'geolocation', {configurable: true, value: {"
                        "getCurrentPosition: (ok, fail) => fail({code: 1, PERMISSION_DENIED: 1, TIMEOUT: 3})}});")
    css(page, "#near-me").click()
    wait(page, "document.getElementById('nearby-state').innerText.includes('not allowed')")
    assert text(page, "#nearby-state") == "Location access was not allowed. Use “Pick on map” instead."


# ================================================================ failure handling
def test_a_failing_server_is_reported_and_retry_recovers(page) -> None:
    page.execute_script("window.__realFetch = window.fetch; window.fetch = () => Promise.reject(new TypeError('offline'));")
    css(page, '#tier-filters input[value="Low"]').click()
    wait(page, "document.getElementById('map-status').innerText.includes('Could not load roads')")
    assert "Cannot reach the server" in text(page, "#map-status")
    wait(page, "document.getElementById('stats').innerText.includes('Statistics are not available')")
    assert drawn(page)["total"] == 0
    page.execute_script("window.fetch = window.__realFetch;")
    css(page, "#map-status button").click()                       # "Try again"
    settle(page)
    assert drawn(page)["byCategory"] == {"Severe": 1, "High": 1, "Medium": 3}


def test_an_ended_session_sends_the_browser_to_sign_in(page, server: str) -> None:
    page.delete_all_cookies()
    css(page, '#tier-filters input[value="Low"]').click()         # the next API call answers 401
    wait(page, "location.pathname === '/login'")
    assert "Sign in" in page.title


def test_sign_out_ends_the_session(browser, server: str, seeded: dict) -> None:
    browser.get(f"{server}/login")
    css(browser, "#email").send_keys(seeded["email"])
    css(browser, "#password").send_keys(seeded["password"])
    css(browser, 'button[type="submit"]').click()
    wait(browser, "location.pathname === '/'")
    idle(browser)
    css(browser, "#sign-out").click()
    wait(browser, "location.pathname === '/login'")
    browser.get(f"{server}/")
    wait(browser, "location.pathname === '/login'")               # the dashboard is closed again
