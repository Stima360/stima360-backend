"""Archivio CRM delle stime, in sola lettura e limitato all'agenzia corrente."""

from fastapi import HTTPException

from core.database import core_cursor


_DETAIL = """LEFT JOIN LATERAL (
    SELECT d.id, d.data, d.classe, d.riscaldamento, d.condizionatore,
           d.spese_cond, d.esposizione, d.arredo, d.note, d.contatto,
           d.sopralluogo
      FROM stime_dettagliate d
     WHERE d.stima_id = s.id AND d.agency_id = s.agency_id
     ORDER BY d.data DESC NULLS LAST, d.id DESC
     LIMIT 1
) detail ON TRUE"""

# Only an active site link may name an immobile. A stima may have several
# lead_stime relations; without a certified source lead, an ambiguous link is
# omitted instead of choosing an arbitrary person's contact.
_LINKS = """LEFT JOIN LATERAL (
    SELECT ps.property_id, ps.lead_id, ps.contact_id
      FROM property_site_sources ps
      JOIN properties p ON p.id = ps.property_id AND p.agency_id = s.agency_id
     WHERE ps.stima_id = s.id AND ps.agency_id = s.agency_id
       AND ps.status = 'active'
     LIMIT 1
) source ON TRUE
LEFT JOIN LATERAL (
    SELECT CASE WHEN count(*) = 1 THEN min(l.id) END AS lead_id,
           CASE WHEN count(*) = 1 THEN min(c.id) END AS contact_id
      FROM lead_stime ls
      JOIN leads l ON l.id = ls.lead_id AND l.agency_id = s.agency_id
      JOIN contacts c ON c.id = l.contact_id AND c.agency_id = s.agency_id
     WHERE ls.stima_id = s.id
       AND (source.lead_id IS NULL OR l.id = source.lead_id)
       AND (source.contact_id IS NULL OR c.id = source.contact_id)
) related ON TRUE
LEFT JOIN stima_pdf_artifacts pdf
  ON pdf.stima_id = s.id AND pdf.agency_id = s.agency_id"""

_CARD = """s.id, s.data, s.comune, s.microzona, s.via, s.civico,
    s.tipologia, s.mq, s.piano, s.nome, s.cognome, s.email, s.telefono,
    detail.id AS detail_id, detail.data AS detail_data,
    related.lead_id, related.contact_id, source.property_id,
    pdf.status AS pdf_status,
    pdf.render_payload ->> 'price_exact' AS price_exact"""

_SEARCH = "(" + " OR ".join(
    f"coalesce(s.{column}, '') ILIKE %s ESCAPE '!'"
    for column in ("nome", "cognome", "email", "telefono", "comune", "via")
) + ")"


def list_stime(ctx, *, view: str, search: str | None, limit: int, offset: int) -> dict:
    agency_id = ctx.require_agency()
    criteria = ["s.agency_id = %s"]
    params: list = [agency_id]
    if view == "detailed":
        criteria.append("EXISTS (SELECT 1 FROM stime_dettagliate d "
                        "WHERE d.stima_id = s.id AND d.agency_id = s.agency_id)")
    elif view == "base":
        criteria.append("NOT EXISTS (SELECT 1 FROM stime_dettagliate d "
                        "WHERE d.stima_id = s.id AND d.agency_id = s.agency_id)")
    elif view != "all":
        raise ValueError("Unknown valuation view")
    if search and search.strip():
        term = search.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        criteria.append(_SEARCH)
        params.extend([f"%{term}%"] * 6)
    where = " AND ".join(criteria)
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT count(*) AS n FROM stime s WHERE {where}", params)
        total = cur.fetchone()["n"]
        cur.execute(f"""SELECT {_CARD} FROM stime s
            {_DETAIL} {_LINKS}
            WHERE {where}
            ORDER BY s.data DESC NULLS LAST, s.id DESC
            LIMIT %s OFFSET %s""", [*params, limit, offset])
        items = [dict(row) for row in cur.fetchall()]
        cur.execute("""SELECT count(*) AS total,
            count(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM stime_dettagliate d
                WHERE d.stima_id = s.id AND d.agency_id = s.agency_id
            )) AS detailed
            FROM stime s WHERE s.agency_id = %s""", (agency_id,))
        stats = dict(cur.fetchone())
    return {"items": items, "total": total, "has_more": offset + len(items) < total,
            "limit": limit, "offset": offset, "stats": stats}


def get_stima(ctx, stima_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        cur.execute(f"""SELECT {_CARD},
            s.locali, s.bagni, s.ascensore, s.pertinenze, s.anno, s.stato,
            s.posizionemare, s.distanzamare, s.barrieramare, s.vistamare,
            s.mqgiardino, s.mqgarage, s.mqcantina, s.mqpostoauto,
            s.mqtaverna, s.mqsoffitta, s.mqterrazzo, s.numbalconi,
            s.altrodescrizione,
            detail.classe, detail.riscaldamento, detail.condizionatore,
            detail.spese_cond, detail.esposizione, detail.arredo,
            detail.note AS detail_note, detail.contatto, detail.sopralluogo
            FROM stime s {_DETAIL} {_LINKS}
            WHERE s.id = %s AND s.agency_id = %s""", (stima_id, agency_id))
        result = cur.fetchone()
    if result is None:
        raise HTTPException(status_code=404, detail="Stima non trovata")
    return dict(result)
