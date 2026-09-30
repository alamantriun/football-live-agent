"""Adaptador local para ejecutar una sola vuelta de los jobs de Football Live.

La planificación residente pertenece a la plataforma (Cron/Vercel). Este módulo
conserva una entrada local de depuración sin duplicar la lógica de descubrimiento,
recolección, liquidación ni entrenamiento.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from uuid import UUID, uuid4

from football_live.jobs import JobResult, run_collect, run_discover, run_settle, run_train


def _minute_key(name: str, at: datetime) -> str:
    return f"{name}:{at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')}"


async def run_cycle(
    repo,
    provider,
    predictor,
    trainer,
    *,
    request_id: UUID | None = None,
    at: datetime | None = None,
) -> tuple[JobResult, JobResult, JobResult, JobResult]:
    """Run the same bounded, idempotent jobs used by the cloud entrypoints."""

    current = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current_request_id = request_id or uuid4()
    return (
        await run_discover(repo, provider, current_request_id, _minute_key("discover", current)),
        await run_collect(repo, provider, predictor, current_request_id, _minute_key("collect", current)),
        await run_settle(repo, provider, current_request_id, _minute_key("settle", current)),
        await run_train(repo, trainer, current_request_id, f"train:{current.date().isoformat()}"),
    )


class Recolector:
    """Compatibility facade; callers supply the production dependencies explicitly."""

    def __init__(self, repo, provider, predictor, trainer) -> None:
        self.repo = repo
        self.provider = provider
        self.predictor = predictor
        self.trainer = trainer

    async def ciclo(
        self,
        *,
        request_id: UUID | None = None,
        at: datetime | None = None,
    ) -> tuple[JobResult, JobResult, JobResult, JobResult]:
        return await run_cycle(
            self.repo,
            self.provider,
            self.predictor,
            self.trainer,
            request_id=request_id,
            at=at,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--una-vez", action="store_true")
    parser.parse_args()
    raise SystemExit(
        "Use un entrypoint de la aplicación para construir repo, provider, predictor y trainer; "
        "este módulo ya no crea un scheduler residente."
    )


if __name__ == "__main__":
    main()
