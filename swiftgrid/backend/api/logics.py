"""逻辑库 CRUD。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import storage

router = APIRouter()


@router.get("/api/logics")
def list_logics():
    return [
        {
            "id": e["id"],
            "name": e["name"],
            "created_at": e.get("created_at", ""),
            "last_used_at": e.get("last_used_at", ""),
            "learn_profile": e.get("learn_profile", []),
            "origin": e.get("origin", "learn"),
            "schema_version": e.get("schema_version", 1),
        }
        for e in storage.load_index()
    ]


@router.delete("/api/logics/{eid}")
def delete_logic(eid: str):
    storage.delete_entry(eid)
    return {"ok": True}


@router.patch("/api/logics/{eid}")
async def rename_logic(eid: str, body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    storage.rename_entry(eid, name)
    return {"ok": True}
