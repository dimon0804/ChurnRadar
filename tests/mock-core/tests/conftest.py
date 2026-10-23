import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.main import create_app  # noqa: E402

CONTRACT = REPO / "contracts" / "openapi.yaml"
SAMPLE = REPO / "data" / "sample" / "data.json"
EVENTS_DIR = REPO / "contracts" / "events"
ADMIN = {"X-User-Name": "admin", "X-User-Roles": "admin"}
ANALYST = {"X-User-Name": "analyst", "X-User-Roles": "analyst"}
METRICS_TOKEN = "test-metrics-token"


def make_config(settings_path: Path, **overrides) -> Config:
    cfg = Config(
        snapshot_path=SAMPLE,
        contract_path=CONTRACT,
        settings_path=settings_path,
        nats_enabled=False,
        metrics_token=METRICS_TOKEN,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class RecordingBus:
    """Подменяет публикацию в NATS и запоминает события."""

    def __init__(self, bus):
        self.events = []
        self._publish = bus.publish

    def __call__(self, event_type, payload):
        env = self._publish(event_type, payload)
        self.events.append(env)
        return env


@pytest.fixture
def settings_path(tmp_path) -> Path:
    return tmp_path / "state" / "settings.json"


@pytest.fixture
def client(settings_path):
    app = create_app(make_config(settings_path))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def recorder(client):
    core = client.app.state.core
    rec = RecordingBus(core.bus)
    core.bus.publish = rec
    return rec


@pytest.fixture(scope="session")
def sample_data():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


# --- проверка ответов по контракту ---

_format_checker = FormatChecker()


@_format_checker.checks("date-time", raises=ValueError)
def _is_datetime(value):
    if not isinstance(value, str):
        return True
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return "T" in value


@_format_checker.checks("date", raises=ValueError)
def _is_date(value):
    if not isinstance(value, str):
        return True
    datetime.strptime(value, "%Y-%m-%d")
    return True


@pytest.fixture(scope="session")
def contract():
    spec = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    uri = "urn:churnradar:openapi"
    registry = Registry().with_resource(uri, Resource.from_contents(spec, default_specification=DRAFT202012))
    events = {}
    for path in EVENTS_DIR.glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        events[path.name.replace(".schema.json", "")] = schema

    class Contract:
        def validate(self, pointer: str, instance):
            schema = {"$ref": f"{uri}#{pointer}"}
            Draft202012Validator(schema, registry=registry, format_checker=_format_checker).validate(instance)

        def schema(self, name: str, instance):
            self.validate(f"/components/schemas/{name}", instance)

        def response(self, path: str, method: str, status: str, instance):
            escaped = path.replace("~", "~0").replace("/", "~1")
            pointer = f"/paths/{escaped}/{method}/responses/{status}"
            node = spec["paths"][path][method]["responses"][status]
            if "$ref" in node:
                pointer = node["$ref"].lstrip("#")
            self.validate(f"{pointer}/content/application~1json/schema", instance)

        def event(self, name: str, instance):
            Draft202012Validator(events[name], format_checker=_format_checker).validate(instance)

    return Contract()
