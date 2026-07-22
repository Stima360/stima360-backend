from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import hashlib, json

@dataclass(frozen=True)
class RuleDefinition:
    code: str
    version: int
    name: str
    description: str
    event_type: str
    entity_type: str
    default_parameters: dict[str, Any]
    allowed_parameters: dict[str, dict[str, Any]]
    action_type: str

    def parameters_hash(self, parameters: dict[str, Any]) -> str:
        raw = json.dumps(parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def validate_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        unknown = set(parameters) - set(self.allowed_parameters)
        if unknown:
            raise ValueError(f"unsupported parameters: {', '.join(sorted(unknown))}")
        merged = {**self.default_parameters, **parameters}
        for key, spec in self.allowed_parameters.items():
            value = merged.get(key)
            if spec.get("type") == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{key} must be an integer")
                if "min" in spec and value < spec["min"]: raise ValueError(f"{key} below minimum")
                if "max" in spec and value > spec["max"]: raise ValueError(f"{key} above maximum")
            elif spec.get("type") == "enum":
                if value not in spec["values"]: raise ValueError(f"invalid {key}")
            elif spec.get("type") == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool): raise ValueError(f"{key} must be numeric")
                if "min" in spec and value < spec["min"]: raise ValueError(f"{key} below minimum")
                if "max" in spec and value > spec["max"]: raise ValueError(f"{key} above maximum")
        return merged
