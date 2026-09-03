"""Focused static contracts for the P17-B3 Seller Timeline UI.

The OS shell has no JavaScript test runner. These checks keep the approved
browser contract explicit without requiring a live API or database.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "static" / "os_shell" / "assets" / "views" / "contatto-dettaglio.js"
TIMELINE = ROOT / "static" / "os_shell" / "assets" / "components" / "timeline.js"


def _source(path: Path) -> str:
    assert path.exists(), f"{path.relative_to(ROOT)} mancante"
    return path.read_text(encoding="utf-8")


def _timeline_source() -> str:
    return _source(TIMELINE)


def _run_timeline_module(script: str) -> None:
    version = subprocess.run(
        ["node", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    major_version = int(version.removeprefix("v").split(".", 1)[0])
    command = ["node"]
    if major_version < 24:
        command.append("--experimental-default-type=module")
    command.extend(["--input-type=module", "--eval", script])
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_timeline_tab_is_immediately_after_panoramica_and_separate_from_activities():
    view = _source(VIEW)

    assert "import { loadSellerTimeline, renderSellerTimeline } from '../components/timeline.js';" in view
    assert """const TABS = [
  { key: 'panoramica', label: 'Panoramica' },
  { key: 'timeline', label: 'Timeline' },""" in view
    assert "case 'timeline':" in view
    assert "case 'attivita': contentEl.innerHTML = renderAttivita(data.activities); break;" in view
    assert "data.activities" not in _timeline_source()


def test_timeline_component_uses_only_the_paginated_read_endpoint():
    source = _timeline_source()

    assert "import { apiGet } from '../core/api-client.js';" in source
    assert "const TIMELINE_PAGE_SIZE = 200;" in source
    assert "const MAX_TIMELINE_PAGES = 25;" in source
    assert "`/api/seller-intelligence/timeline?contact_id=${encodeURIComponent(contactId)}&limit=${TIMELINE_PAGE_SIZE}&offset=${offset}`" in source
    assert "for (let page = 0; page < MAX_TIMELINE_PAGES; page += 1)" in source
    assert "const offset = page * TIMELINE_PAGE_SIZE;" in source
    assert "if (pageItems.length < TIMELINE_PAGE_SIZE)" in source
    assert "apiPost" not in source
    assert "apiPatch" not in source
    assert "apiDelete" not in source
    assert "/api/core/activities" not in source


def test_timeline_paginates_sorts_caches_and_renders_all_required_states_safely():
    _timeline_source()
    _run_timeline_module(
        """
        import assert from 'node:assert/strict';
        import { loadSellerTimeline, renderSellerTimeline } from './static/os_shell/assets/components/timeline.js';

        const firstPage = Array.from({ length: 200 }, (_, index) => ({
          id: index + 1,
          event_type: 'stima_richiesta',
          event_source: 'stima360_it',
          occurred_at: '2026-09-01T10:00:00Z',
          payload: {},
        }));
        firstPage[0] = {
          id: 4,
          event_type: 'stima_richiesta',
          event_source: 'stima360_it',
          occurred_at: '2026-09-03T10:00:00Z',
          payload: { comune: 'Teramo', tipologia: 'Appartamento', mq: 90 },
        };
        firstPage[1] = {
          id: 3,
          event_type: 'stima_completata',
          event_source: 'stima360_it',
          occurred_at: '2026-09-02T10:00:00Z',
          payload: { price_exact: 180000, eur_mq_finale: 2000, base_mq: 1500 },
        };
        const calls = [];
        const cache = {};
        const getTimeline = async (path) => {
          calls.push(path);
          return {
            items: calls.length === 1
              ? firstPage
              : [{ id: 5, event_type: 'email_stima_inviata', event_source: 'stima360_it', occurred_at: '2026-09-03T10:00:00Z', payload: { pdf_url: '/report.pdf' }, stima_id: 99, lead_id: 22, property_id: 11 }],
          };
        };

        const firstLoad = loadSellerTimeline(42, cache, getTimeline);
        const duplicateLoad = loadSellerTimeline(42, cache, getTimeline);
        assert.strictEqual(firstLoad, duplicateLoad);
        const state = await firstLoad;
        assert.deepEqual(calls, [
          '/api/seller-intelligence/timeline?contact_id=42&limit=200&offset=0',
          '/api/seller-intelligence/timeline?contact_id=42&limit=200&offset=200',
        ]);
        assert.deepEqual(state.items.slice(0, 3).map((event) => event.id), [5, 4, 3]);
        await loadSellerTimeline(42, cache, getTimeline);
        assert.equal(calls.length, 2);

        const capCalls = [];
        const capped = await loadSellerTimeline(7, {}, async (path) => {
          capCalls.push(path);
          return { items: Array.from({ length: 200 }, (_, index) => ({ id: index + 1, occurred_at: '2026-09-01T10:00:00Z' })) };
        });
        assert.equal(capCalls.length, 25);
        assert.equal(capCalls[24], '/api/seller-intelligence/timeline?contact_id=7&limit=200&offset=4800');
        assert.equal(capped.items.length, 5000);
        assert.equal(capped.truncated, true);

        const knownHtml = renderSellerTimeline(state);
        for (const label of ['Stima richiesta', 'Stima completata', 'Email stima inviata', 'Stima360.it', 'Comune', 'Tipologia', 'Superficie', 'Prezzo', 'EUR/mq finale', 'Base EUR/mq', 'PDF', 'Lead #22', 'Stima #99', 'Immobile #11']) {
          assert.ok(knownHtml.includes(label), label);
        }
        const xssHtml = renderSellerTimeline({
          status: 'ready',
          items: [{
            id: '<img src=x onerror=alert(1)>',
            event_type: '<img src=x onerror=alert(1)>',
            event_source: '<img src=x onerror=alert(1)>',
            occurred_at: '<img src=x onerror=alert(1)>',
            payload: { future_key: '<img src=x onerror=alert(1)>', idempotency_key: 'do-not-show' },
            lead_id: '<img src=x onerror=alert(1)>',
          }],
        });
        assert.equal(xssHtml.includes('<img src=x onerror=alert(1)>'), false);
        assert.ok(xssHtml.includes('&lt;img src=x onerror=alert(1)&gt;'));
        assert.equal(xssHtml.includes('do-not-show'), false);

        assert.ok(renderSellerTimeline({ status: 'loading' }).includes('Caricamento timeline'));
        assert.ok(renderSellerTimeline({ status: 'ready', items: [], truncated: false }).includes('Nessun evento Seller Intelligence disponibile'));
        assert.ok(renderSellerTimeline({ status: 'error', message: '<img src=x onerror=alert(1)>' }).includes('&lt;img src=x onerror=alert(1)&gt;'));
        assert.ok(renderSellerTimeline(capped).includes('La timeline potrebbe essere incompleta'));
        const failed = await loadSellerTimeline(9, {}, async () => { throw new Error('non disponibile'); });
        assert.equal(failed.status, 'error');
        """
    )


def test_timeline_component_renders_loading_empty_error_and_truncation_states():
    source = _timeline_source()

    assert "truncated = false;" in source
    assert "if (page === MAX_TIMELINE_PAGES - 1) {" in source
    assert "truncated = true;" in source
    assert "if (cache.timelineResult)" in source
    assert "if (cache.timelinePromise)" in source
    assert "const loadPromise = fetchTimelinePages(contactId, getTimeline)" in source
    assert "cache.timelinePromise = loadPromise;" in source
    assert "cache.timelineResult = state;" in source
    assert "cache.timelinePromise = null;" in source
    assert "key !== 'idempotency_key'" in source
    assert "<a " not in source
    assert "status: 'loading'" in source
    assert "Caricamento timeline…" in source
    assert "Nessun evento Seller Intelligence disponibile per questo contatto." in source
    assert "Errore nel caricamento della timeline:" in source
    assert "state.truncated" in source


def test_seller_intent_contract_remains_unchanged():
    view = _source(VIEW)

    for fragment in (
        "lead.pipeline === 'sell'",
        "/api/seller-intent/leads/${lead.id}/score",
        "Promise.allSettled",
        "sellerIntentByLead",
        "sellerIntentPromise",
        "renderSellerIntentFactor",
        "scoreData.operational_flags",
        "seller-intent-flags",
        "scoreData.score",
        "scoreData.band",
    ):
        assert fragment in view
