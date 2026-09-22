"""学习流：生成+校验（自修正 ≤3）→ 失败诊断澄清（≤3 轮）→ 成功落库。SSE 推进度。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..deps import engine, engine_kwargs, require_key, storage
from ..services.orchestrator import Job, run_job, sse
from ..services.session import get_data, get_session, new_session, put_data
from ..services.tableio import as_named_dict, summarize

router = APIRouter()


@router.post("/api/learn/start")
async def start(body: dict):
    src = get_data(body.get("source_handle", ""))
    tgt = get_data(body.get("target_handle", ""))
    if src is None or tgt is None:
        raise HTTPException(status_code=400, detail="源表或目标表句柄无效")
    try:
        require_key()  # 缺 key 早失败
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    sid = new_session({
        "source": src,
        "target": tgt,
        "clarifications": [],
        "round": 0,
        "formula_hints": body.get("formula_hints", ""),
        "profile": engine.build_learn_profile(src),
        "code": None,
        "result": None,
        "job": None,
    })
    return {"session_id": sid}


@router.get("/api/learn/stream/{sid}")
async def stream(sid: str):
    sess = get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    job = Job()
    sess["job"] = job

    def work(j: Job):
        j.emit("progress", {"stage": "read", "label": "读取源表"})
        j.emit("progress", {"stage": "target", "label": "理解目标样例"})
        j.emit("progress", {"stage": "infer", "label": "推导处理逻辑 · 本地校验中"})
        if j.cancelled:
            return j.finish("cancelled")

        clar = "\n".join(f"- {c}" for c in sess["clarifications"]) or "（尚无用户澄清）"
        code, diff, last = engine.generate_and_test_logic(
            sess["source"], sess["target"], require_key(),
            formula_hints=sess["formula_hints"], clarification_context=clar,
            **engine_kwargs(),
        )
        if j.cancelled:
            return j.finish("cancelled")

        if code:
            sess["code"] = code
            sess["result"] = last
            name = engine.generate_logic_name(sess["source"], sess["target"], require_key(), **engine_kwargs())
            rh = put_data(last)
            return j.finish("success", {
                "code": code,
                "suggested_name": name,
                "result_handle": rh,
                "result_preview": summarize(as_named_dict(last, "结果表"), 20),
            })

        # 失败 → 诊断 + 澄清
        sess["round"] += 1
        sess["result"] = last
        diag = engine.diagnose_mismatch(
            sess["source"], sess["target"], last, diff or "",
            "\n".join(sess["clarifications"]), require_key(), **engine_kwargs(),
        )
        return j.finish("clarify", {
            "diagnosis": diag.get("diagnosis", ""),
            "suggested_clarification": diag.get("suggested_clarification", ""),
            "diff": diff or "",
            "round": sess["round"],
            "max": engine.MAX_CLARIFICATION_ROUNDS,
        })

    run_job(job, work)
    return EventSourceResponse(sse(job))


@router.post("/api/learn/clarify/{sid}")
async def clarify(sid: str, body: dict):
    sess = get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    text = (body.get("clarification") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="澄清内容为空")
    sess["clarifications"].append(text)
    return {"ok": True, "round": sess["round"]}  # 前端随后重新 GET /stream 重试


@router.post("/api/learn/save/{sid}")
async def save(sid: str, body: dict):
    sess = get_session(sid)
    if sess is None or not sess.get("code"):
        raise HTTPException(status_code=400, detail="没有可保存的逻辑")
    name = (body.get("name") or "").strip() or "未命名逻辑"
    eid = storage.save_entry(name, sess["code"], sess["profile"], sess["source"], sess["target"])
    return {"id": eid}


@router.post("/api/learn/cancel/{sid}")
async def cancel(sid: str):
    sess = get_session(sid)
    if sess and sess.get("job"):
        sess["job"].cancel()
    return {"ok": True}
