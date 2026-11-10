"""계약서 PDF 텍스트 추출 API의 안정적인 응답 형식."""

from pydantic import BaseModel


class ContractPage(BaseModel):
    page: int
    text: str


class ContractTextResponse(BaseModel):
    filename: str
    page_count: int
    # 스캔본처럼 추출된 글자가 없으면 False. OCR은 현재 MVP에 포함되지 않는다.
    has_text: bool
    pages: list[ContractPage]
