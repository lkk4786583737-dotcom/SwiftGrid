"""SwiftGrid 后端入口（FastAPI）。

开发(A)：uvicorn swiftgrid.backend.main:app --reload --port 8000   （前端 next dev :3000，CORS）
发布(B)：前端 next build 产出 frontend/out → 本服务同源托管静态文件（单进程）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import analysis, compose, config, io, learn, logics, run

app = FastAPI(title="SwiftGrid API", version="0.1.0")

# 开发期跨域（A 方案）；发布期同源，不经过这里
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

for _m in (config, io, logics, learn, run, analysis, compose):
    app.include_router(_m.router)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "swiftgrid"}


# 发布(B)：若已构建前端静态文件，则同源托管（放最后，避免吃掉 /api）
_OUT = Path(__file__).resolve().parents[2] / "frontend" / "out"
if _OUT.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_OUT), html=True), name="static")
