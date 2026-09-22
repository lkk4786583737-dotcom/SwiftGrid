"""智能制表 API：文件导入、方案确认、全量确定性执行和结果交付。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from ..deps import get_config, storage
from ..services import compose
from ..services.orchestrator import Job, run_job, sse
from ..services.session import get_session, new_session

router = APIRouter()


def _table_required_fields(table: dict) -> set[str]:
    required: set[str] = set()

    def visit_expression(expression) -> None:
        if not isinstance(expression, dict):
            return
        if expression.get("op") == "field" and expression.get("field"):
            required.add(expression["field"])
        condition = expression.get("condition")
        if isinstance(condition, dict) and condition.get("field"):
            required.add(condition["field"])
        for key in ("args",):
            for item in expression.get(key, []) if isinstance(expression.get(key), list) else []:
                visit_expression(item)
        visit_expression(expression.get("then"))
        visit_expression(expression.get("else"))

    for condition in table.get("filters", []):
        if isinstance(condition, dict) and condition.get("field"):
            required.add(condition["field"])
    for item in table.get("group_by", []):
        if isinstance(item, dict) and item.get("field"):
            required.add(item["field"])
    for collection in ("columns", "aggregations"):
        for item in table.get(collection, []):
            if isinstance(item, dict):
                visit_expression(item.get("expression"))
    return required


def _raise(exc: compose.ComposeError) -> None:
    raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message, **exc.details}) from exc


def _manifest_or_error(sid: str) -> dict:
    try:
        return compose.read_manifest(sid)
    except compose.ComposeError as exc:
        _raise(exc)
        raise AssertionError  # pragma: no cover


def _start_job(sid: str, key: str, worker) -> Job:
    sess = get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND", "message": "智能制表会话不存在。"})
    current = sess.get(key)
    if current is not None and not current.done:
        return current
    job = Job()
    sess[key] = job

    def wrapped(j: Job):
        try:
            worker(j)
        except compose.ComposeError as exc:
            j.finish("error", {"code": exc.code, "message": exc.message, **exc.details})
        except Exception as exc:  # noqa: BLE001
            j.finish("error", {"code": "COMPOSE_FAILED", "message": f"{type(exc).__name__}: {exc}"})

    run_job(job, wrapped)
    return job


@router.post("/api/compose/import")
async def import_files(files: list[UploadFile] = File(default=[])):
    if not files:
        raise HTTPException(status_code=400, detail={"code": "DATASET_INCOMPLETE", "message": "至少上传一个源数据文件。"})
    sid = new_session({"kind": "compose"})
    compose.create_session(sid)
    uploaded = []
    try:
        for upload in files:
            # UploadFile already uses a spooled temporary file. Copy it to the
            # session cache in bounded chunks and hash while copying.
            entry = compose.add_uploaded_file(sid, upload.filename or "未命名文件", iter(lambda: upload.file.read(1024 * 1024), b""))
            uploaded.append(entry)
    except compose.ComposeError as exc:
        manifest = compose.read_manifest(sid)
        manifest["status"] = "FAILED"
        compose.write_manifest(sid, manifest)
        _raise(exc)
    except Exception as exc:  # noqa: BLE001
        manifest = compose.read_manifest(sid)
        manifest["status"] = "FAILED"
        compose.write_manifest(sid, manifest)
        raise HTTPException(status_code=400, detail={"code": "FILE_READ_FAILED", "message": str(exc)}) from exc
    return {"session_id": sid, "status": "UPLOADING", "files": uploaded}


@router.get("/api/compose/import/stream/{sid}")
async def import_stream(sid: str):
    manifest = _manifest_or_error(sid)
    if manifest.get("status") == "READY":
        job = Job()
        job.finish("success", {"dataset": manifest.get("dataset"), "directions": (manifest.get("dataset") or {}).get("directions", []), "plan": manifest.get("plan")})
    else:
        model_config = get_config()
        job = _start_job(sid, "import_job", lambda j: compose.run_import(sid, j, model_config=model_config))
    return EventSourceResponse(sse(job))


@router.post("/api/compose/plan/{sid}")
async def choose_plan(sid: str, body: dict):
    manifest = _manifest_or_error(sid)
    if manifest.get("status") not in {"READY", "COMPLETED"}:
        _raise(compose.ComposeError("DATASET_INCOMPLETE", "请等待源数据完成扫描和全量缓存。"))
    direction_id = body.get("direction_id") or body.get("direction")
    prompt = (body.get("message") or body.get("text") or "").strip()
    # A new direction is a new plan conversation. Reusing the old chat turns
    # would display them as confirmed without applying them to the new plan.
    facts: list[dict] = []
    if prompt:
        facts.append({"turn": len(facts) + 1, "text": prompt})
    try:
        plan = compose.make_plan(manifest["dataset"], direction_id, prompt, facts, model_config=get_config())
    except compose.ComposeError as exc:
        _raise(exc)
    compose.invalidate_result_state(manifest)
    manifest["facts"] = facts
    manifest["plan"] = plan
    compose.write_manifest(sid, manifest)
    preview = compose.get_preview(sid, limit=20)
    return {"dataset": compose._dataset_summary(manifest["dataset"]), "directions": manifest["dataset"].get("directions", []), "plan": plan, "questions": plan.get("questions", []), "preview": preview}


@router.post("/api/compose/message/{sid}")
async def compose_message(sid: str, body: dict):
    manifest = _manifest_or_error(sid)
    if manifest.get("status") not in {"READY", "COMPLETED"}:
        _raise(compose.ComposeError("DATASET_INCOMPLETE", "当前正在处理数据，请完成或取消后再修改方案。"))
    text = (body.get("message") or body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail={"code": "PLAN_INVALID", "message": "补充内容不能为空。"})
    if not manifest.get("plan"):
        _raise(compose.ComposeError("PLAN_INVALID", "请先选择或描述一个目标表方向。"))
    facts = manifest.setdefault("facts", [])
    facts.append({"turn": len(facts) + 1, "text": text})
    plan = compose.apply_message(manifest["plan"], manifest["dataset"], text, facts, model_config=get_config())
    compose.invalidate_result_state(manifest)
    manifest["plan"] = plan
    compose.write_manifest(sid, manifest)
    preview = compose.get_preview(sid, limit=20)
    return {"plan": plan, "questions": plan.get("questions", []), "facts": facts, "preview": preview}


@router.get("/api/compose/preview/{sid}")
async def preview(sid: str, direction_id: str | None = None, limit: int = 100):
    try:
        return compose.get_preview(sid, direction_id, limit)
    except compose.ComposeError as exc:
        _raise(exc)


@router.post("/api/compose/execute/{sid}")
async def execute(sid: str):
    manifest = _manifest_or_error(sid)
    try:
        compose.validate_plan(manifest)
    except compose.ComposeError as exc:
        _raise(exc)
    sess = get_session(sid)
    if sess is None:
        # A server restart loses in-memory jobs, but a valid on-disk session can
        # still be inspected and executed again in this single-user app.
        from ..services.session import _SESS  # noqa: PLC0415
        _SESS[sid] = {"kind": "compose"}
    return {"session_id": sid, "status": "QUEUED"}


@router.get("/api/compose/execute/stream/{sid}")
async def execute_stream(sid: str):
    _manifest_or_error(sid)
    job = _start_job(sid, "execute_job", lambda j: compose.run_execute(sid, j))
    return EventSourceResponse(sse(job))


@router.get("/api/compose/result/{sid}")
async def result(sid: str, sheet: str | None = None, offset: int = 0, limit: int = 100):
    try:
        return compose.get_result_page(sid, sheet, offset, limit)
    except compose.ComposeError as exc:
        _raise(exc)


@router.post("/api/compose/classification/{sid}")
async def classification(sid: str, body: dict):
    mapping = body.get("mapping") or {}
    if not mapping and isinstance(body.get("corrections"), list):
        mapping = {str(item.get("entity_id", "")): str(item.get("user_confirmed_type", "")) for item in body["corrections"] if item.get("entity_id")}
    if not isinstance(mapping, dict) or not mapping:
        raise HTTPException(status_code=400, detail={"code": "CLASSIFICATION_PENDING", "message": "没有可保存的分类修正。"})
    try:
        return {"ok": True, **compose.apply_classification(sid, mapping)}
    except compose.ComposeError as exc:
        _raise(exc)


@router.get("/api/compose/export/{sid}")
async def export(sid: str):
    try:
        path = compose.export_result(sid)
    except compose.ComposeError as exc:
        _raise(exc)
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=Path(path).name)


@router.post("/api/compose/complete/{sid}")
async def complete(sid: str, body: dict):
    manifest = _manifest_or_error(sid)
    quality = manifest.get("quality") or {}
    if not quality.get("can_complete"):
        code = "CLASSIFICATION_PENDING" if quality.get("pending_classification_count") else "RECONCILIATION_FAILED"
        _raise(compose.ComposeError(code, "质量门禁未通过，暂不能完成制表。", details={"quality": quality}))
    name = (body.get("name") or "").strip() or (manifest.get("plan") or {}).get("direction_title") or "智能制表逻辑"
    if manifest.get("logic_id"):
        return {"ok": True, "logic_id": manifest["logic_id"], "name": name, "quality": quality, "already_saved": True}
    try:
        plan = manifest["plan"]
        dataset = manifest["dataset"]
        required_fields = set(plan.get("dimensions", []))
        required_fields.update(value for value in plan.get("role_bindings", {}).values() if value)
        required_fields.update(item.get("field") for item in plan.get("measures", []) if item.get("field"))
        required_fields.update(item.get("column") for item in plan.get("filters", []) if item.get("column"))
        if plan.get("output_kind") == "table":
            required_fields.update(_table_required_fields(plan.get("table") or {}))
        field_types = {
            field.get("name"): field.get("data_type")
            for field in dataset.get("fields", [])
            if field.get("name") in required_fields
        }
        entry_id = storage.save_compose_entry(
            name=name,
            plan=plan,
            profile=[
                {key: field.get(key) for key in ("name", "role", "confidence", "null_rate", "data_type")}
                for field in manifest["dataset"].get("fields", [])
            ],
            output_schema=[
                {"name": sheet.get("name"), "columns": sheet.get("columns", [])}
                for sheet in manifest.get("result_sheets", [])
            ],
            classification_mapping=manifest.get("classification_mapping", {}),
            structure_profile=[
                {"sheet": item.get("sheet", ""), "columns": item.get("columns", [])}
                for item in manifest["dataset"].get("sheets", [])
            ],
            applicability={
                "schema_signature": dataset.get("schema", {}).get("signature"),
                "required_fields": sorted(required_fields),
                "required_types": field_types,
                "output_kind": plan.get("output_kind"),
                "plan_schema_version": plan.get("schema_version"),
                "quality_rules": [item.get("name") for item in quality.get("reconciliations", [])],
            },
        )
    except storage.StorageFullError as exc:
        _raise(compose.ComposeError("LOGIC_LIBRARY_FULL", str(exc), status=409))
    except Exception as exc:  # noqa: BLE001
        _raise(compose.ComposeError("LOGIC_SAVE_FAILED", f"逻辑保存失败：{exc}", status=500))
    manifest["logic_id"] = entry_id
    compose.write_manifest(sid, manifest)
    return {"ok": True, "logic_id": entry_id, "name": name, "quality": quality}


@router.post("/api/compose/cancel/{sid}")
async def cancel(sid: str):
    manifest = _manifest_or_error(sid)
    sess = get_session(sid)
    if sess:
        for key in ("import_job", "execute_job"):
            job = sess.get(key)
            if job and not job.done:
                job.cancel()
    if manifest.get("status") in {"UPLOADING", "SCANNING", "EXECUTING"}:
        manifest["status"] = "CANCELLED"
        compose.write_manifest(sid, manifest)
    return {"ok": True}
