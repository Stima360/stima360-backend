from pathlib import Path


VIEW = Path("static/os_shell/assets/views/contatto-dettaglio.js")
CSS = Path("static/os_shell/assets/app.css")


def _source():
    return VIEW.read_text(encoding="utf-8")


def test_p19b_uses_sell_leads_and_backend_score_endpoint():
    source = _source()

    assert "lead.pipeline === 'sell'" in source
    assert "/api/seller-intent/leads/${lead.id}/score" in source
    assert "Promise.allSettled" in source
    assert "sellLeads.map" in source
    assert "items.map" in source


def test_p19b_handles_multiple_zero_and_failed_sell_leads():
    source = _source()

    assert "renderSellerIntentUnavailableCard" in source
    assert "Seller Intent non disponibile." in source
    assert "Nessuna opportunità venditore collegata." in source

    assert "sellerIntentByLead" in source
    assert "sellerIntentPromise" in source

    assert "mount.isConnected" in source
    assert "mount.dataset.requestId" in source


def test_p19b_keeps_factors_and_operational_flags_separate_and_escaped():
    source = _source()

    assert "scoreData.factors" in source
    assert "renderSellerIntentFactor" in source

    assert "scoreData.operational_flags" in source
    assert "seller-intent-flags" in source
    assert "seller-intent-flag" in source

    assert "escapeHtml(scoreData.score)" in source
    assert "escapeHtml(lead.id)" in source
    assert "escapeHtml(factor && factor.label" in source
    assert "escapeHtml(flag.label || flag.code" in source


def test_p19b_does_not_duplicate_backend_scoring_formula():
    source = _source()

    forbidden_backend_factor_codes = (
        "stage_new",
        "stima_completata",
        "recent_activity_7d",
    )

    for token in forbidden_backend_factor_codes:
        assert token not in source

    assert "scoreData.score" in source
    assert "scoreData.band" in source
    assert "scoreData.factors" in source
    assert "scoreData.operational_flags" in source


def test_p19b_css_is_scoped_to_seller_intelligence():
    css = CSS.read_text(encoding="utf-8")

    assert ".seller-intelligence-section" in css
    assert ".seller-intent-grid" in css
    assert ".seller-intent-card" in css
    assert ".seller-intent-flags" in css
