from pathlib import Path


MODEL_PAGE = Path("public/modelo")


def test_model_page_explains_that_live_evidence_is_still_collecting():
    html = (MODEL_PAGE / "index.html").read_text(encoding="utf-8")

    assert "Evidencia live todavía en recolección" in html
    assert "70" in html
    assert "30" in html
    assert "precisión garantizada" not in html.lower()
    assert "apuesta segura" not in html.lower()


def test_model_page_uses_only_same_origin_safe_dom_updates():
    javascript = (MODEL_PAGE / "modelo.js").read_text(encoding="utf-8")

    assert '"/api/model/status"' in javascript
    assert "textContent" in javascript
    assert "AbortController" in javascript
    assert "innerHTML" not in javascript
    assert "insertAdjacentHTML" not in javascript
