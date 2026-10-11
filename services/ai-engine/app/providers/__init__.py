from app.config import AssessRules, Settings
from app.engine.fmea import load_master_fmea
from app.providers.ai import AiProvider, AiProviderError, MockAiProvider
from app.providers.ai_openai import OpenAiCompatibleAiProvider
from app.providers.base import IndustrialDataProvider
from app.providers.mock import MockIndustrialDataProvider
from app.providers.supos import (
    HttpSupOsClient,
    SupOsClient,
    SupOsEndpoints,
    SupOsError,
    SupOsEventRecord,
    SupOsNode,
    SupOsProvider,
    SupOsSample,
    SupOsUnavailableError,
)
from app.providers.supos_mqtt import (
    EventBuffer,
    MqttBackedSupOsClient,
    MqttEventIngestor,
    MqttEventSettings,
)

__all__ = [
    "AiProvider",
    "AiProviderError",
    "EventBuffer",
    "HttpSupOsClient",
    "IndustrialDataProvider",
    "MockAiProvider",
    "MockIndustrialDataProvider",
    "MqttBackedSupOsClient",
    "MqttEventIngestor",
    "MqttEventSettings",
    "OpenAiCompatibleAiProvider",
    "SupOsClient",
    "SupOsEndpoints",
    "SupOsError",
    "SupOsEventRecord",
    "SupOsNode",
    "SupOsProvider",
    "SupOsSample",
    "SupOsUnavailableError",
    "create_ai_provider",
    "create_data_provider",
]


def _supos_event_client(
    settings: Settings, http_client: HttpSupOsClient
) -> SupOsClient:
    """Pick the event source (spec §22).

    supOS CE exposes no REST event-query endpoint, so MQTT is the only
    supported source. Anything else fails loudly rather than defaulting,
    matching the provider-selection rule below.
    """
    if settings.sup_os_event_source == "mqtt":
        if not settings.sup_os_mqtt_url:
            raise ValueError("SUPOS_EVENT_SOURCE=mqtt requires SUPOS_MQTT_URL to be set.")
        mqtt_settings = MqttEventSettings.from_url(
            settings.sup_os_mqtt_url,
            topic=settings.sup_os_mqtt_topic,
            username=settings.sup_os_mqtt_username,
            password=settings.sup_os_mqtt_password,
        )
        buffer = EventBuffer(maxlen=mqtt_settings.buffer_size)
        return MqttBackedSupOsClient(
            http=http_client,
            ingestor=MqttEventIngestor(mqtt_settings, buffer),
            buffer=buffer,
        )
    if settings.sup_os_event_source == "rest":
        raise ValueError(
            "SUPOS_EVENT_SOURCE=rest is not supported: supOS CE exposes no REST "
            "event-query endpoint. Use SUPOS_EVENT_SOURCE=mqtt with SUPOS_MQTT_URL."
        )
    raise ValueError(
        f"Unknown SUPOS_EVENT_SOURCE: {settings.sup_os_event_source!r} "
        "(expected 'mqtt')."
    )


def create_data_provider(
    settings: Settings, assess_rules: AssessRules
) -> IndustrialDataProvider:
    """Build the provider named by `DATA_PROVIDER`.

    Unknown values fail loudly rather than silently degrading to mock data — a
    demo that quietly serves synthetic numbers while claiming to read the plant
    would be worse than no demo at all (spec §7, §23). `supos` is a real
    integration: it requires a reachable gateway, so a missing `SUPOS_API_URL`
    is a configuration error, not a reason to fall back to the mock.
    """
    if settings.data_provider == "mock":
        return MockIndustrialDataProvider(
            seed=settings.demo_seed,
            fmea_records=load_master_fmea(assess_rules),
        )
    if settings.data_provider == "supos":
        if not settings.sup_os_api_url:
            raise ValueError(
                "DATA_PROVIDER=supos requires SUPOS_API_URL to be set "
                "(e.g. http://<host>:8088)."
            )
        http_client = HttpSupOsClient(
            base_url=settings.sup_os_api_url,
            token=settings.sup_os_api_token,
            timeout=settings.sup_os_timeout_seconds,
            verify_tls=settings.sup_os_verify_tls,
        )
        return SupOsProvider(
            client=_supos_event_client(settings, http_client),
            fmea_records=load_master_fmea(assess_rules),
            uns_root_alias=settings.sup_os_uns_root_alias,
        )
    raise ValueError(f"Unknown DATA_PROVIDER: {settings.data_provider!r}")


def _openai_compatible_provider(settings: Settings) -> AiProvider:
    """Build the real explanation layer (spec §20).

    `openai-compatible` talks to a hosted API and therefore requires a key;
    `local` talks to a self-hosted server (Ollama, vLLM, LM Studio) where a key
    is usually absent. Both require an explicit base URL and model: defaulting
    either would silently send plant data to an endpoint the operator never
    chose.
    """
    if not settings.ai_base_url:
        raise ValueError(
            f"AI_PROVIDER={settings.ai_provider!r} requires AI_BASE_URL to be set."
        )
    if not settings.ai_model:
        raise ValueError(
            f"AI_PROVIDER={settings.ai_provider!r} requires AI_MODEL to be set."
        )
    if settings.ai_provider == "openai-compatible" and not settings.ai_api_key:
        raise ValueError(
            "AI_PROVIDER='openai-compatible' requires AI_API_KEY to be set "
            "(use AI_PROVIDER=local for a self-hosted server that needs no key)."
        )
    return OpenAiCompatibleAiProvider(
        base_url=settings.ai_base_url,
        model=settings.ai_model,
        api_key=settings.ai_api_key,
        timeout=settings.ai_timeout_seconds,
        max_retries=settings.ai_max_retries,
        name=settings.ai_provider,
    )


def create_ai_provider(settings: Settings) -> AiProvider:
    """Build the explanation layer named by `AI_PROVIDER` (spec §20).

    `mock` renders the deterministic result into the exact schema a real model
    must return, so the demo never depends on a live API. `openai-compatible`
    and `local` drive a real model and enforce the citation and schema rules in
    `app.providers.ai_openai`. A configured-but-unimplemented provider fails
    loudly instead of silently falling back to the mock — the same rule the data
    provider follows.
    """
    if settings.ai_provider == "mock":
        return MockAiProvider()
    if settings.ai_provider in {"openai-compatible", "local"}:
        return _openai_compatible_provider(settings)
    raise ValueError(f"Unknown AI_PROVIDER: {settings.ai_provider!r}")