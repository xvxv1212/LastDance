import io
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.config import get_settings
from app.main import app

client = TestClient(app)
URL = "/api/contracts/extract-text"


def upload(data: bytes):
    return client.post(URL, files={"file": ("sample.pdf", data, "application/pdf")})


def test_text_pdf_extracts_actual_text():
    fixture = Path(__file__).parent / "fixtures" / "synthetic_text_contract.pdf"
    response = upload(fixture.read_bytes())
    assert response.status_code == 200
    body = response.json()
    assert body["page_count"] == 1
    assert body["has_text"] is True
    assert "Synthetic gym contract" in body["pages"][0]["text"]


def test_password_protected_pdf_is_rejected():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("fake-password")
    buf = io.BytesIO()
    writer.write(buf)
    response = upload(buf.getvalue())
    assert response.status_code == 400
    assert "암호" in response.json()["detail"]


def test_page_limit_is_enforced(monkeypatch):
    from app.api import contracts

    base = get_settings()
    monkeypatch.setattr(
        contracts,
        "get_settings",
        lambda: SimpleNamespace(max_pdf_bytes=base.max_pdf_bytes, max_pdf_pages=1),
    )
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    response = upload(buf.getvalue())
    assert response.status_code == 413
    assert "1페이지" in response.json()["detail"]
