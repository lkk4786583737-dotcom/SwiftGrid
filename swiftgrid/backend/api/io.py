"""输入解析 / 预览 / 导出。"""
from __future__ import annotations

import io

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from ..deps import engine
from ..services.session import get_data, put_data
from ..services.tableio import (
    PASTE_ROW_HINT,
    as_named_dict,
    column_roles,
    parse_paste,
    result_to_xlsx,
    suggest_charts,
    summarize,
)

router = APIRouter()


@router.post("/api/parse")
async def parse(
    slot: str = Form("source"),
    paste_text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    """万能输入：上传 files[] 或 粘贴 paste_text。slot = source | target。
    返回 handle（后续只传句柄）+ 预览 sheets + oversize（粘贴过大提示上传）。
    """
    oversize = False

    if files:
        pairs = [(f.filename, io.BytesIO(await f.read())) for f in files]
        if slot == "target":
            fn, fl = pairs[0]
            store = engine.load_target(fn, fl)          # df 或 dict（真实形状）
            named = as_named_dict(store, "目标表")
        else:
            store = engine.load_dfs_from_uploaded(pairs)  # dict[名称->df]
            named = store
    elif paste_text.strip():
        df, err = parse_paste(paste_text)
        if err:
            raise HTTPException(status_code=400, detail=err)
        oversize = len(df) > PASTE_ROW_HINT
        if slot == "target":
            store = df
            named = {"目标表": df}
        else:
            store = {"粘贴表_数据": df}
            named = store
    else:
        raise HTTPException(status_code=400, detail="未提供文件或粘贴内容")

    handle = put_data(store)
    return {"handle": handle, "sheets": summarize(named), "oversize": oversize}


@router.post("/api/export/xlsx")
async def export_xlsx(body: dict):
    obj = get_data(body.get("result_handle", ""))
    if obj is None:
        raise HTTPException(status_code=404, detail="结果不存在或已过期")
    data = result_to_xlsx(obj)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=swiftgrid_result.xlsx"},
    )


@router.post("/api/charts/suggest")
async def charts_suggest(body: dict):
    obj = get_data(body.get("result_handle", ""))
    if obj is None:
        raise HTTPException(status_code=404, detail="结果不存在或已过期")
    named = as_named_dict(obj, "结果表")
    sheet = body.get("sheet")
    df = named.get(sheet) if sheet and sheet in named else next(iter(named.values()))
    time, cat, num = column_roles(df)
    return {
        "roles": {"time": time, "category": cat, "numeric": num},
        "suggestions": suggest_charts(time, cat, num),
    }
