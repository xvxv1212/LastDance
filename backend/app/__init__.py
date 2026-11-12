"""권리채비 AI 백엔드 패키지.

계층 구조(Clean Architecture, 의존성은 항상 안쪽으로만 향한다)

    interface (FastAPI 라우터·스키마)
        ↓
    application (유스케이스·포트[인터페이스])
        ↓
    domain (엔티티·값·정책·도메인 예외)  ← 어떤 프레임워크에도 의존하지 않음

    infrastructure (SQLAlchemy·파일 저장소·JWT) 는 application 의 포트를 "구현"하여
    바깥에서 안쪽으로 의존한다(의존성 역전 원칙, DIP).
"""
