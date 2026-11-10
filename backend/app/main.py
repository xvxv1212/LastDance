from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import contracts
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="권리채비 AI API",
    description="헬스장·필라테스 회원권 해지·환불 상황의 사실 정리와 상담 준비를 돕는 API. "
    "법률 결론이나 환불액을 판정하지 않는다.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(contracts.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
