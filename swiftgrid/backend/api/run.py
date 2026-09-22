"""执行流：匹配（完全匹配自动 / 多匹配让用户选）→ 执行+自愈。SSE 推进度。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
import pandas as pd
from sse_starlette.sse import EventSourceResponse

from ..deps import engine, engine_kwargs, require_key, storage
from ..services.orchestrator import Job, run_job, sse
from ..services import compose
from ..services.session import get_data, get_session, new_session, put_data
from ..services.tableio import as_named_dict, summarize

router = APIRouter()

AUTO_GAP = 0.15  # 与次优拉开这么多 → 视为唯一强匹配，免确认


@router.post("/api/run/match")
async def match(body: dict):
    src = get_data(body.get("source_handle", ""))
    if src is None:
        raise HTTPException(status_code=400, detail="源表句柄无效")
    best, score, all_scored = storage.match(src)
    candidates = [{"id": e["id"], "name": e["name"], "score": e["score"]} for e in all_scored]

    auto = False
    if best and score >= storage.HIGH_CONF:
        second = all_scored[1]["score"] if len(all_scored) > 1 else 0.0
        auto = len(all_scored) == 1 or (score - second) >= AUTO_GAP

    return {
        "auto": auto,
        "best": {"id": best["id"], "name": best["name"], "score": round(score, 3)} if best else None,
        "candidates": candidates,
    }


@router.post("/api/run/execute")
async def execute(body: dict):
    if not body.get("logic_id") or get_data(body.get("source_handle", "")) is None:
        raise HTTPException(status_code=400, detail="缺少 logic_id 或源表句柄无效")
    meta = next((entry for entry in storage.load_index() if entry["id"] == body["logic_id"]), None)
    if meta is None:
        raise HTTPException(status_code=404, detail="逻辑不存在")
    if meta.get("origin", "learn") != "smart_table":
        try:
            require_key()
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    sid = new_session({
        "logic_id": body["logic_id"],
        "source_handle": body["source_handle"],
        "origin": meta.get("origin", "learn"),
        "job": None,
    })
    return {"session_id": sid}


@router.get("/api/run/stream/{sid}")
async def stream(sid: str):
    sess = get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    job = Job()
    sess["job"] = job

    def work(j: Job):
        ent = storage.load_entry(sess["logic_id"])
        src = get_data(sess["source_handle"])
        j.emit("progress", {"stage": "apply", "label": "套用已学逻辑处理新表"})
        if j.cancelled:
            return j.finish("cancelled")

        if ent.get("origin", "learn") == "smart_table":
            result_payload = compose.execute_plan_on_frames(
                ent["plan"], src, ent.get("classification_mapping", {})
            )
            result = {
                sheet["name"]: pd.DataFrame(sheet["rows"], columns=sheet["columns"])
                for sheet in result_payload["sheets"]
            }
            healed = False
            err = None
        else:
            result, healed, err = engine.phase2_process_with_healing(
                ent["locked_code"], src, ent["learn_sources"], ent["learn_target"],
                ent["learn_profile"], require_key(), **engine_kwargs(),
            )
        if j.cancelled:
            return j.finish("cancelled")
        if err:
            return j.finish("error", {"message": err})

        if healed:
            j.emit("progress", {"stage": "heal", "label": "新表结构有变，已自动自愈逻辑"})
            storage.update_code_and_profile(sess["logic_id"], healed, ent["learn_profile"])
        storage.touch_entry(sess["logic_id"])

        rh = put_data(result)
        return j.finish("success", {
            "result_handle": rh,
            "healed": bool(healed),
            "result_preview": summarize(as_named_dict(result, "结果表"), 20),
            "before_preview": summarize(src, 8),
        })

    run_job(job, work)
    return EventSourceResponse(sse(job))


@router.post("/api/run/cancel/{sid}")
async def cancel(sid: str):
    sess = get_session(sid)
    if sess and sess.get("job"):
        sess["job"].cancel()
    return {"ok": True}
