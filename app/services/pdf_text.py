"""PDF 바이트에서 페이지별 텍스트 추출. 업로드 파일은 저장하지 않는다."""

import io

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.schemas.contracts import ContractPage

PDF_MAGIC = b"%PDF-"


class PDFExtractionError(Exception):
    """손상되었거나 처리할 수 없는 PDF."""


class PDFPageLimitError(PDFExtractionError):
    """허용 페이지 수를 초과한 PDF."""


def extract_pages(data: bytes, *, max_pages: int) -> list[ContractPage]:
    """페이지 단위 텍스트 추출. 출력은 검증된 사실이나 법률 판단이 아니다."""
    if not data.startswith(PDF_MAGIC):
        raise PDFExtractionError("PDF 형식의 파일만 올릴 수 있습니다.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PDFExtractionError("암호가 설정된 PDF는 처리할 수 없습니다.")
        if len(reader.pages) > max_pages:
            raise PDFPageLimitError(f"PDF는 {max_pages}페이지 이하여야 합니다.")
        return [
            ContractPage(page=idx, text=(page.extract_text() or "").strip())
            for idx, page in enumerate(reader.pages, start=1)
        ]
    except PDFExtractionError:
        raise
    except (PyPdfError, ValueError, TypeError, OSError) as exc:
        raise PDFExtractionError("PDF를 읽을 수 없습니다.") from exc
