"""Versioned P18-D2 cron runner. Scheduling remains external."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests


class ConfigurationError(ValueError):
    pass


class TechnicalError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    base_url: str
    username: str
    password: str
    limit: int
    timeout: tuple[float, float]


def _integer(name: str, default: int, minimum: int = 1, maximum: int = 500) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigurationError(name) from None
    if value < minimum or value > maximum:
        raise ConfigurationError(name)
    return value


def _seconds(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ConfigurationError(name) from None
    if value <= 0:
        raise ConfigurationError(name)
    return value


def load_config() -> Config:
    base_url = (os.getenv("FOLLOWUP_AUTOMATION_BASE_URL") or "").strip().rstrip("/")
    username = os.getenv("ADMIN_USER") or ""
    password = os.getenv("ADMIN_PASS") or ""
    parsed = urlparse(base_url)
    if not base_url or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigurationError("FOLLOWUP_AUTOMATION_BASE_URL")
    if not username or not password:
        raise ConfigurationError("ADMIN credentials")
    return Config(
        base_url,
        username,
        password,
        _integer("FOLLOWUP_TEMPORAL_SCAN_LIMIT", 100),
        (
            _seconds("FOLLOWUP_CONNECT_TIMEOUT_SECONDS", 5),
            _seconds("FOLLOWUP_READ_TIMEOUT_SECONDS", 60),
        ),
    )


def _log(status: str, duration_ms: int, reason: str | None = None, counts: dict | None = None) -> None:
    fields = [f"status={status}"]
    for key in ("requested_limit", "processed", "escalated", "skipped", "failed"):
        if counts and key in counts:
            fields.append(f"{key}={counts[key]}")
    fields.append(f"duration_ms={duration_ms}")
    if reason:
        fields.append(f"reason={reason}")
    print(" ".join(fields), flush=True)


def _application_failure(data: dict) -> bool:
    return data.get("status") not in ("completed", "success") or bool(data.get("failed", 0))


def run_once(config: Config) -> dict:
    started = time.monotonic()
    try:
        response = requests.post(
            f"{config.base_url}/api/followup/scan-temporal",
            json={"limit": config.limit},
            auth=(config.username, config.password),
            timeout=config.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("status"), str):
            raise TechnicalError("invalid_json")
        for key in ("requested_limit", "processed", "escalated", "skipped", "failed"):
            if type(data.get(key)) is not int or data[key] < 0:
                raise TechnicalError("invalid_json")
    except requests.Timeout:
        _log("failed", int((time.monotonic() - started) * 1000), "timeout")
        raise TechnicalError("timeout") from None
    except requests.RequestException:
        _log("failed", int((time.monotonic() - started) * 1000), "http_or_network")
        raise TechnicalError("http_or_network") from None
    except (ValueError, TechnicalError):
        _log("failed", int((time.monotonic() - started) * 1000), "invalid_json")
        raise TechnicalError("invalid_json") from None

    _log(data["status"], int((time.monotonic() - started) * 1000), counts=data)
    return data


def main() -> int:
    try:
        config = load_config()
    except ConfigurationError:
        _log("failed", 0, "configuration")
        return 1

    try:
        result = run_once(config)
    except TechnicalError:
        return 1
    if _application_failure(result):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
