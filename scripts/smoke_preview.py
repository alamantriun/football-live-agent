"""Bounded smoke checks for a Vercel Preview or Production deployment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


TIMEOUT_SECONDS = 10
SECRET_PATTERN = re.compile(r"(?:sb_secret_|service_role|cron_secret|begin (?:rsa|openssh|ec) private key)", re.IGNORECASE)
REQUIRED_HEADERS = {
    "content-security-policy": "frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
}


def fetch(base_url: str, path: str) -> tuple[int, dict[str, str], bytes]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    request = Request(url, headers={"Accept": "application/json, text/html;q=0.9"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, dict(response.headers.items()), response.read(200_000)
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read(200_000)
    except URLError as error:
        raise RuntimeError(f"No fue posible consultar {url}: {error.reason}") from error


def require_headers(headers: dict[str, str], path: str) -> None:
    normalized = {key.lower(): value.lower() for key, value in headers.items()}
    for key, expected in REQUIRED_HEADERS.items():
        if expected not in normalized.get(key, ""):
            raise RuntimeError(f"{path} no incluye el encabezado seguro {key} esperado")


def require_safe_body(body: bytes, path: str) -> None:
    if SECRET_PATTERN.search(body.decode("utf-8", errors="replace")):
        raise RuntimeError(f"{path} contiene texto con apariencia de secreto")


def require_json(body: bytes, path: str) -> dict:
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{path} no devolvió JSON válido") from error
    if not isinstance(decoded, dict):
        raise RuntimeError(f"{path} no devolvió un objeto JSON")
    return decoded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("La URL debe usar HTTPS fuera de desarrollo local.")

    checks = (("/", 200), ("/app", 200), ("/modelo", 200), ("/api/health", 200), ("/api/live?limit=1", 200), ("/api/model/status", None))
    for path, expected_status in checks:
        status, headers, body = fetch(args.base_url, path)
        require_headers(headers, path)
        require_safe_body(body, path)
        if expected_status is not None and status != expected_status:
            raise RuntimeError(f"{path} respondió {status}; se esperaba {expected_status}")
        if path.startswith("/api/"):
            payload = require_json(body, path)
            if path == "/api/model/status" and status == 503:
                if payload.get("error", {}).get("code") != "unavailable":
                    raise RuntimeError("El estado de modelo no explicó su indisponibilidad")
            elif status != 200:
                raise RuntimeError(f"{path} respondió {status}")
            elif "request_id" not in payload:
                raise RuntimeError(f"{path} no incluyó request_id")
    print("Smoke de páginas públicas y API superado.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"SMOKE FAILED: {error}", file=sys.stderr)
        raise SystemExit(1)
