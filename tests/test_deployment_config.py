import json
from pathlib import Path


def test_security_headers_cover_all_routes():
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    headers = {item["key"].lower(): item["value"] for item in config["headers"][0]["headers"]}

    assert "content-security-policy" in headers
    assert "unsafe-eval" not in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_vercel_routes_keep_public_pages_and_api_in_the_same_origin():
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    rewrites = {(rule["source"], rule["destination"]) for rule in config["rewrites"]}

    assert ("/app", "/app/index.html") in rewrites
    assert ("/modelo", "/modelo/index.html") in rewrites
    assert ("/api/(.*)", "/api/index.py") in rewrites
