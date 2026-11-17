"""A view with more roads than one API page: the map must draw all of them, in score order.

Regression for a defect found in the Phase 14 verification: only the 15,000 highest-scoring roads of a
city-wide view were drawn, so the overview looked almost entirely High although most roads are Medium.
This module seeds its own SYNTHETIC grid of 16,000 short roads (more than the old limit and three pages of 5,000) with scores
spread over all four categories, in the TEST database, and removes them afterwards.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.schemas.auth import RegisterRequest
from app.services.auth import register_user
from core import ivi
from tests import testdb
from tests.db.conftest import ORIGIN_SQL
from tests.e2e.test_dashboard import css, settle, wait

pytestmark = [pytest.mark.db, pytest.mark.e2e]

ROADS = 16_000         # more than the old 15,000 limit, and more than three pages of 5,000
COLUMNS = 160          # 160 x 100 grid, 25 m apart: about 4 km x 2.5 km around the origin
EMAIL = "large-view@example.com"


@pytest.fixture(scope="module")
def seeded(db_engine: Engine) -> Iterator[dict]:
    """Overrides the dashboard module's network: a dense grid of scored roads and one confirmed account."""
    testdb.empty(db_engine)
    password = "Lv1-" + secrets.token_urlsafe(12)
    stored = ivi.ScoringConfig().to_storage()
    with db_engine.begin() as conn:
        cfg = conn.execute(text(
            "INSERT INTO scoring_config (weights, breakpoints, surface_map, tier_thresholds, is_active) VALUES "
            "(CAST(:w AS jsonb), CAST(:b AS jsonb), CAST(:s AS jsonb), CAST(:t AS jsonb), TRUE) RETURNING config_version"),
            {"w": json.dumps(stored["weights"]), "b": json.dumps(stored["breakpoints"]),
             "s": json.dumps(stored["surface_map"]), "t": json.dumps(stored["tier_thresholds"])}).scalar_one()
        conn.execute(text(
            f"INSERT INTO road_segments (osm_id, highway_type, geom) SELECT 700000 + g, 'residential', "
            f"ST_MakeLine(ST_Translate({ORIGIN_SQL}, (g % :cols) * 25 - 2000, (g / :cols) * 25 - 1250), "
            f"ST_Translate({ORIGIN_SQL}, (g % :cols) * 25 - 2000, (g / :cols) * 25 - 1230)) "
            "FROM generate_series(0, :n - 1) g"), {"cols": COLUMNS, "n": ROADS})
        # IVI 1.000 .. 4.997 spread evenly, so all four categories appear; the category comes from the SQL rule.
        conn.execute(text(
            "INSERT INTO road_vulnerability (road_id, config_version, vulnerability_index, risk_category) "
            "SELECT road_id, :c, (1 + (osm_id % 4000) / 1000.0)::numeric(4,3), "
            "ivi_tier((1 + (osm_id % 4000) / 1000.0)::numeric(4,3), (SELECT tier_thresholds FROM scoring_config WHERE config_version = :c)) "
            "FROM road_segments"), {"c": cfg})
    with Session(db_engine) as session:
        register_user(session, RegisterRequest(full_name="Large View", email=EMAIL, password=password,
                                               confirm_password=password), verified=True)
    with db_engine.connect() as conn:
        counts = dict(conn.execute(text("SELECT risk_category, count(*) FROM road_vulnerability GROUP BY 1")).all())
    yield {"email": EMAIL, "password": password, "counts": counts}
    testdb.empty(db_engine)


def test_every_road_of_a_large_view_is_drawn_in_score_order(browser, server: str, seeded: dict) -> None:
    driver = browser
    driver.delete_all_cookies()
    driver.get(f"{server}/login")
    css(driver, "#email").send_keys(seeded["email"])
    css(driver, "#password").send_keys(seeded["password"])
    css(driver, 'button[type="submit"]').click()
    wait(driver, "location.pathname === '/' && window.aquamap", 30)
    driver.execute_script("window.aquamap.map.setView([-1.29, 36.82], 14, {animate: false});")
    settle(driver, timeout=90)

    drawn = driver.execute_script("return window.aquamap.roads.drawn;")
    assert drawn["matching"] == ROADS, "the view must contain the whole grid for this test to mean anything"
    assert drawn["total"] == ROADS and len(set(drawn["ids"])) == ROADS          # all of them, none twice
    assert drawn["byCategory"] == seeded["counts"]                                # the same mix as the database
    assert set(drawn["byCategory"]) == {"Low", "Medium", "High", "Severe"}
    assert driver.execute_script("return document.getElementById('map-status').hidden") is True   # no "showing only" notice

    order = driver.execute_script(
        "const v = []; let o = window.aquamap.roads.renderer._drawFirst;"
        "while (o) { v.push(o.layer.feature.properties.vulnerability_index); o = o.next; } return v;")
    assert len(order) == ROADS and order == sorted(order)                         # painted low to high: higher on top
