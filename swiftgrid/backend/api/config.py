"""模型与密钥配置。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import get_config, runtime_config_enabled, set_config

router = APIRouter()


@router.get("/api/models")
def models():
    return {
        "recommended": "deepseek-v4-pro",
        "options": [
            {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro（推荐）", "base_url": "https://api.deepseek.com"},
            {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash", "base_url": "https://api.deepseek.com"},
            {"id": "custom", "label": "企业私有部署（自填 base_url / model）", "base_url": ""},
        ],
    }


@router.post("/api/config")
async def config(body: dict):
    if not runtime_config_enabled():
        raise HTTPException(status_code=403, detail="当前部署已关闭浏览器端模型配置")
    if not (body.get("api_key") or "").strip():
        raise HTTPException(status_code=400, detail="API Key 不能为空")
    set_config(
        api_key=body.get("api_key"),
        model=body.get("model"),
        base_url=body.get("base_url"),
    )
    return {"ok": True}


@router.get("/api/config/status")
def config_status():
    config = get_config()
    return {
        "configured": bool(config["api_key"]),
        "model": config["model"],
        "runtime_config_enabled": runtime_config_enabled(),
    }
