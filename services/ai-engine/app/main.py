"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.config import load_anomaly_rules, load_assess_rules, load_settings
from app.providers import create_ai_provider, create_data_provider
from app.routers import actions, analysis, anomalies, industrial
from app.routers.actions import ActionStore

DESCRIPTION = """\
FactoryMind AI 引擎：确定性指标计算、异常检测、FMEA 匹配与 AI 解释层。
数据源由 `DATA_PROVIDER` 决定（mock | supos），AI 由 `AI_PROVIDER` 决定。
"""


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Providers that hold a network client (e.g. SupOsProvider) may need to
    # start background work — the MQTT event subscriber — and to release their
    # connections on shutdown. The mock has neither hook and is simply skipped.
    provider = getattr(app.state, "data_provider", None)
    starter = getattr(provider, "astart", None)
    if starter is not None:
        await starter()
    yield
    closer = getattr(provider, "aclose", None)
    if closer is not None:
        await closer()


def create_app() -> FastAPI:
    settings = load_settings()

    app = FastAPI(
        title="FactoryMind AI Engine",
        description=DESCRIPTION,
        version=__version__,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.anomaly_rules = load_anomaly_rules()
    app.state.assess_rules = load_assess_rules()
    app.state.data_provider = create_data_provider(settings, app.state.assess_rules)
    app.state.ai_provider = create_ai_provider(settings)
    app.state.action_store = ActionStore()

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        # Liveness only: it must not touch a provider, or a slow supOS gateway
        # would get the container killed by the orchestrator instead of surfaced
        # as a data-source fault. `/api/health` reports the configured providers.
        return {"status": "ok"}

    app.include_router(industrial.router)
    app.include_router(anomalies.router)
    app.include_router(analysis.router)
    app.include_router(actions.router)
    return app


app = create_app()