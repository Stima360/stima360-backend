"""Inventario APPROVATO delle migrazioni scoperte dal runner (`scripts/p26_migrate.py`).

BLOCCO 1 PRE-COLLAUDO. Molte sentinelle storiche fissavano "l'ultima
migrazione" a un punto del passato (la 086, la 095...) e si nominavano a mano
in decine di file: dalla 096 alla 125 nessuno le aveva aggiornate e fallivano
tutte, quindi non proteggevano piu' niente. Lo scopo resta lo stesso - una
migrazione nuova e' un atto deliberato - ma in UN posto: questa tupla. Una
migrazione aggiunta, tolta, rinominata o fuori ordine fa fallire ogni
sentinella che chiama `approved_series()`, finche' non si aggiorna QUI,
dichiarandolo nella revisione.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

APPROVED_VERSIONS = (
    "026_p26_baseline",
    "027_p26_agency_identity",
    "028_p26_core_agency_columns",
    "029_p26_core_agency_backfill",
    "030_p26_core_agency_enforce",
    "031_p26_stima_agency_columns",
    "032_p26_stima_agency_backfill",
    "033_p26_stima_agency_enforce",
    "034_p26_property_agency_columns",
    "035_p26_property_agency_backfill",
    "036_p26_property_agency_enforce",
    "037_p26_buy_agency_columns",
    "038_p26_buy_agency_backfill",
    "039_p26_buy_agency_enforce",
    "040_p26_match_agency_enforce",
    "041_p26_match_trigger_record_fix",
    "042_p26_sales_proposals_agency_enforce",
    "043_p26_seller_engine_agency_columns",
    "044_p26_seller_engine_agency_backfill",
    "045_p26_seller_engine_agency_enforce",
    "046_p26_intelligence_agency_columns",
    "047_p26_intelligence_agency_backfill",
    "048_p26_intelligence_agency_enforce",
    "049_p26_stima_dettagliata_agency_columns",
    "050_p26_stima_dettagliata_agency_backfill",
    "051_p26_stima_dettagliata_agency_enforce",
    "052_p26_flow_agency_columns",
    "053_p26_flow_agency_backfill",
    "054_p26_flow_agency_enforce",
    "055_p26_flow_agency_immutability",
    "056_p26_stime_gestionale_columns",
    "057_p27_platform_audit_log",
    "058_p27_network_territories",
    "059_p27_territory_aliases",
    "060_p28_acting_agency_context",
    "061_p29_consent_notices",
    "062_p29_consent_events",
    "063_p29_contacts_consent_projection",
    "064_p29_communication_foundation",
    "065_p29_service_lifecycle_parent",
    "066_lmc1_owner_stima_access",
    "067_lmc1b_owner_login_reason",
    "068_lmc10_owner_home_overrides",
    "069_lmc12_owner_home_notifications",
    "070_lmc15_acquisition_bridge",
    "071_p29_3_journey_automation",
    "072_a30_1_appointments",
    "073_a30_2p_lmc15_facade",
    "074_a30_9a_calendar_sync",
    "075_a30_10_calendar_inbound",
    "076_a30_11_working_hours",
    "077_a30_12_public_booking",
    "078_a31_2_buyer_visits_projection",
    "079_a32_1_appointment_reminders",
    "080_crm_ops_2_property_form",
    "081_crm_ops_3_acquisitions",
    "082_crm_ops_4_property_interactions",
    "083_censimento_1_buildings_units",
    "084_delete_arch_1a_mistakes",
    "085_delete_arch_2b1_property_trash",
    "086_delete_arch_2b2_property_trash_guards",
    "087_catalogo_canonico_1_site_attributes",
    "088_catalogo_canonico_1b_site_inbox",
    "089_pertinenze_1_unit_nature",
    "090_cestino_contatti_1_contact_trash",
    "091_cestino_edifici_1_building_trash",
    "092_cestino_richieste_1_buy_request_trash",
    "093_stima_private_pdf",
    "094_public_submission_receipts",
    "095_site_import_ledger",
    "096_site_import_baseline",
    "097_tappa6_request_followup",
    "098_voice_commands",
    "099_voice_undo",
    "100_censimento_2_composition",
    "101_eliminazione_edificio_1",
    "102_territorio_4_position_verification",
    "103_zona_3_property_position_verification",
    "104_agenzie_c2_territorial_change_requests",
    "105_adm_d15_no_delete_admin_entities",
    "106_sale_appointments",
    "107_visita_immobile_incarico",
    "108_followup_contact_journey",
    "109_consent_channels",
    "110_consent_capture",
    "111_voice_mandate_intents",
    "112_stime_consenso_marketing_prova",
    "113_operator_logical_delete",
    "114_territory_logical_delete",
    "115_agency_logos",
    "116_property_valuations",
    "117_property_valuation_pdfs",
    "118_whatsapp_inbound",
    "119_whatsapp_auto_reply_simulation",
    "120_property_visit_booking",
    "121_visit_sheets",
    "122_signature_requests",
    "123_whatsapp_auto_reply_send",
    "124_do_not_contact_and_link_revocation",
    "125_signature_provider_readiness",
    "126_whatsapp_inbound_reads",
    "127_appointment_confirmations",
    # SENTINELLA AGGIORNATA DALL'INTEGRAZIONE DOCUMENSO: la 128 (firmatari delle richieste di firma; additiva).
    "128_signature_request_signers",
    # SENTINELLA AGGIORNATA DALLA FIRMA OTP: la 129 (sfide OTP e registro probatorio; additiva).
    "129_signature_otp",
    # SENTINELLA AGGIORNATA DA WHATSAPP DUE CASELLE: la 130 (caselle WhatsApp; additiva, chiave per casella).
    "130_whatsapp_inboxes",
    # SENTINELLA AGGIORNATA DALL'IMPORTAZIONE RUBRICA: la 131 (contatti riservati, contact_phones; additiva).
    "131_contact_import",
)


def approved_series():
    """Le migrazioni scoperte, verificate contigue ed ESATTAMENTE quelle approvate."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    assert [m.version for m in tutte] == list(APPROVED_VERSIONS), (
        "serie delle migrazioni diversa dall'inventario approvato (tests/migration_inventory.py)")
    return tutte


def approved_up_sql(after: int = 0) -> list[str]:
    """Il testo delle UP approvate con numero > `after`, nell'ordine della serie."""
    return [(ROOT / "migrations" / f"{v}.sql").read_text(encoding="utf-8")
            for v in APPROVED_VERSIONS if int(v[:3]) > after]


def latest_check_values(constraint: str) -> set[str]:
    """I letterali dell'ULTIMA definizione di un CHECK ... IN (...) nella serie
    approvata: il vincolo che il database ha davvero dopo tutte le migrazioni."""
    import re

    clausole = re.findall(rf"CONSTRAINT {constraint}\s*CHECK\s*\(([^;]*?)\)\s*\)?\s*[;,]",
                          "\n".join(approved_up_sql()))
    assert clausole, f"{constraint} non trovato nella serie approvata"
    return set(re.findall(r"'([a-z_0-9]+)'", clausole[-1]))
