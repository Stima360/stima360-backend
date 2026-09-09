"""Versioned FLOW P2B cron runner. Infrastructure scheduling is external."""

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
    recovery_limit: int
    scan_limit: int
    timeout: tuple[float, float]


def _integer(name, default, minimum=1, maximum=500):
    raw=os.getenv(name,str(default))
    try: value=int(raw)
    except (TypeError,ValueError): raise ConfigurationError(name) from None
    if value<minimum or value>maximum: raise ConfigurationError(name)
    return value


def _seconds(name, default):
    raw=os.getenv(name,str(default))
    try: value=float(raw)
    except (TypeError,ValueError): raise ConfigurationError(name) from None
    if value<=0: raise ConfigurationError(name)
    return value


def load_config():
    base_url=(os.getenv('FLOW_AUTOMATION_BASE_URL') or '').strip().rstrip('/')
    username=os.getenv('ADMIN_USER') or ''
    password=os.getenv('ADMIN_PASS') or ''
    parsed=urlparse(base_url)
    if not base_url or parsed.scheme not in ('http','https') or not parsed.netloc:
        raise ConfigurationError('FLOW_AUTOMATION_BASE_URL')
    if not username or not password:
        raise ConfigurationError('ADMIN credentials')
    return Config(
        base_url=base_url,
        username=username,
        password=password,
        recovery_limit=_integer('FLOW_RECOVERY_LIMIT',100),
        scan_limit=_integer('FLOW_SCAN_LIMIT',100),
        timeout=(_seconds('FLOW_CONNECT_TIMEOUT_SECONDS',5),_seconds('FLOW_READ_TIMEOUT_SECONDS',60)),
    )


def _log(phase,status,duration_ms,reason=None,counts=None):
    fields=[f"phase={phase}",f"status={status}"]
    for key in ('requested_limit','processed','ignored','failed','busy','successes','failures','skips'):
        if counts and key in counts: fields.append(f"{key}={counts[key]}")
    fields.append(f"duration_ms={duration_ms}")
    if reason: fields.append(f"reason={reason}")
    print(' '.join(fields),flush=True)


def _post(config,phase,path,payload):
    started=time.monotonic()
    try:
        response=requests.post(
            f"{config.base_url}{path}",
            json=payload,
            auth=(config.username,config.password),
            timeout=config.timeout,
        )
        response.raise_for_status()
        data=response.json()
        if not isinstance(data,dict) or not isinstance(data.get('status'),str):
            raise TechnicalError('invalid_json')
        required=(
            ('requested_limit','processed','ignored','failed','busy')
            if phase=='recovery'
            else ('requested_limit','processed','successes','failures','skips')
        )
        if any(type(data.get(key)) is not int or data[key]<0 for key in required):
            raise TechnicalError('invalid_json')
    except requests.Timeout:
        _log(phase,'failed',int((time.monotonic()-started)*1000),'timeout')
        raise TechnicalError('timeout') from None
    except requests.RequestException:
        _log(phase,'failed',int((time.monotonic()-started)*1000),'http_or_network')
        raise TechnicalError('http_or_network') from None
    except (ValueError,TechnicalError):
        _log(phase,'failed',int((time.monotonic()-started)*1000),'invalid_json')
        raise TechnicalError('invalid_json') from None
    _log(phase,data['status'],int((time.monotonic()-started)*1000),counts=data)
    return data


def _application_failure(data):
    return data.get('status') not in ('completed','success') or bool(data.get('failed',0)) or bool(data.get('failures',0)) or bool(data.get('busy',0))


# ---------------------------------------------------------------------------
# P26-6C: two runners, because there are two deployments.
#
# `main()` is unchanged and still drives the HTTP API. What changed underneath
# it is that `/api/flow/scan` and `/api/flow/events/recover` are no longer
# global: since P26-6C each resolves an agency server-side and runs ONE
# bounded cycle. So this runner is no longer a cross-tenant operation - it is
# one tenant's cycle, the tenant the compatibility context resolves.
#
# `main_all_agencies()` is the server-only path for a platform-wide sweep. It
# runs in-process, enumerates active agencies through the certified
# `list_active_agency_ids()`, and calls the same bounded cycle once per tenant.
# It is deliberately NOT an HTTP route: a user-facing endpoint that swept every
# agency is precisely what P26-6C removed, and adding one back to serve a cron
# would undo the slice.
#
# Which one to schedule is a deployment decision. While a single agency is
# active the two are equivalent; onboarding a second makes the difference real.
# ---------------------------------------------------------------------------

def main_all_agencies(limit=None):
    """Server-only: one bounded FLOW cycle per active agency, in-process.

    No HTTP, no credentials, no global query. Each cycle carries its own tenant
    predicate, so one agency's failing rule cannot end another agency's scan.
    """
    from flow import service as flow_service
    from flow.schemas import ScanRequest

    scan_limit = limit if limit is not None else _integer('FLOW_SCAN_LIMIT', 100)
    started = time.monotonic()
    result = flow_service.scan_for_all_agencies(
        ScanRequest(simulation=False, limit=scan_limit)
    )
    _log(
        'scan_all_agencies',
        'completed' if not result['failures'] else 'partial_failure',
        int((time.monotonic() - started) * 1000),
        counts=result,
    )
    return 0 if not result['failures'] else 2


def main():
    try:
        config=load_config()
    except ConfigurationError:
        _log('config','failed',0,'configuration')
        return 1
    try:
        recovery=_post(config,'recovery','/api/flow/events/recover',{'limit':config.recovery_limit})
    except TechnicalError:
        return 1
    application_problem=_application_failure(recovery)
    try:
        # One agency's bounded cycle - see the note above main_all_agencies.
        scan=_post(config,'scan','/api/flow/scan',{'simulation':False,'limit':config.scan_limit})
    except TechnicalError:
        return 1
    saturation=scan.get('processed')==scan.get('requested_limit')
    if saturation:
        _log('runner','partial_failure',0,'possible_saturation')
    if application_problem or _application_failure(scan) or saturation:
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
