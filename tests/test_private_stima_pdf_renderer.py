"""F07: real PDF rendering, no public file transport or external upload."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PAYLOAD = {
    "id_stima": 501, "nome": "Test", "cognome": "Locale",
    "email": "pdf@example.invalid", "telefono": "+39 333 123 4567",
    "indirizzo": "Via Sintetica 12, Tortoreto", "comune": "Tortoreto",
    "microzona": "Lido Sud", "tipologia": "Appartamento", "mq": 85.5,
    "piano": "3", "locali": "Trilocale", "bagni": 2, "ascensore": "Sì",
    "anno": 1998, "stato": "ristrutturato", "pertinenze": "garage",
    "stima": "180.000 €", "price_exact": 180000, "eur_mq_finale": 2000,
    "valore_pertinenze": 5000, "base_mq": 1500,
}


def test_known_report_filename_is_not_public_without_a_capability(monkeypatch, tmp_path):
    main = importlib.import_module("main")
    pdf = tmp_path / "stima_501.pdf"
    pdf.write_bytes(b"%PDF-1.4\nsynthetic-private-report\n%%EOF\n")
    for route in main.app.routes:
        if getattr(route, "path", None) == "/reports":
            monkeypatch.setattr(route.app, "directory", str(tmp_path))
            monkeypatch.setattr(route.app, "all_directories", [str(tmp_path)])
    response = TestClient(main.app).get("/reports/stima_501.pdf")
    assert response.status_code == 404, "a predictable filename exposed the private report"
    assert b"synthetic-private-report" not in response.content


def test_build_failure_never_uploads_or_returns_an_old_or_partial_pdf(monkeypatch, tmp_path):
    renderer = importlib.import_module("pdf_report")
    partial = tmp_path / "stima_501.pdf"
    partial.write_bytes(b"%PDF-1.4\nold-synthetic-report\n%%EOF\n")
    uploads = []
    document = renderer.SimpleDocTemplate

    def failing_document(destination, *args, **kwargs):
        doc = document(str(partial), *args, **kwargs)

        def build(*args, **kwargs):
            partial.write_bytes(b"%PDF-1.4\npartial-synthetic-report")
            raise RuntimeError("synthetic-build-failure")

        doc.build = build
        return doc

    monkeypatch.setattr(renderer, "SimpleDocTemplate", failing_document)
    if hasattr(renderer, "_upload_pdf_to_github"):
        monkeypatch.setattr(renderer, "_upload_pdf_to_github", lambda *args:
                            uploads.append(args) or "https://example.invalid/stima_501.pdf")
    with pytest.raises(RuntimeError, match="synthetic-build-failure"):
        renderer.genera_pdf_stima(dict(PAYLOAD), nome_file=partial.name)
    assert uploads == [], "failed rendering must not reach a storage provider"


def test_renderer_returns_real_pdf_bytes_without_filesystem_or_upload(monkeypatch, tmp_path):
    renderer = importlib.import_module("pdf_report")
    calls = []
    if hasattr(renderer, "_upload_pdf_to_github"):
        monkeypatch.setattr(renderer, "_upload_pdf_to_github", lambda *args:
                            calls.append(args) or "https://example.invalid/stima_501.pdf")
    document = renderer.SimpleDocTemplate

    def local_document(destination, *args, **kwargs):
        # The old generator receives a filename; the private renderer receives
        # its in-memory stream. No application/global output file is touched.
        if isinstance(destination, (str, Path)):
            destination = str(tmp_path / Path(destination).name)
        return document(destination, *args, **kwargs)

    monkeypatch.setattr(renderer, "SimpleDocTemplate", local_document)
    pdf = renderer.genera_pdf_stima(dict(PAYLOAD), nome_file="stima_501.pdf")
    assert isinstance(pdf, bytes), "the renderer returned a public storage URL"
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 1000
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field", ["nome", "indirizzo", "telefono", "email"])
@pytest.mark.parametrize("text", ["<b>Test</b>", '<img src="http://example.invalid/x">',
                                 '<img src="/private/tmp/synthetic-not-read.png">',
                                 '<img src="http://example.invalid/x"/>',
                                 '<img src="/private/tmp/synthetic-not-read.png"/>'])
def test_client_markup_is_literal_text_and_never_loads_an_image(monkeypatch, tmp_path, field, text):
    from reportlab.platypus import Spacer
    from reportlab.platypus import paraparser
    renderer = importlib.import_module("pdf_report")
    paragraph, document = renderer.Paragraph, renderer.SimpleDocTemplate
    plain_text, image_reads = [], []

    def literal_paragraph(content, *a, **k):
        result = paragraph(content, *a, **k)
        plain_text.append(result.getPlainText())
        return result

    def forbidden_image(source, *a, **k):
        image_reads.append(source)
        raise AssertionError("client markup requested an image")

    def local_document(destination, *a, **k):
        if isinstance(destination, (str, Path)):
            destination = str(tmp_path / Path(destination).name)
        return document(destination, *a, **k)

    monkeypatch.setattr(renderer, "Paragraph", literal_paragraph)
    monkeypatch.setattr(renderer, "SimpleDocTemplate", local_document)
    monkeypatch.setattr(renderer, "_logo_flowable", lambda *a, **k: Spacer(1, 1))
    monkeypatch.setattr(paraparser, "ImageReader", forbidden_image)
    if hasattr(renderer, "_upload_pdf_to_github"):
        monkeypatch.setattr(renderer, "_upload_pdf_to_github", lambda *a, **k:
                            "https://example.invalid/stima_501.pdf")
    renderer.genera_pdf_stima({**PAYLOAD, field: text}, nome_file="stima_501.pdf")
    assert image_reads == []
    assert any(text in value for value in plain_text), "ReportLab interpreted client text as markup"


def test_pdf_artifact_is_owned_content_not_a_new_crm_purge_blocker():
    import stime_purge

    class CatalogCursor:
        def execute(self, query):
            assert "pg_constraint" in query

        def fetchall(self):
            return [("stima_pdf_artifacts", "stima_id"), ("lead_stime", "stima_id")]

    references = stime_purge.references(CatalogCursor())
    assert not any(table == "stima_pdf_artifacts" for table, column, label in references)
    assert any(table == "lead_stime" for table, column, label in references)
