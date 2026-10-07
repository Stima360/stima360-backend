"""PDF double for existing offline tests of unrelated legacy consumers.

Authorization, snapshots, SQL and byte integrity are tested with PostgreSQL in
 test_private_stima_pdf_postgres.py; these callers retain their focused doubles.
"""
import copy
from fastapi import HTTPException


def install_private_pdf_fake(monkeypatch, main_module):
    snapshots = {}

    def prepare(stima_id, agency_id, payload, **kwargs):
        snapshots[stima_id] = copy.deepcopy(payload)

    def generate(stima_id, renderer, **kwargs):
        try:
            return renderer(copy.deepcopy(snapshots[stima_id]), nome_file=f"stima_{stima_id}.pdf")
        except Exception:
            raise HTTPException(status_code=503, detail="PDF non disponibile") from None

    monkeypatch.setattr(main_module.stima_pdf, "prepare", prepare)
    monkeypatch.setattr(main_module.stima_pdf, "generate", generate)
