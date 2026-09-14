from __future__ import annotations

import typer
import uvicorn

from riftlab.config import get_settings

app = typer.Typer(
    help="RiftLab CLI (cc). Comandos implementados nesta etapa: api, worker, health.",
    no_args_is_help=True,
)


@app.command()
def api(
    host: str = typer.Option(None, help="Sobrescreve RIFTLAB_API_HOST."),
    port: int = typer.Option(None, help="Sobrescreve RIFTLAB_API_PORT."),
    reload: bool = typer.Option(False, help="Recarrega em mudancas de codigo (desenvolvimento)."),
) -> None:
    """Inicia a API FastAPI (uvicorn), health checks e nada mais nesta etapa."""
    settings = get_settings()
    uvicorn.run(
        "riftlab.api.main:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_config=None,
    )


@app.command()
def worker() -> None:
    """Inicia o worker minimo com heartbeat verificavel; sem processamento de fila."""
    from riftlab.worker.main import main as worker_main

    worker_main()


@app.command()
def health(
    host: str = typer.Option("127.0.0.1", help="Host da API a consultar."),
    port: int = typer.Option(None, help="Porta da API; usa RIFTLAB_API_PORT por padrao."),
    timeout: float = typer.Option(3.0, help="Timeout em segundos por chamada."),
) -> None:
    """Consulta /health/live e /health/ready localmente; saida != 0 se alguma falhar."""
    import httpx

    settings = get_settings()
    resolved_port = port or settings.api_port
    exit_code = 0
    for path in ("/health/live", "/health/ready"):
        url = f"http://{host}:{resolved_port}{path}"
        try:
            response = httpx.get(url, timeout=timeout)
            ok = response.status_code == 200
            detail = str(response.status_code)
        except httpx.HTTPError as exc:
            ok = False
            detail = f"sem resposta ({exc.__class__.__name__})"
        typer.echo(f"{path}: {'OK' if ok else 'FAIL'} ({detail})")
        if not ok:
            exit_code = 1
    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()
