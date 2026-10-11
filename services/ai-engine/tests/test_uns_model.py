"""Contract tests for the assembly-line quality UNS model (spec §22).

`config/uns_model.json` is the single source of truth for what gets seeded into
open supOS and what the adapter later queries. A drift between the model and the
adapter is silent on both sides — seeding succeeds, queries return empty — so
these tests pin the seam in place:

* every alias is legal for the supOS backend (`^[A-Za-z_$][A-Za-z0-9_$]*$`);
* parents precede children (batch create rejects the reverse order);
* each station carries exactly the five metric files, and their aliases equal
  what `SupOsProvider` will compute (template + slug);
* the event contract (topic + payload keys) matches the MQTT ingestor.
"""

from __future__ import annotations

import re

import pytest
from app.domain.models import IndustrialEventType, MetricKey, RiskLevel
from app.providers.supos import DEFAULT_METRIC_ALIAS_TEMPLATE, METRIC_ALIAS_SLUGS
from scripts import seed_uns

# The backend rejects any alias failing `normalizeCreateFileCommand`'s pattern.
ALIAS_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

EXPECTED_METRICS = 5


@pytest.fixture(scope="module")
def model() -> dict:
    return seed_uns.load_model()


@pytest.fixture(scope="module")
def payload(model: dict) -> list[dict]:
    return seed_uns.build_payload(model)


def test_model_template_matches_provider(model: dict) -> None:
    assert model["metricAliasTemplate"] == DEFAULT_METRIC_ALIAS_TEMPLATE


def test_every_alias_is_backend_legal(payload: list[dict]) -> None:
    illegal = [node["alias"] for node in payload if not ALIAS_RE.match(node["alias"])]
    assert illegal == []


def test_aliases_are_unique(payload: list[dict]) -> None:
    aliases = [node["alias"] for node in payload]
    assert len(aliases) == len(set(aliases))


def test_parents_precede_children(payload: list[dict]) -> None:
    known: set[str] = set()
    for node in payload:
        parent = node.get("parentAlias") or ""
        assert parent == "" or parent in known, f"{node['alias']} precedes its parent"
        known.add(node["alias"])


def test_station_metric_slugs_match_provider(model: dict) -> None:
    model_slugs = {key: metric["slug"] for key, metric in model["metrics"].items()}
    for metric in MetricKey:
        assert model_slugs[metric.value] == METRIC_ALIAS_SLUGS[metric]


def test_each_station_has_the_five_metric_files(model: dict, payload: list[dict]) -> None:
    by_alias = {node["alias"]: node for node in payload}
    template = model["metricAliasTemplate"]
    for line in model["lines"]:
        for station in line["stations"]:
            station_alias = station["alias"]
            files = [
                node
                for node in payload
                if node.get("parentAlias") == station_alias
                and node["pathType"] == seed_uns.PATH_TYPE_FILE
            ]
            assert len(files) == EXPECTED_METRICS, station_alias
            for metric in MetricKey:
                expected = seed_uns.metric_alias(
                    template, station_alias, METRIC_ALIAS_SLUGS[metric]
                )
                assert expected in by_alias, f"{station_alias} missing {expected}"


def test_station_folders_are_tagged_with_a_known_kind(payload: list[dict]) -> None:
    known_kinds = {"factory", "line", "station", "equipment", "process"}
    for node in payload:
        if node["pathType"] != seed_uns.PATH_TYPE_DIR:
            continue
        kind = node.get("extendProperties", {}).get(seed_uns.KIND_KEY)
        assert kind in known_kinds, node["alias"]


def test_event_topics_cover_every_station(model: dict) -> None:
    topics = seed_uns.event_topics(model)
    station_count = sum(len(line["stations"]) for line in model["lines"])
    assert len(topics) == station_count
    assert len(set(topics)) == len(topics)
    subscribe = model["events"]["subscribeTopic"]
    prefix = subscribe.rstrip("#").rstrip("/")
    assert all(topic.startswith(prefix) for topic in topics)


def test_event_payload_vocabulary_matches_domain(model: dict) -> None:
    payload = model["events"]["payload"]
    for key in ("stationAlias", "type", "severity", "message", "timestamp"):
        assert key in payload
    example = model["events"]["example"]
    assert example["type"] in {member.value for member in IndustrialEventType}
    assert example["severity"] in {member.value for member in RiskLevel}
