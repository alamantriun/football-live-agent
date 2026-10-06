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


def test_motion_is_scoped_to_product_and_live_match_pages():
    landing = (PUBLIC / "index.html").read_text(encoding="utf-8")
    live = (PUBLIC / "app" / "index.html").read_text(encoding="utf-8")
    site_css = (PUBLIC / "assets" / "site.css").read_text(encoding="utf-8")
    overrides = (PUBLIC / "assets" / "landing-overrides.css").read_text(encoding="utf-8")
    app_css = (PUBLIC / "app" / "app.css").read_text(encoding="utf-8")

    assert '<body class="product-page">' in landing
    assert '<body class="live-page">' in live
    assert ".product-page .reveal.is-visible" in site_css
    assert "@keyframes product-page-enter" in site_css
    assert ".product-page .reading" in site_css
    assert ".live-page .app-hero" in app_css
    assert "@keyframes live-page-enter" in app_css
    assert ".live-page .match-button:hover" in app_css
    assert ".live-page .analytics-flow>section:not([hidden])" in app_css
    assert ".reveal { opacity: 1; transform: none; }" not in overrides
    assert "prefers-reduced-motion" in site_css and "prefers-reduced-motion" in app_css


def test_landing_demo_uses_real_arsenal_and_chelsea_crests():
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    app_javascript = (PUBLIC / "app" / "app.js").read_text(encoding="utf-8")
    vercel = Path("vercel.json").read_text(encoding="utf-8")

    assert 'alt="Escudo del Arsenal"' in html
    assert 'alt="Escudo del Chelsea"' in html
    assert "/v20/Competitors/104" in html
    assert "/Competitors/106" in html
    assert "freebiesupply" not in html.lower()
    assert "freebiesupply" not in app_javascript.lower()
    assert "freebiesupply" not in vercel.lower()
