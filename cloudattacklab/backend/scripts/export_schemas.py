"""Write the JSON Schema files in /contracts from the Pydantic models.

Run from the repo root after changing a schema:
    python backend/scripts/export_schemas.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas.event import NormalizedEvent  # noqa: E402
from app.schemas.scenario import ScenarioDefinition  # noqa: E402

OUT = ROOT / "contracts"
OUT.mkdir(exist_ok=True)

for name, model in (("event", NormalizedEvent), ("scenario", ScenarioDefinition)):
    target = OUT / f"{name}.schema.json"
    target.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)}")
