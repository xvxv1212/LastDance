import io

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.config import get_settings
from app.main import app

client = TestClient(app)
URL = "/api/contracts/extract-text"


def make_blank_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def upload(data: bytes, filename: str = "contract.pdf"):
    return client.post(URL, files={"file": (filename, data, "application/pdf")})


def test_blank_pdf_is_accepted_and_flagged_as_no_text():
    response = upload(make_blank_pdf(pages=2))

    assert response.status_code == 200
    body = response.json()
    assert body["page_count"] == 2
    assert body["has_text"] is False
    assert [page["page"] for page in body["pages"]] == [1, 2]


def test_non_pdf_content_is_rejected_even_with_pdf_extension():
    response = upload(b"this is not a pdf", filename="fake.pdf")

    assert response.status_code == 400


def test_broken_pdf_is_rejected():
    response = upload(b"%PDF-1.7\nbroken body without structure")

    assert response.status_code == 400


def test_oversized_file_is_rejected():
    too_big = b"%PDF-" + b"0" * get_settings().max_pdf_bytes

    response = upload(too_big)

    assert response.status_code == 413


def test_missing_file_is_rejected():
    response = client.post(URL)

    assert response.status_code == 422
