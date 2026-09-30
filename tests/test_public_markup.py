from pathlib import Path


PUBLIC = Path("public")


def test_landing_structure_and_approved_reading_order():
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")

    assert html.index('id="hero"') < html.index('id="live-reading"')
    assert "Demostración ilustrativa" in html
    assert 'href="/app"' in html
    assert 'href="/modelo"' in html
    assert "01" not in html and "02" not in html and "03" not in html
    assert "wikipedia" not in html.lower()


def test_landing_uses_local_assets_and_accessible_motion_controls():
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    css = (PUBLIC / "assets" / "site.css").read_text(encoding="utf-8")
    javascript = (PUBLIC / "assets" / "site.js").read_text(encoding="utf-8")

    assert 'src="/assets/football-players-white-kits.png"' in html
    assert "prefers-reduced-motion" in css
    assert "#fff" in css.lower() or "white" in css.lower()
    assert "innerHTML" not in javascript
    assert "onerror=" not in html.lower()
    assert (PUBLIC / "assets" / "football-players-white-kits.png").is_file()
    assert (PUBLIC / "assets" / "fonts" / "README.md").is_file()
