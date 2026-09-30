from pathlib import Path


APP = Path("public/app")


def test_dashboard_has_a_safe_same_origin_live_data_contract():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")

    assert 'id="match-list"' in html
    assert 'id="match-detail"' in html
    assert 'href="/"' in html and 'href="/modelo"' in html
    assert '"/api/live?limit=24"' in javascript
    assert '"/api/matches/"' in javascript
    assert "AbortController" in javascript
    assert "document.hidden" in javascript
    assert "innerHTML" not in javascript
    assert "insertAdjacentHTML" not in javascript
    assert "document.write" not in javascript
    assert "eval(" not in javascript


def test_dashboard_declares_all_live_data_states_and_safe_logo_fallback():
    javascript = (APP / "app.js").read_text(encoding="utf-8")

    for state in ("loading", "empty", "fresh", "degraded", "stale", "suspended", "error"):
        assert f'"{state}"' in javascript
    assert "URL(" in javascript
    assert "https:" in javascript
    assert "loading" in javascript
    assert "localStorage" in javascript
