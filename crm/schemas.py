from typing import Any

from pydantic import BaseModel


class Contact360Response(BaseModel):
    contact: dict[str, Any]
    roles: list[dict[str, Any]]
    leads: list[dict[str, Any]]
    properties: list[dict[str, Any]]
    buy_requests: list[dict[str, Any]]
    matches: list[dict[str, Any]]
    visits: list[dict[str, Any]]
    activities: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
