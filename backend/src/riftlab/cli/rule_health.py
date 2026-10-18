"""Relatórios agregados offline de saúde e diferenças de catálogo."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from riftlab.catalog.diff import catalog_diff, patch_catalog
from riftlab.config import get_settings
from riftlab.ingestion.models import MatchModel
from riftlab.shared.db import build_session_factory
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.empirical_rules import load_empirical_rules_or_disabled
from riftlab.timeline.health import compute_health

rules_app = typer.Typer(no_args_is_help=True)


@rules_app.command("health")
def health(patch: str | None = typer.Option(None)) -> None:
    async def run() -> None:
        cfg = get_settings()
        engine = create_async_engine(cfg.database_url)
        sf = build_session_factory(engine)
        try:
            rules = load_empirical_rules_or_disabled(
                Path(cfg.empirical_rules_path),
                reconstruction_version=RECONSTRUCTION_ALGORITHM_VERSION,
            )
            async with sf() as session:
                patches = (
                    [patch]
                    if patch
                    else list((await session.scalars(select(MatchModel.patch).distinct())).all())
                )
            result = []
            for p in patches:
                result.extend(await compute_health(sf, rules, p))
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            await engine.dispose()

    asyncio.run(run())


def catalog_diff_command(
    from_patch: str = typer.Option(..., "--from"), to_patch: str = typer.Option(..., "--to")
) -> None:
    async def run() -> None:
        engine = create_async_engine(get_settings().database_url)
        sf = build_session_factory(engine)
        try:
            async with sf() as session:
                a = await patch_catalog(session, from_patch)
                b = await patch_catalog(session, to_patch)
                if a is None or b is None:
                    typer.echo("CATALOG_UNAVAILABLE")
                    raise typer.Exit(1)
                diff = catalog_diff(
                    {i.item_id: i for i in a.items},
                    {i.item_id: i for i in b.items},
                    a.classification,
                    b.classification,
                )
                grouped: dict[str, list[int]] = {}
                for item, reasons in diff.items():
                    for reason in reasons:
                        grouped.setdefault(reason, []).append(item)
                typer.echo(json.dumps(grouped, ensure_ascii=False, indent=2))
        finally:
            await engine.dispose()

    asyncio.run(run())
