from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_invisible_sale_component_uses_safe_targeted_body_free_contract():
    source = (ROOT / "static/os_shell/assets/components/invisible-sale.js").read_text()
    assert "export async function loadInvisibleSale" in source
    assert "export async function refreshInvisibleSale" in source
    assert "export async function reviewInvisibleSaleCandidate" in source
    assert "textContent" in source
    assert "innerHTML" not in source
    assert "Promise.allSettled" in source
    assert "/invisible-sale/refresh" in source


def test_contact_view_mounts_p22_in_overview():
    source = (ROOT / "static/os_shell/assets/views/contatto-dettaglio.js").read_text()
    assert "Potenziali acquirenti prima della pubblicazione" in source
    assert "mountInvisibleSale" in source


def test_inflight_actions_disable_by_the_same_key_used_for_deduplication():
    source = (ROOT / "static/os_shell/assets/components/invisible-sale.js").read_text()
    assert "function actionKey" in source
    assert "inFlight.has(actionKey(stimaId, 'refresh'))" in source
