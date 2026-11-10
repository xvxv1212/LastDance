"""계약서 PDF 업로드 API. 기존 경로/응답 형식 유지."""

from fastapi import APIRouter, HTTPException, UploadFile

from app.config import get_settings
from app.schemas.contracts import ContractTextResponse
from app.services.pdf_text import PDFExtractionError, PDFPageLimitError, extract_pages

router = APIRouter(prefix="/api/contracts", tags=["contracts"])


@router.post("/extract-text", response_model=ContractTextResponse)
async def extract_contract_text(file: UploadFile) -> ContractTextResponse:
    settings = get_settings()
    data = await file.read(settings.max_pdf_bytes + 1)
    if len(data) > settings.max_pdf_bytes:
        limit_mb = settings.max_pdf_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"PDF는 {limit_mb}MB 이하만 올릴 수 있습니다.")

    try:
        pages = extract_pages(data, max_pages=settings.max_pdf_pages)
    except PDFPageLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except PDFExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ContractTextResponse(
        filename=file.filename or "",
        page_count=len(pages),
        has_text=any(page.text for page in pages),
        pages=pages,
    )
