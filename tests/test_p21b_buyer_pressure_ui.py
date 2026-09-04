from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "static" / "os_shell" / "assets" / "components" / "buyer-pressure.js"
VIEW = ROOT / "static" / "os_shell" / "assets" / "views" / "contatto-dettaglio.js"
CSS = ROOT / "static" / "os_shell" / "assets" / "app.css"


def _run_module(script: str) -> None:
    version = subprocess.run(
        ["node", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    command = ["node"]
    if int(version.removeprefix("v").split(".", 1)[0]) < 24:
        command.append("--experimental-default-type=module")
    result = subprocess.run(
        [*command, "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_component_and_overview_delegation_exist_in_the_approved_position():
    component = COMPONENT.read_text(encoding="utf-8")
    view = VIEW.read_text(encoding="utf-8")

    assert "export function createBuyerPressureCache" in component
    assert "export async function loadBuyerPressure" in component
    assert "export async function refreshBuyerPressure" in component
    assert "export async function hydrateBuyerPressure" in component
    assert "from '../components/buyer-pressure.js'" in view
    overview = view[view.index("function renderPanoramica"):]
    assert overview.index("data-seller-intent-mount") < overview.index("data-buyer-pressure-mount")
    assert overview.index("data-buyer-pressure-mount") < overview.index("Relazioni operative")
    assert "/api/property-watch/" not in view
    assert "compatible_volume" not in view


def test_component_resolves_known_stime_ids_and_isolates_failures():
    _run_module(
        """
        import assert from 'node:assert/strict';
        import {
          createBuyerPressureCache,
          loadBuyerPressure,
          renderBuyerPressureSection,
        } from './static/os_shell/assets/components/buyer-pressure.js';

        const watchCalls = [];
        const state = await loadBuyerPressure(
          [{ id: 2 }, { id: 1 }, { id: 3 }],
          createBuyerPressureCache(),
          async () => ({
            leads: [
              { id: 2, estimations: [{ stima_id: 12 }, { stima_id: 4 }] },
              { id: 1, estimations: [{ stima_id: 4 }, { stima_id: 'invalid' }] },
            ],
            failedCount: 1,
          }),
          async (path) => {
            watchCalls.push(path);
            if (path.endsWith('/12')) throw new Error('not found');
            return { buyer_pressure_metrics: null, buyer_pressure_insight: null };
          },
        );
        assert.deepEqual(state.stimaIds, [4, 12]);
        assert.deepEqual(watchCalls, [
          '/api/property-watch/stime/4',
          '/api/property-watch/stime/12',
        ]);
        const html = renderBuyerPressureSection(state);
        assert.ok(html.includes('Non è stato possibile verificare tutte le stime collegate.'));
        assert.ok(html.includes('Domanda buyer non ancora calcolata.'));
        assert.ok(html.includes('Domanda buyer non disponibile per la stima #12.'));
        assert.equal(html.includes('Nessuna stima collegata per il calcolo della domanda buyer.'), false);

        const none = await loadBuyerPressure(
          [{ id: 1 }],
          createBuyerPressureCache(),
          async () => ({ leads: [{ id: 1, estimations: [] }], failedCount: 0 }),
          async () => { throw new Error('must not load watch'); },
        );
        assert.ok(renderBuyerPressureSection(none).includes('Nessuna stima collegata per il calcolo della domanda buyer.'));

        const unverifiable = await loadBuyerPressure(
          [{ id: 1 }],
          createBuyerPressureCache(),
          async () => ({ leads: [], failedCount: 1 }),
          async () => { throw new Error('must not load watch'); },
        );
        assert.ok(renderBuyerPressureSection(unverifiable).includes('Non è stato possibile verificare le stime collegate.'));
        """
    )


def test_component_renders_exact_raw_metrics_safe_content_and_states():
    _run_module(
        """
        import assert from 'node:assert/strict';
        import { renderBuyerPressureSection } from './static/os_shell/assets/components/buyer-pressure.js';

        const valid = {
          leadWarning: null,
          stimaIds: [4],
          cards: new Map([[4, {
            status: 'ready',
            state: {
              buyer_pressure_metrics: {
                evaluated_buyers: 18,
                compatible_buyers: 13,
                highly_compatible_buyers: 5,
                recent_compatible_buyers_30d: 7,
                average_match_score: 72.35,
                maximum_match_score: 91.4,
                average_budget: 245000,
                observed_at: '2026-09-03T12:00:00Z',
              },
              buyer_pressure_insight: {
                score: 87,
                band_label: 'Domanda alta',
                headline: 'DOMANDA ALTA — 87/100',
                message: '<img src=x onerror=alert(1)>',
                disclaimer: 'Indicatore interno',
                factors: [{
                  code: 'compatible_volume',
                  label: '<img src=x onerror=alert(1)>',
                  points: 30,
                  max_points: 30,
                }],
              },
            },
          }]]),
        };
        const html = renderBuyerPressureSection(valid);
        for (const label of [
          'Buyer valutati',
          'Buyer compatibili',
          'Buyer altamente compatibili',
          'Buyer compatibili recenti (30 giorni)',
          'Score medio MATCH',
          'Score massimo MATCH',
          'Budget medio',
          'Aggiorna domanda buyer',
        ]) assert.ok(html.includes(label), label);
        assert.ok(html.includes('72,35/100'));
        assert.ok(html.includes('91,40/100'));
        assert.ok(html.includes('245.000,00'));
        assert.equal(html.includes('<img src=x onerror=alert(1)>'), false);
        assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));

        for (const [status, copy] of [
          ['refreshing', 'Calcolo domanda buyer in caricamento…'],
          ['baseline_unavailable', 'Dati della stima insufficienti per calcolare la domanda buyer.'],
          ['failed', 'Impossibile aggiornare la domanda buyer. Riprova.'],
        ]) {
          const state = { leadWarning: null, stimaIds: [4], cards: new Map([[4, { status }]]) };
          assert.ok(renderBuyerPressureSection(state).includes(copy), status);
        }
        """
    )


def test_manual_refresh_is_bodyless_deduplicated_and_target_scoped():
    _run_module(
        """
        import assert from 'node:assert/strict';
        import {
          createBuyerPressureCache,
          refreshBuyerPressure,
          renderBuyerPressureSection,
        } from './static/os_shell/assets/components/buyer-pressure.js';

        const cache = createBuyerPressureCache();
        cache.watchResults.set(2, { status: 'ready', state: { buyer_pressure_metrics: null } });
        const postCalls = [];
        const watchCalls = [];
        let completePost;
        const post = (...args) => {
          postCalls.push(args);
          return new Promise((resolve) => { completePost = resolve; });
        };
        const getWatch = async (path) => {
          watchCalls.push(path);
          return { buyer_pressure_metrics: null, buyer_pressure_insight: null };
        };
        const first = refreshBuyerPressure(1, cache, post, getWatch);
        const second = refreshBuyerPressure(1, cache, post, getWatch);
        assert.equal(postCalls.length, 1);
        assert.deepEqual(postCalls[0], ['/api/property-watch/stime/1/buyer-pressure/refresh']);
        completePost({ status: 'written' });
        const result = await first;
        await second;
        assert.equal(result.status, 'ready');
        assert.deepEqual(watchCalls, ['/api/property-watch/stime/1']);
        assert.ok(renderBuyerPressureSection({
          leadWarning: null,
          stimaIds: [1],
          cards: new Map([[1, result]]),
        }).includes('Domanda buyer non ancora calcolata.'));

        const unavailable = await refreshBuyerPressure(
          1,
          createBuyerPressureCache(),
          async () => ({ status: 'baseline_unavailable' }),
          async () => { throw new Error('must not refetch'); },
        );
        assert.equal(unavailable.status, 'baseline_unavailable');

        const failed = await refreshBuyerPressure(
          1,
          createBuyerPressureCache(),
          async () => { throw new Error('network'); },
          async () => { throw new Error('must not refetch'); },
        );
        assert.equal(failed.status, 'failed');
        """
    )


def test_manual_refresh_treats_superseded_as_a_successful_targeted_refresh():
    _run_module(
        """
        import assert from 'node:assert/strict';
        import {
          createBuyerPressureCache,
          refreshBuyerPressure,
        } from './static/os_shell/assets/components/buyer-pressure.js';

        const cache = createBuyerPressureCache();
        const otherCard = { status: 'ready', state: { marker: 'unchanged' } };
        cache.watchResults.set(2, otherCard);
        const postCalls = [];
        const watchCalls = [];

        const result = await refreshBuyerPressure(
          1,
          cache,
          (...args) => {
            postCalls.push(args);
            return Promise.resolve({ status: 'superseded' });
          },
          async (path) => {
            watchCalls.push(path);
            return { buyer_pressure_metrics: null, buyer_pressure_insight: null };
          },
        );

        assert.deepEqual(postCalls, [
          ['/api/property-watch/stime/1/buyer-pressure/refresh'],
        ]);
        assert.deepEqual(watchCalls, ['/api/property-watch/stime/1']);
        assert.equal(result.status, 'ready');
        assert.equal(cache.watchResults.get(2), otherCard);
        assert.equal(cache.watchResults.has(1), true);
        """
    )


def test_component_and_css_keep_the_safety_boundary_explicit():
    component = COMPONENT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert "Promise.allSettled" in component
    assert "mount.isConnected" in component
    assert "mount.dataset.buyerPressureRequestId" in component
    assert "escapeHtml(" in component
    assert "localStorage" not in component
    assert "sessionStorage" not in component
    assert "setInterval" not in component
    assert "WebSocket" not in component
    assert "EventSource" not in component
    assert "buyer_pressure_score" not in component
    assert "buyer-pressure-" in css
