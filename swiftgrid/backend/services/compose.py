"""Smart-table composition service.

The composition flow deliberately keeps the model-facing part separate from
the data engine. Files are written to a session directory, source rows are
cached in SQLite, and every output amount is calculated with ``Decimal``.
"""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import re
import random
import shutil
import sqlite3
import threading
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Iterator

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import compose_ai, compose_plan
from .orchestrator import Job

ROOT = Path(__file__).resolve().parents[3]
SESSION_ROOT = ROOT / "data" / "compose_sessions"
PERCENT_QUANT = Decimal("0.0001")
MAX_SAMPLE_ROWS = 300
MAX_PROFILE_DISTINCT = 20_000
MAX_PROFILE_TOP_VALUES = 200
MAX_PREVIEW_SOURCE_ROWS = 1_500
MAX_PREVIEW_OUTPUT_ROWS = 100
MAX_UPLOAD_FILE_BYTES = 512 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_COLUMNS = 1_000
MAX_SOURCE_ROWS = 5_000_000
MAX_EXCEL_DATA_ROWS = 1_048_575
INSERT_BATCH = 1000
_manifest_lock = threading.RLock()


class ComposeError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _safe_json(value.item())
        except Exception:  # noqa: BLE001
            pass
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_safe_json, separators=(",", ":"))


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return re.sub(r"\s+", "", text)


def _display(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text:
        return None
    if text.startswith("￥") or text.startswith("¥"):
        text = text[1:]
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _money(value: Decimal) -> str:
    # Source workbooks often carry more than two decimal places. Keep the
    # exact Decimal aggregate by default; a presentation-specific rounding
    # policy must be an explicit future plan option, never a silent loss.
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _percent(value: Decimal) -> str:
    return str((value * Decimal("100")).quantize(PERCENT_QUANT, rounding=ROUND_HALF_UP)) + "%"


def _decimal_delta(value: Decimal) -> str:
    return _money(value.copy_abs()) if value == 0 else _money(value)


def _empty_row(row: Iterable[Any]) -> bool:
    return all(_display(v) == "" for v in row)


def _unique_headers(row: Iterable[Any]) -> list[str]:
    headers = [_display(v) for v in row]
    while headers and not headers[-1]:
        headers.pop()
    if not headers or any(not h for h in headers):
        raise ComposeError("SCHEMA_CONFLICT", "存在空字段名，无法安全合并工作表。")
    normalized = [_normalize(h).lower() for h in headers]
    if len(set(normalized)) != len(normalized):
        raise ComposeError("SCHEMA_CONFLICT", "工作表存在重复字段名，无法安全合并。", details={"columns": headers})
    return headers


def _find_header(rows: list[tuple[Any, ...]]) -> int:
    candidates = [(i, r) for i, r in enumerate(rows) if not _empty_row(r)]
    if not candidates:
        raise ComposeError("SCHEMA_CONFLICT", "工作表没有可识别的表头。")
    # Real headers usually have many non-empty text cells; this tolerates a
    # short title row above the table without guessing from the data body.
    return max(
        candidates[:10],
        key=lambda x: (sum(_display(v) != "" for v in x[1]), sum(isinstance(v, str) for v in x[1]), -x[0]),
    )[0]


def _iter_csv(path: Path) -> Iterator[tuple[Any, ...]]:
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
        handle.read(0)
    except Exception:
        handle = path.open("r", encoding="gb18030", newline="")
    with handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;") if sample.strip() else csv.excel
        except csv.Error:
            dialect = csv.excel_tab if "\t" in sample else csv.excel
        yield from (tuple(row) for row in csv.reader(handle, dialect))


def _iter_workbook(path: Path) -> Iterator[tuple[str, Iterator[tuple[Any, ...]]]]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        yield path.stem[:31] or "数据", _iter_csv(path)
        return
    if suffix not in {".xlsx", ".xlsm"}:
        raise ComposeError("FILE_UNSUPPORTED", f"暂不支持文件类型：{path.suffix or '无扩展名'}。")
    try:
        wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    except Exception as exc:  # noqa: BLE001
        raise ComposeError("FILE_READ_FAILED", f"无法读取工作簿 {path.name}：{exc}") from exc
    try:
        for ws in wb.worksheets:
            yield ws.title[:31] or "数据", (tuple(row) for row in ws.iter_rows(values_only=True))
    finally:
        wb.close()


def _session_dir(sid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", sid):
        raise ComposeError("SESSION_NOT_FOUND", "智能制表会话不存在。", status=404)
    return SESSION_ROOT / sid


def _manifest_path(sid: str) -> Path:
    return _session_dir(sid) / "manifest.json"


def read_manifest(sid: str) -> dict:
    path = _manifest_path(sid)
    if not path.exists():
        raise ComposeError("SESSION_NOT_FOUND", "智能制表会话不存在或已过期。", status=404)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ComposeError("SESSION_CORRUPT", "智能制表会话状态损坏。", status=500) from exc


def write_manifest(sid: str, manifest: dict) -> None:
    manifest["updated_at"] = _now()
    directory = _session_dir(sid)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    temp = directory / "manifest.json.tmp"
    with _manifest_lock:
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def create_session(sid: str) -> dict:
    cleanup_expired_sessions()
    directory = _session_dir(sid)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "source").mkdir(exist_ok=True)
    (directory / "cache").mkdir(exist_ok=True)
    (directory / "result").mkdir(exist_ok=True)
    (directory / "export").mkdir(exist_ok=True)
    manifest = {
        "schema_version": 1,
        "session_id": sid,
        "status": "UPLOADING",
        "created_at": _now(),
        "updated_at": _now(),
        "files": [],
        "dataset": None,
        "plan": None,
        "facts": [],
        "classification_mapping": {},
        "quality": None,
        "result_sheets": [],
        "logic_id": None,
    }
    write_manifest(sid, manifest)
    return manifest


def cleanup_expired_sessions(max_age_hours: int = 24) -> None:
    """Remove abandoned upload caches without touching active recent sessions."""
    if not SESSION_ROOT.exists():
        return
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    for directory in SESSION_ROOT.iterdir():
        if not directory.is_dir():
            continue
        try:
            manifest_path = directory / "manifest.json"
            timestamp = manifest_path.stat().st_mtime if manifest_path.exists() else directory.stat().st_mtime
            if timestamp < cutoff:
                shutil.rmtree(directory, ignore_errors=True)
        except OSError:
            continue


def add_uploaded_file(sid: str, filename: str, chunks: Iterable[bytes]) -> dict:
    manifest = read_manifest(sid)
    safe_name = Path(filename or "未命名文件").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".xlsx", ".xlsm", ".csv", ".tsv"}:
        raise ComposeError("FILE_UNSUPPORTED", f"暂不支持文件类型：{suffix or '无扩展名'}。")
    stored_name = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    path = _session_dir(sid) / "source" / stored_name
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("wb") as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_UPLOAD_FILE_BYTES:
                    raise ComposeError("FILE_TOO_LARGE", "单个文件超过 512 MB 的安全处理上限，请先拆分文件。", status=413)
                handle.write(chunk)
                digest.update(chunk)
        if size == 0:
            raise ComposeError("FILE_READ_FAILED", f"文件 {safe_name} 为空。")
        existing_size = sum(int(item.get("size_bytes", 0)) for item in manifest.get("files", []))
        if existing_size + size > MAX_UPLOAD_TOTAL_BYTES:
            raise ComposeError("FILE_TOO_LARGE", "本次上传文件合计超过 1 GB，请分批处理。", status=413)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    entry = {"filename": safe_name, "stored_name": stored_name, "size_bytes": size, "sha256": digest.hexdigest()}
    manifest["files"].append(entry)
    write_manifest(sid, manifest)
    return entry


def get_source_preview(sid: str, *, max_sheets: int = 20, max_rows: int = 12) -> dict[str, Any]:
    manifest = read_manifest(sid)
    files = []
    sheet_count = 0
    for file_entry in manifest.get('files', []):
        path = _session_dir(sid) / 'source' / file_entry['stored_name']
        file_preview = {'filename': file_entry['filename'], 'size_bytes': file_entry['size_bytes'], 'sheets': []}
        for sheet_name, raw_rows in _iter_workbook(path):
            if sheet_count >= max_sheets:
                break
            inspected = list(itertools.islice(raw_rows, 50))
            header_idx = _find_header(inspected)
            columns = _unique_headers(inspected[header_idx])
            rows = []
            for raw in inspected[header_idx + 1:]:
                values = list(raw[:len(columns)]) + [None] * max(0, len(columns) - len(raw))
                if _empty_row(values):
                    continue
                rows.append([_safe_json(value) for value in values])
                if len(rows) >= max_rows:
                    break
            file_preview['sheets'].append({
                'name': sheet_name,
                'header_row': header_idx + 1,
                'columns': columns,
                'rows': rows,
                'preview_rows': len(rows),
            })
            sheet_count += 1
        files.append(file_preview)
    return {
        'scope': 'source_preview',
        'files': files,
        'sheet_count': sheet_count,
        'max_rows_per_sheet': max_rows,
        'truncated_sheets': sheet_count >= max_sheets,
    }


def _connect(sid: str) -> sqlite3.Connection:
    path = _session_dir(sid) / "cache" / "source.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _prepare_db(sid: str) -> sqlite3.Connection:
    path = _session_dir(sid) / "cache" / "source.sqlite3"
    if path.exists():
        path.unlink()
    conn = _connect(sid)
    conn.execute("DROP TABLE IF EXISTS source_rows")
    conn.execute(
        "CREATE TABLE source_rows (row_id INTEGER PRIMARY KEY, source_file TEXT NOT NULL, sheet_name TEXT NOT NULL, source_row INTEGER NOT NULL, values_json TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX idx_source_rows_sheet ON source_rows(sheet_name)")
    conn.commit()
    return conn


def _role_score(column: str, samples: list[Any]) -> dict[str, float]:
    text = _normalize(column).lower()
    aliases = {
        "entity_id": ("往来单位编号", "客户编号", "客户编码", "单位编号", "门店编号", "供应商编号", "客户id", "customerid", "编码"),
        "entity_name": ("往来单位名称", "客户名称", "单位名称", "门店名称", "供应商名称", "客户名", "customername"),
        "date": ("交易日期", "开单日期", "业务日期", "日期", "年月", "月份", "date", "time", "时间"),
        "amount": ("含税金额", "金额", "销售额", "销售金额", "价税合计", "收入", "金额合计", "amount", "sales"),
        "transaction_type": ("单据类型", "交易类型", "业务类型", "收支类型", "出入库类型", "单据标识", "类型"),
        "document_id": ("单据编号", "单号", "订单号", "票号", "单据号", "documentid", "orderid"),
        "department": ("部门名称", "部门", "销售部门", "业务部门", "事业部", "department"),
        "salesperson": ("业务员", "销售员", "负责人", "经办人", "salesperson"),
        "region": ("地区", "区域", "省份", "城市", "region"),
    }
    scores = {role: 0.0 for role in aliases}
    for role, words in aliases.items():
        for word in words:
            if text == _normalize(word).lower():
                scores[role] = max(scores[role], 1.0)
            elif _normalize(word).lower() in text:
                scores[role] = max(scores[role], 0.78)
    numeric_rate = 0.0
    nonempty = [_display(v) for v in samples if _display(v) != ""]
    if nonempty:
        numeric_rate = sum(_to_decimal(v) is not None for v in nonempty) / len(nonempty)
    if numeric_rate >= 0.8 and scores["amount"] == 0:
        scores["amount"] = 0.35
    if scores["entity_id"] == 0 and scores["entity_name"] == 0 and nonempty:
        unique_rate = len(set(nonempty)) / len(nonempty)
        if unique_rate > 0.6:
            scores["entity_id"] = 0.2
    return scores


def _new_profile_states(column_count: int) -> list[dict[str, Any]]:
    return [
        {
            "rows": 0,
            "nonempty": 0,
            "numeric": 0,
            "positive": 0,
            "negative": 0,
            "zero": 0,
            "distinct": set(),
            "distinct_truncated": False,
            "top_values": Counter(),
        }
        for _ in range(column_count)
    ]


def _update_profile_states(states: list[dict[str, Any]], values: list[Any]) -> None:
    for index, state in enumerate(states):
        state["rows"] += 1
        value = values[index] if index < len(values) else None
        text = _display(value)
        if not text:
            continue
        state["nonempty"] += 1
        numeric = _to_decimal(value)
        if numeric is not None:
            state["numeric"] += 1
            if numeric > 0:
                state["positive"] += 1
            elif numeric < 0:
                state["negative"] += 1
            else:
                state["zero"] += 1
        distinct: set[str] = state["distinct"]
        if text in distinct:
            pass
        elif len(distinct) < MAX_PROFILE_DISTINCT:
            distinct.add(text)
        else:
            state["distinct_truncated"] = True
        top_values: Counter = state["top_values"]
        if text in top_values or len(top_values) < MAX_PROFILE_TOP_VALUES:
            top_values[text] += 1


def _finalize_profile_states(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finalized = []
    for state in states:
        rows = int(state["rows"])
        nonempty = int(state["nonempty"])
        finalized.append({
            "rows": rows,
            "nonempty_count": nonempty,
            "null_count": rows - nonempty,
            "numeric_count": int(state["numeric"]),
            "positive_count": int(state["positive"]),
            "negative_count": int(state["negative"]),
            "zero_count": int(state["zero"]),
            "unique_count": len(state["distinct"]),
            "unique_count_exact": not bool(state["distinct_truncated"]),
            "top_values": [
                {"value": value, "count": count}
                for value, count in state["top_values"].most_common(8)
            ],
        })
    return finalized


def _sample_date_rate(values: list[Any]) -> float:
    nonempty = [value for value in values if _display(value)]
    if not nonempty:
        return 0.0
    matched = 0
    for value in nonempty:
        if isinstance(value, (date, datetime)):
            matched += 1
            continue
        text = _display(value)
        if re.fullmatch(r"\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?", text):
            matched += 1
    return matched / len(nonempty)


def _profile_fields(
    columns: list[str],
    samples: list[list[Any]],
    full_stats: list[dict[str, Any]] | None = None,
) -> list[dict]:
    fields = []
    for idx, column in enumerate(columns):
        values = [row[idx] if idx < len(row) else None for row in samples]
        nonempty = [_display(v) for v in values if _display(v) != ""]
        scores = _role_score(column, values)
        role, confidence = max(scores.items(), key=lambda pair: pair[1])
        if confidence < 0.5:
            role = "dimension"
            confidence = 0.0
        examples = []
        for value in values:
            safe = _safe_json(value)
            if safe not in examples and safe not in (None, ""):
                examples.append(safe)
            if len(examples) == 5:
                break
        stats = full_stats[idx] if full_stats and idx < len(full_stats) else {
            "rows": len(values),
            "nonempty_count": len(nonempty),
            "null_count": len(values) - len(nonempty),
            "numeric_count": sum(_to_decimal(value) is not None for value in values if _display(value)),
            "positive_count": 0,
            "negative_count": 0,
            "zero_count": 0,
            "unique_count": len(set(nonempty)),
            "unique_count_exact": True,
            "top_values": [],
        }
        nonempty_count = max(int(stats.get("nonempty_count", 0)), 1)
        numeric_rate = int(stats.get("numeric_count", 0)) / nonempty_count
        date_rate = _sample_date_rate(values)
        if numeric_rate >= 0.95:
            data_type = "number"
        elif date_rate >= 0.8 or role == "date":
            data_type = "date"
        elif numeric_rate >= 0.1:
            data_type = "mixed"
        else:
            data_type = "text"
        warnings = []
        if data_type == "mixed":
            warnings.append("同一列包含数字与文本，参与计算前需要确认。")
        if not stats.get("unique_count_exact", True):
            warnings.append(f"唯一值超过 {MAX_PROFILE_DISTINCT:,} 个，已停止精确计数。")
        fields.append({
            "name": column,
            "role": role,
            "confidence": round(confidence, 3),
            "data_type": data_type,
            "null_rate": round(int(stats.get("null_count", 0)) / max(int(stats.get("rows", len(values))), 1), 4),
            "nonempty_count": int(stats.get("nonempty_count", len(nonempty))),
            "numeric_count": int(stats.get("numeric_count", 0)),
            "positive_count": int(stats.get("positive_count", 0)),
            "negative_count": int(stats.get("negative_count", 0)),
            "zero_count": int(stats.get("zero_count", 0)),
            "unique_count": int(stats.get("unique_count", len(set(nonempty)))),
            "unique_count_exact": bool(stats.get("unique_count_exact", True)),
            "top_values": list(stats.get("top_values", [])),
            "examples": examples,
            "sample_rows": len(values),
            "profile_scope": "full" if full_stats else "sample",
            "warnings": warnings,
        })
    return fields


def _relationship_evidence(sid: str, dataset: dict) -> tuple[dict[str, Any], list[list[Any]]]:
    """Calculate full-data evidence used by planning without exposing every row to the model."""
    columns = dataset.get("schema", {}).get("columns", [])
    col_index = {name: index for index, name in enumerate(columns)}
    bindings = _role_bindings(dataset)
    amount_index = col_index.get(bindings.get("amount", ""))
    transaction_index = col_index.get(bindings.get("transaction_type", ""))
    entity_name_index = col_index.get(bindings.get("entity_name", ""))
    entity_id_index = col_index.get(bindings.get("entity_id", ""))
    dimension_indexes = {
        field["name"]: col_index[field["name"]]
        for field in dataset.get("fields", [])
        if field.get("role") in {"dimension", "department", "salesperson", "region"}
        and field.get("name") in col_index
    }
    sign_counts: dict[str, Counter] = defaultdict(Counter)
    comparison_counts: dict[str, Counter] = defaultdict(Counter)
    representative: dict[str, list[list[Any]]] = defaultdict(list)
    zero_rows = nonzero_rows = 0
    conn = _connect(sid)
    try:
        for (raw_json,) in conn.execute("SELECT values_json FROM source_rows ORDER BY row_id"):
            values = json.loads(raw_json)
            amount = _to_decimal(values[amount_index]) if amount_index is not None and amount_index < len(values) else None
            sign = "invalid"
            if amount is not None:
                if amount > 0:
                    sign = "positive"
                    nonzero_rows += 1
                elif amount < 0:
                    sign = "negative"
                    nonzero_rows += 1
                else:
                    sign = "zero"
                    zero_rows += 1
                if len(representative[sign]) < 12:
                    representative[sign].append(values)
            if transaction_index is not None and transaction_index < len(values):
                code = _display(values[transaction_index])
                if code and (code in sign_counts or len(sign_counts) < 200):
                    sign_counts[code][sign] += 1
            entity_name = _display(values[entity_name_index]) if entity_name_index is not None and entity_name_index < len(values) else ""
            entity_id = _display(values[entity_id_index]) if entity_id_index is not None and entity_id_index < len(values) else ""
            for field, index in dimension_indexes.items():
                value = _display(values[index]) if index < len(values) else ""
                if not value:
                    continue
                comparison_counts[field]["nonempty"] += 1
                if entity_name and value == entity_name:
                    comparison_counts[field]["same_as_entity_name"] += 1
                if entity_id and value == entity_id:
                    comparison_counts[field]["same_as_entity_id"] += 1
    finally:
        conn.close()

    mappings = []
    explained_nonzero = 0
    sales_values = []
    return_values = []
    for value, counts in sorted(sign_counts.items(), key=lambda item: (-sum(item[1].values()), item[0])):
        positive = int(counts.get("positive", 0))
        negative = int(counts.get("negative", 0))
        zero = int(counts.get("zero", 0))
        total_nonzero = positive + negative
        majority = max(positive, negative)
        explained_nonzero += majority
        confidence = majority / total_nonzero if total_nonzero else 0.0
        suggested = "销售" if positive > negative else "退货" if negative > positive else "待确认"
        if total_nonzero >= 3 and confidence >= 0.995:
            if positive > negative:
                sales_values.append(value)
            elif negative > positive:
                return_values.append(value)
        mappings.append({
            "value": value,
            "positive_rows": positive,
            "negative_rows": negative,
            "zero_rows": zero,
            "suggested": suggested,
            "confidence": round(confidence, 4),
        })
    agreement_rate = explained_nonzero / nonzero_rows if nonzero_rows else 0.0
    transaction = {
        "transaction_field": bindings.get("transaction_type"),
        "amount_field": bindings.get("amount"),
        "nonzero_rows": nonzero_rows,
        "zero_rows": zero_rows,
        "agreement_rate": round(agreement_rate, 6),
        "sales_values": sales_values,
        "return_values": return_values,
        "mappings": mappings[:20],
        "supports_amount_sign": bool(bindings.get("amount")) and nonzero_rows > 0,
        "supports_code_mapping": bool(sales_values and return_values and agreement_rate >= 0.995),
    }
    relationships = {}
    for field, counts in comparison_counts.items():
        nonempty = int(counts.get("nonempty", 0))
        relationships[field] = {
            "same_as_entity_name_rate": round(int(counts.get("same_as_entity_name", 0)) / nonempty, 4) if nonempty else 0.0,
            "same_as_entity_id_rate": round(int(counts.get("same_as_entity_id", 0)) / nonempty, 4) if nonempty else 0.0,
        }
    sample_rows = []
    for bucket in ("positive", "negative", "zero", "invalid"):
        sample_rows.extend(representative.get(bucket, []))
    return {"transaction": transaction, "fields": relationships}, sample_rows


def _dataset_readiness(dataset: dict) -> dict[str, Any]:
    fields = dataset.get("fields", [])
    bindings = _role_bindings(dataset)
    reasons = []
    warnings = []
    level = 1
    if not fields:
        return {"level": 4, "label": "当前不支持", "reasons": ["没有识别到可用字段。"], "warnings": []}
    if not any(field.get("data_type") == "number" for field in fields):
        level = 2
        reasons.append("没有高置信度数值字段，当前只能推荐计数或明细整理方案。")
    transaction = dataset.get("evidence", {}).get("transaction", {})
    if bindings.get("transaction_type") and transaction.get("supports_amount_sign"):
        level = max(level, 2)
        reasons.append("销售与退货口径会改变结果，需要用户确认后执行。")
    if int(transaction.get("zero_rows", 0)) > 0:
        level = max(level, 2)
        reasons.append(f"发现 {int(transaction['zero_rows']):,} 条零金额记录，需要确认是否计入单据数。")
    mixed_fields = [field["name"] for field in fields if field.get("data_type") == "mixed"]
    if mixed_fields:
        warnings.append("混合类型字段：" + "、".join(mixed_fields[:6]))
    return {
        "level": level,
        "label": {1: "可直接生成", 2: "确认后生成", 3: "需要整理源表", 4: "当前不支持"}[level],
        "reasons": reasons or ["已识别出可执行的字段、维度和指标。"],
        "warnings": warnings,
    }


def _modeling_preflight(dataset: dict) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    total_rows = max(int(dataset.get('total_rows', 0)), 1)
    candidate_keys = []
    categorical_fields = []
    numeric_fields = []
    date_fields = []
    findings = []
    for field in dataset.get('fields', []):
        name = field.get('name')
        data_type = field.get('data_type')
        nonempty = int(field.get('nonempty_count', 0))
        unique = int(field.get('unique_count', 0))
        null_rate = float(field.get('null_rate', 0))
        if nonempty == 0:
            findings.append({'code': 'EMPTY_COLUMN', 'severity': 'warning', 'field': name, 'message': f'字段「{name}」全为空。'})
        elif data_type == 'mixed':
            findings.append({'code': 'MIXED_TYPE', 'severity': 'plan_related', 'field': name, 'message': f'字段「{name}」同时包含数字和文本，参与公式前需要确认。'})
        elif null_rate >= 0.5:
            findings.append({'code': 'HIGH_NULL_RATE', 'severity': 'warning', 'field': name, 'message': f'字段「{name}」空值率为 {null_rate * 100:.1f}%。'})
        if field.get('unique_count_exact') and nonempty == total_rows and unique == total_rows:
            candidate_keys.append({'fields': [name], 'coverage': 1.0, 'uniqueness': 1.0})
        if data_type == 'number':
            numeric_fields.append(name)
        elif data_type == 'date':
            date_fields.append(name)
        elif 1 < unique <= min(200, total_rows):
            categorical_fields.append({'field': name, 'unique_count': unique})
    schema_count = len({item.get('signature') for item in dataset.get('sheets', []) if item.get('signature')})
    if schema_count > 1:
        findings.insert(0, {
            'code': 'HETEROGENEOUS_SHEETS',
            'severity': 'plan_related',
            'message': f'源文件包含 {schema_count} 种工作表结构。系统已按字段名安全对齐；如果目标需要跨表关联，必须确认关联键和关系。',
        })
    evidence = {
        'candidate_keys': candidate_keys[:12],
        'numeric_fields': numeric_fields,
        'date_fields': date_fields,
        'categorical_fields': categorical_fields[:30],
    }
    return evidence, findings


def _fallback_directions(dataset: dict) -> list[dict]:
    fields = dataset.get("fields", [])
    bindings = _role_bindings(dataset)
    total_rows = int(dataset.get("total_rows", 0))
    field_map = {field["name"]: field for field in fields}
    relationships = dataset.get("evidence", {}).get("fields", {})
    excluded = {
        bindings.get("entity_id"), bindings.get("entity_name"), bindings.get("amount"),
        bindings.get("date"), bindings.get("transaction_type"), bindings.get("document_id"),
    }
    dimensions = []
    for field in fields:
        name = field["name"]
        if name in excluded or field.get("data_type") in {"number", "date", "mixed"}:
            continue
        unique_count = int(field.get("unique_count", 0))
        if unique_count <= 1:
            continue
        top_values = list(field.get("top_values", []))
        top_total = sum(int(item.get("count", 0)) for item in top_values)
        permission_rate = (
            sum(int(item.get("count", 0)) for item in top_values if "权限" in _display(item.get("value"))) / top_total
            if top_total else 0.0
        )
        if permission_rate >= 0.5:
            continue
        relation = relationships.get(name, {})
        same_as_name = float(relation.get("same_as_entity_name_rate", 0))
        if same_as_name >= 0.5:
            continue
        cardinality_rate = unique_count / max(total_rows, 1)
        if cardinality_rate > 0.5 and field.get("role") == "dimension":
            continue
        score = 0.82 if field.get("role") in {"department", "salesperson", "region"} else 0.64
        score -= min(cardinality_rate, 0.5)
        dimensions.append((score, field))
    dimensions.sort(key=lambda item: (-item[0], int(item[1].get("unique_count", 0)), item[1]["name"]))
    numeric_fields = [
        field for field in fields
        if field.get("data_type") == "number"
        and field["name"] not in {bindings.get("entity_id"), bindings.get("document_id")}
    ]
    numeric_fields.sort(key=lambda field: (
        0 if field["name"] == bindings.get("amount") else 1,
        -int(field.get("nonempty_count", 0)),
        field["name"],
    ))
    measure_specs = [
        {"field": field["name"], "aggregation": "sum", "name": f"{field['name']}合计"}
        for field in numeric_fields[:3]
    ]
    directions: list[dict[str, Any]] = []

    def add(
        title: str,
        description: str,
        output_kind: str,
        *,
        direction_dimensions: list[str] | None = None,
        time_grain: str | None = None,
        outputs: list[str] | None = None,
        confidence: float = 0.75,
        classification_requested: bool = False,
        measures: list[dict[str, str]] | None = None,
        evidence: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        hint = {
            "output_kind": output_kind,
            "dimensions": direction_dimensions or [],
            "time_grain": time_grain,
            "classification_requested": classification_requested,
            "measures": measures or [],
        }
        signature = hashlib.sha256(_json_dumps(hint).encode()).hexdigest()[:10]
        grain_parts = []
        if time_grain:
            grain_parts.append({"year": "每年", "quarter": "每季度", "month": "每月"}[time_grain])
        if direction_dimensions:
            grain_parts.append(" + ".join(direction_dimensions))
        elif output_kind in {"customer_sales", "classification"}:
            grain_parts.append("每个客户或往来单位")
        else:
            grain_parts.append("全部数据")
        directions.append({
            "id": f"data_{signature}",
            "title": title,
            "description": description,
            "evidence": list(dict.fromkeys(evidence or []))[:8],
            "evidence_notes": [],
            "outputs": outputs or ["目标汇总表"],
            "recommended": False,
            "confidence": round(max(0.0, min(confidence, 0.99)), 3),
            "grain_label": "；".join(grain_parts),
            "estimated_rows": None,
            "warnings": warnings or [],
            "plan_hint": hint,
        })

    sales_like = bool(bindings.get("amount")) and field_map.get(bindings.get("amount"), {}).get("role") == "amount"
    entity_id = bindings.get("entity_id") or bindings.get("entity_name")
    entity_name = bindings.get("entity_name")
    if sales_like and entity_id:
        evidence = [name for name in (entity_id, entity_name, bindings.get("amount"), bindings.get("document_id")) if name]
        add(
            "按客户或往来单位汇总",
            "每个稳定客户一行，汇总销售、退货、净额和去重单据数。",
            "customer_sales",
            outputs=["客户销售汇总"],
            confidence=0.94,
            evidence=evidence,
        )
        if bindings.get("date"):
            add(
                "按客户和月份汇总",
                "同一客户按月份拆行，适合制作客户月度明细汇总表。",
                "customer_sales",
                time_grain="month",
                outputs=["客户月度汇总"],
                confidence=0.88,
                evidence=[bindings["date"], *evidence],
            )
        if entity_name:
            add(
                "客户汇总并设计分类",
                "先生成客户汇总，再由用户确认分类依据；未命中记录单独保留。",
                "classification",
                outputs=["客户销售汇总", "分类汇总", "待确认分类"],
                confidence=0.81,
                classification_requested=True,
                evidence=[entity_id, entity_name, bindings.get("amount")],
                warnings=["分类名称和规则需要用户确认，系统不会自动编造。"],
            )
    if measure_specs:
        for dimension_score, field in dimensions[:2]:
            name = field["name"]
            add(
                f"按{name}汇总",
                f"每个「{name}」一行，汇总可计算的数值字段并保留记录数。",
                "generic_summary",
                direction_dimensions=[name],
                outputs=[f"{name}汇总"],
                confidence=min(0.91, 0.68 + dimension_score / 4),
                measures=measure_specs,
                evidence=[name, *[item["field"] for item in measure_specs]],
            )
        if bindings.get("date"):
            add(
                "按月份汇总数值指标",
                "按数据中的日期字段形成月份行，并汇总主要数值指标。",
                "generic_summary",
                time_grain="month",
                outputs=["月度指标汇总"],
                confidence=0.78,
                measures=measure_specs,
                evidence=[bindings["date"], *[item["field"] for item in measure_specs]],
            )
    elif dimensions:
        name = dimensions[0][1]["name"]
        add(
            f"统计{name}分布",
            f"按「{name}」分组统计记录数，适合没有数值指标的数据表。",
            "generic_summary",
            direction_dimensions=[name],
            outputs=[f"{name}分布"],
            confidence=0.72,
            evidence=[name],
        )
    if not directions:
        add(
            "整理为结构化明细",
            "保留已识别字段并统计有效记录数，后续可继续描述目标列和筛选规则。",
            "generic_summary",
            outputs=["结构化数据概况"],
            confidence=0.55,
            evidence=[field["name"] for field in fields[:6]],
            warnings=["当前没有识别到稳定分组维度，建议补充目标表要求。"],
        )
    directions.sort(key=lambda item: (-float(item.get("confidence", 0)), item["id"]))
    return directions[:6]


def _string_items(value: Any, *, limit: int) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [_display(item) for item in values if isinstance(item, str) and _display(item)][:limit]


def _extract_direction_table(raw: dict) -> dict | None:
    hint = raw.get('plan_hint') if isinstance(raw.get('plan_hint'), dict) else {}
    if isinstance(hint.get('table'), dict):
        return hint['table']
    if isinstance(raw.get('table'), dict):
        return raw['table']
    outputs = raw.get('outputs')
    candidates = outputs if isinstance(outputs, list) else [outputs]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('table'), dict):
            return item['table']
        if item.get('mode') in {'detail', 'aggregate'}:
            return item
    return None


def _normalize_transaction_summary(table: dict, dataset: dict) -> list[str]:
    evidence = dataset.get('evidence', {}).get('transaction', {})
    if not evidence.get('supports_amount_sign') or float(evidence.get('agreement_rate', 0)) < 0.98:
        return []
    transaction_field = evidence.get('transaction_field')
    amount_field = evidence.get('amount_field')
    sales_values = {_display(value) for value in evidence.get('sales_values', [])}
    return_values = {_display(value) for value in evidence.get('return_values', [])}
    if not transaction_field or not amount_field or not sales_values or not return_values:
        return []

    signed_sales: set[str] = set()
    signed_returns: set[str] = set()
    for item in table.get('aggregations', []) if isinstance(table.get('aggregations'), list) else []:
        if not isinstance(item, dict) or item.get('aggregation') != 'sum':
            continue
        expression = item.get('expression')
        if not isinstance(expression, dict) or expression.get('op') != 'if':
            continue
        condition = expression.get('condition')
        then = expression.get('then')
        otherwise = expression.get('else')
        if (
            not isinstance(condition, dict)
            or condition.get('field') != transaction_field
            or condition.get('operator') not in {'equals', 'in'}
            or not isinstance(then, dict)
            or then.get('op') != 'field'
            or then.get('field') != amount_field
            or not isinstance(otherwise, dict)
            or otherwise.get('op') != 'literal'
            or _to_decimal(otherwise.get('value')) != 0
        ):
            continue
        values = condition.get('value') if condition.get('operator') == 'in' else [condition.get('value')]
        normalized_values = {_display(value) for value in values or []}
        name = _display(item.get('name'))
        if normalized_values and normalized_values.issubset(sales_values):
            signed_sales.add(name)
        if normalized_values and normalized_values.issubset(return_values):
            signed_returns.add(name)

    notes = []
    for column in table.get('columns', []) if isinstance(table.get('columns'), list) else []:
        if not isinstance(column, dict) or not isinstance(column.get('expression'), dict):
            continue
        name = _display(column.get('name'))
        expression = column['expression']
        if expression.get('op') == 'field' and expression.get('field') in signed_returns and ('退货' in name or 'return' in name.casefold()):
            column['expression'] = {'op': 'abs', 'args': [expression]}
            notes.append('退货金额已依据全量正负号证据转为正数展示。')
            continue
        is_net = any(token in name.casefold() for token in ('净额', '净销售', '净交易', 'net'))
        args = expression.get('args') if expression.get('op') == 'subtract' else None
        if not is_net or not isinstance(args, list) or len(args) != 2:
            continue
        left = args[0].get('field') if isinstance(args[0], dict) and args[0].get('op') == 'field' else None
        right = args[1].get('field') if isinstance(args[1], dict) and args[1].get('op') == 'field' else None
        if left in signed_sales and right in signed_returns:
            column['expression'] = {'op': 'add', 'args': args}
            notes.append('净额已依据退货为负数的全量证据改为销售额加退货额。')
    return list(dict.fromkeys(notes))


def _sanitize_table_direction(
    raw: dict,
    title: str,
    description: str,
    dataset: dict,
    validation_errors: list[str] | None = None,
) -> dict | None:
    source_columns = list(dataset.get('schema', {}).get('columns', []))
    columns = set(source_columns)
    table = compose_plan.normalize_model_table_spec(_extract_direction_table(raw), source_columns)
    if not isinstance(table, dict):
        if validation_errors is not None:
            validation_errors.append(f'{title or "未命名方向"}：缺少完整 table 计划。')
        return None
    table['output_name'] = _display(table.get('output_name'))[:31] or title[:31] or '目标表'
    normalization_notes = _normalize_transaction_summary(table, dataset)
    try:
        compose_plan.validate_table_spec(table, list(columns))
    except compose_plan.TablePlanError as exc:
        if validation_errors is not None:
            validation_errors.append(f'{title or "未命名方向"}：{exc.message}（{exc.path}）')
        return None
    outputs = [table['output_name']]
    signature = hashlib.sha256(_json_dumps(table).encode()).hexdigest()[:10]
    raw_evidence = _string_items(raw.get('evidence'), limit=20)
    if isinstance(raw.get('evidence'), str):
        raw_evidence.extend(name for name in source_columns if name in raw['evidence'])
    referenced = compose_plan.referenced_fields(table)
    evidence = list(dict.fromkeys([
        *[name for name in raw_evidence if name in columns],
        *[name for name in source_columns if name in referenced],
    ]))[:8]
    estimated_rows = int(dataset.get('total_rows', 0)) if table.get('mode') == 'detail' and not table.get('filters') else None
    groups = [item.get('name') or item.get('field') for item in table.get('group_by', [])]
    grain_label = '、'.join(groups)
    if not grain_label:
        grain_label = '全部数据一行' if table.get('mode') == 'aggregate' else '保留符合条件的每条记录'
    return {
        'id': f'ai_{signature}', 'title': title, 'description': description,
        'evidence': evidence, 'evidence_notes': [], 'outputs': outputs,
        'recommended': bool(raw.get('recommended')), 'confidence': None,
        'assessment': {'data_evidence': 'strong' if len(evidence) >= 2 else 'limited', 'executability': 'executable', 'business_fit': 'pending_confirmation'},
        'grain_label': grain_label, 'estimated_rows': estimated_rows,
        'warnings': [
            *[value[:160] for value in _string_items(raw.get('warnings'), limit=4)],
            *normalization_notes,
        ][:6],
        'plan_hint': {'output_kind': 'table', 'table': table},
    }


def _sanitize_model_table_candidate(
    raw: dict,
    index: int,
    dataset: dict,
    validation_errors: list[str],
) -> dict | None:
    if not isinstance(raw, dict):
        validation_errors.append(f'第 {index + 1} 个方向不是 JSON 对象。')
        return None
    title = _display(raw.get('title'))[:40]
    description = _display(raw.get('description'))[:180]
    if not title or not description:
        validation_errors.append(f'第 {index + 1} 个方向缺少标题或说明。')
        return None
    if any(word in title for word in ('趋势', '原因', '预测', '经营建议', '可视化')):
        validation_errors.append(f'{title}：属于数据分析而非目标表制表方向。')
        return None
    return _sanitize_table_direction(raw, title, description, dataset, validation_errors)


def _sanitize_direction(raw: dict, index: int, dataset: dict) -> dict | None:
    columns = set(dataset.get("schema", {}).get("columns", []))
    if not isinstance(raw, dict):
        return None
    if _extract_direction_table(raw) is not None:
        table_title = _display(raw.get('title'))[:40]
        table_description = _display(raw.get('description'))[:180]
        if not table_title or not table_description:
            return None
        return _sanitize_table_direction(raw, table_title, table_description, dataset)
    title = _display(raw.get("title"))[:40]
    description = _display(raw.get("description"))[:180]
    if not title or not description or any(word in title for word in ("趋势", "原因", "预测", "经营建议", "可视化")):
        return None
    hint = raw.get("plan_hint") if isinstance(raw.get("plan_hint"), dict) else {}
    output_kind = hint.get("output_kind")
    if output_kind not in {"customer_sales", "dimension_summary", "classification", "generic_summary"}:
        output_kind = "generic_summary"
    dimensions = [name for name in hint.get("dimensions", []) if name in columns][:5]
    bindings = _role_bindings(dataset)
    if output_kind in {"customer_sales", "classification"}:
        dimensions = [name for name in dimensions if name not in {bindings.get("entity_id"), bindings.get("entity_name")}]
    relationships = dataset.get("evidence", {}).get("fields", {})
    unsafe_dimensions = []
    for name in dimensions:
        field = next((field for field in dataset.get("fields", []) if field["name"] == name), {})
        top_values = list(field.get("top_values", []))
        top_total = sum(int(item.get("count", 0)) for item in top_values)
        permission_rate = (
            sum(int(item.get("count", 0)) for item in top_values if "权限" in _display(item.get("value"))) / top_total
            if top_total else 0.0
        )
        if float(relationships.get(name, {}).get("same_as_entity_name_rate", 0)) >= 0.5 or permission_rate >= 0.5:
            unsafe_dimensions.append(name)
    dimensions = [name for name in dimensions if name not in unsafe_dimensions]
    time_grain = hint.get("time_grain") if hint.get("time_grain") in {None, "year", "quarter", "month"} else None
    numeric_fields = {
        field["name"] for field in dataset.get("fields", [])
        if field.get("data_type") == "number"
    }
    measures = []
    for item in hint.get("measures", []) if isinstance(hint.get("measures"), list) else []:
        if not isinstance(item, dict) or item.get("field") not in numeric_fields:
            continue
        aggregation = item.get("aggregation") if item.get("aggregation") in {"sum", "average", "min", "max"} else "sum"
        measures.append({
            "field": item["field"],
            "aggregation": aggregation,
            "name": _display(item.get("name"))[:60] or f"{item['field']}合计",
        })
    if output_kind == "generic_summary" and not measures:
        measures = [
            {"field": field["name"], "aggregation": "sum", "name": f"{field['name']}合计"}
            for field in dataset.get("fields", [])
            if field.get("data_type") == "number"
            and field["name"] not in {bindings.get("entity_id"), bindings.get("document_id")}
        ][:3]
    outputs = [_display(value)[:31] for value in raw.get("outputs", []) if _display(value)][:4]
    if not outputs:
        outputs = ["目标汇总表"]
    if output_kind == "classification":
        output_names = {
            "customer_sales": outputs[0],
            "classification_summary": outputs[1] if len(outputs) > 1 else "分类汇总",
            "pending_classification": outputs[2] if len(outputs) > 2 else "待确认分类",
        }
    elif output_kind == "dimension_summary":
        output_names = {"dimension_summary": outputs[0]}
    elif output_kind == "generic_summary":
        output_names = {"generic_summary": outputs[0]}
    else:
        output_names = {"customer_sales": outputs[0]}
    plan_hint = {
        "output_kind": output_kind,
        "dimensions": dimensions,
        "time_grain": time_grain,
        "classification_requested": bool(hint.get("classification_requested")) or output_kind == "classification",
        "output_names": output_names,
        "measures": measures,
    }
    signature = hashlib.sha256(_json_dumps(plan_hint).encode()).hexdigest()[:10]
    estimated_rows = None
    if output_kind in {"customer_sales", "classification"}:
        entity_field = next((field for field in dataset.get("fields", []) if field["name"] == bindings.get("entity_id")), None)
        if entity_field and entity_field.get("unique_count_exact") and not time_grain:
            estimated_rows = int(entity_field.get("unique_count", 0))
    elif len(dimensions) == 1 and not time_grain:
        dimension_field = next((field for field in dataset.get("fields", []) if field["name"] == dimensions[0]), None)
        if dimension_field and dimension_field.get("unique_count_exact"):
            estimated_rows = int(dimension_field.get("unique_count", 0))
    return {
        "id": f"ai_{signature}",
        "title": title,
        "description": description,
        "evidence": [name for name in raw.get("evidence", []) if name in columns][:8],
        "evidence_notes": [],
        "outputs": outputs,
        "recommended": bool(raw.get("recommended")),
        "confidence": round(max(0.45, min(float(raw.get("confidence", 0.72) or 0.72), 0.95)), 3),
        "grain_label": ({"year": "每年", "quarter": "每季度", "month": "每月"}.get(time_grain, "") + ("；" if time_grain and dimensions else "") + (" + ".join(dimensions) if dimensions else "每个客户或往来单位" if output_kind in {"customer_sales", "classification"} else "全部数据")),
        "estimated_rows": estimated_rows,
        "warnings": [f"已排除与客户名称高度重复的维度：{'、'.join(unsafe_dimensions)}"] if unsafe_dimensions else [],
        "plan_hint": plan_hint,
    }


def _make_directions_legacy(dataset: dict, model_config: dict | None = None) -> tuple[list[dict], dict]:
    fallback = _fallback_directions(dataset)
    raw_directions, error = compose_ai.generate_directions(dataset, model_config)
    generated = [
        direction for index, raw in enumerate(raw_directions or [])
        if (direction := _sanitize_direction(raw, index, dataset)) is not None
    ]
    directions = list(fallback[:3])
    signatures = {_json_dumps(item.get("plan_hint", {})) for item in directions}
    for item in [*generated, *fallback[3:]]:
        signature = _json_dumps(item.get("plan_hint", {}))
        if signature in signatures or len(directions) >= 6:
            continue
        signatures.add(signature)
        directions.append(item)
    # The highest-scoring data-derived candidate remains stable for the same
    # schema/profile. Model-only additions may enrich the list but cannot make
    # the default selection fluctuate between runs.
    recommended_id = fallback[0]["id"] if fallback else (directions[0]["id"] if directions else "")
    recommended_index = next((index for index, item in enumerate(directions) if item["id"] == recommended_id), 0)
    for index, item in enumerate(directions):
        item["recommended"] = index == recommended_index
        if item.get("estimated_rows") is None:
            hint = item.get("plan_hint", {})
            dimensions = hint.get("dimensions", [])
            if len(dimensions) == 1 and not hint.get("time_grain"):
                field = next((field for field in dataset.get("fields", []) if field["name"] == dimensions[0]), None)
                if field and field.get("unique_count_exact"):
                    item["estimated_rows"] = int(field.get("unique_count", 0))
        evidence_notes = item.setdefault("evidence_notes", [])
        for field_name in item.get("evidence", [])[:4]:
            field = next((field for field in dataset.get("fields", []) if field["name"] == field_name), None)
            if field:
                unique = f"{int(field.get('unique_count', 0)):,}" + ("+" if not field.get("unique_count_exact", True) else "")
                evidence_notes.append(f"{field_name}：{field.get('data_type', '未知类型')}，{unique} 个唯一值")
    planning = {
        "source": "hybrid" if generated else "local",
        "model_configured": bool((model_config or {}).get("api_key")),
        "notice": error,
    }
    return directions[:6], planning


def _direction_duplicate_reason(candidate: dict, accepted: list[dict]) -> str:
    table = (candidate.get('plan_hint') or {}).get('table') or {}
    exact = _json_dumps(table)
    mode = table.get('mode')
    filters = _json_dumps(table.get('filters', []))
    columns = _json_dumps([
        item.get('expression') for item in table.get('columns', []) if isinstance(item, dict)
    ])
    aggregations = _json_dumps([
        {
            'aggregation': item.get('aggregation'),
            'expression': item.get('expression'),
        }
        for item in table.get('aggregations', []) if isinstance(item, dict)
    ])
    group_count = len(table.get('group_by', [])) if isinstance(table.get('group_by'), list) else 0
    for item in accepted:
        other = (item.get('plan_hint') or {}).get('table') or {}
        if exact == _json_dumps(other):
            return f'{candidate.get("title")}：与「{item.get("title")}」执行计划完全相同。'
        if mode != other.get('mode') or filters != _json_dumps(other.get('filters', [])):
            continue
        other_columns = _json_dumps([
            value.get('expression') for value in other.get('columns', []) if isinstance(value, dict)
        ])
        if mode == 'detail' and columns == other_columns:
            return f'{candidate.get("title")}：与「{item.get("title")}」是相同范围和字段的明细表。'
        other_aggregations = _json_dumps([
            {
                'aggregation': value.get('aggregation'),
                'expression': value.get('expression'),
            }
            for value in other.get('aggregations', []) if isinstance(value, dict)
        ])
        other_group_count = len(other.get('group_by', [])) if isinstance(other.get('group_by'), list) else 0
        if (
            mode == 'aggregate'
            and aggregations == other_aggregations
            and columns == other_columns
            and group_count == other_group_count
        ):
            return f'{candidate.get("title")}：与「{item.get("title")}」仅更换了分组字段。'
    return ''


def _append_unique_directions(
    accepted: list[dict],
    candidates: list[dict],
    validation_errors: list[str],
    *,
    limit: int = 3,
) -> None:
    for candidate in candidates:
        if len(accepted) >= limit:
            return
        reason = _direction_duplicate_reason(candidate, accepted)
        if reason:
            validation_errors.append(reason)
            continue
        accepted.append(candidate)


def _make_directions(dataset: dict, model_config: dict | None = None) -> tuple[list[dict], dict]:
    dataset['modeling_evidence'], dataset['quality_findings'] = _modeling_preflight(dataset)
    fallback = _basic_table_directions(dataset)
    raw_directions, error = compose_ai.generate_directions(dataset, model_config)
    validation_errors: list[str] = []
    initial_candidates = [
        direction for index, raw in enumerate(raw_directions or [])
        if (direction := _sanitize_model_table_candidate(raw, index, dataset, validation_errors)) is not None
    ]
    directions: list[dict] = []
    _append_unique_directions(directions, initial_candidates, validation_errors)

    supplement_error = ''
    supplemented = False
    model_configured = bool((model_config or {}).get('api_key'))
    if model_configured and raw_directions is not None and len(directions) < 3:
        supplemental_raw, supplement_error = compose_ai.supplement_directions(
            dataset,
            directions,
            validation_errors,
            3 - len(directions),
            model_config,
        )
        supplemental_candidates = [
            direction for index, raw in enumerate(supplemental_raw or [])
            if (direction := _sanitize_model_table_candidate(raw, index, dataset, validation_errors)) is not None
        ]
        before = len(directions)
        _append_unique_directions(directions, supplemental_candidates, validation_errors)
        supplemented = len(directions) > before

    model_direction_count = len(directions)
    _append_unique_directions(directions, fallback, validation_errors)
    requested_recommendation = next((index for index, item in enumerate(directions) if item.get('recommended')), 0)
    for index, item in enumerate(directions):
        item['recommended'] = index == requested_recommendation
        item['confidence'] = None
        item.setdefault('assessment', {
            'data_evidence': 'strong' if len(item.get('evidence', [])) >= 2 else 'limited',
            'executability': 'executable',
            'business_fit': 'pending_confirmation',
        })
        evidence_notes = item.setdefault('evidence_notes', [])
        for field_name in item.get('evidence', [])[:4]:
            field = next((candidate for candidate in dataset.get('fields', []) if candidate.get('name') == field_name), None)
            if not field:
                continue
            suffix = '' if field.get('unique_count_exact', True) else '以上'
            data_type = field.get('data_type', '未知类型')
            unique_count = int(field.get('unique_count', 0))
            evidence_notes.append(f'{field_name}：{data_type}，{unique_count:,} 个{suffix}唯一值')
    notices = [value for value in (error, supplement_error) if value]
    if validation_errors:
        notices.append(f'已排除 {len(validation_errors)} 个无效或实质重复的候选。')
    if model_direction_count < len(directions):
        notices.append('模型有效方向不足三项，已使用数据画像补足可执行方案。')
    source = 'model' if model_direction_count == len(directions) and model_direction_count > 0 else 'hybrid' if model_direction_count else 'basic'
    planning = {
        'source': source,
        'model_configured': model_configured,
        'notice': ' '.join(dict.fromkeys(notices)),
        'degraded': source != 'model',
        'supplemented': supplemented,
        'accepted_count': len(directions),
        'rejected_count': len(validation_errors),
    }
    return directions[:3], planning


def _basic_table_directions(dataset: dict) -> list[dict]:
    """Return conservative, domain-neutral directions when semantic planning is unavailable."""
    fields = list(dataset.get('fields', []))
    columns = list(dataset.get('schema', {}).get('columns', []))
    directions: list[dict] = []

    def add(title: str, description: str, table: dict, evidence: list[str], warnings: list[str] | None = None) -> None:
        raw = {
            'title': title,
            'description': description,
            'outputs': [table['output_name']],
            'evidence': evidence,
            'warnings': warnings or ['当前为基础模式，业务含义需要你确认或补充。'],
            'recommended': not directions,
            'plan_hint': {'output_kind': 'table', 'table': table},
        }
        direction = _sanitize_table_direction(raw, title, description, dataset)
        if direction:
            directions.append(direction)

    add(
        '整理为结构化明细表',
        '保留源表中的真实字段和每条记录，不主动推断业务口径。',
        {
            'mode': 'detail',
            'output_name': '结构化明细',
            'filters': [],
            'columns': [
                {'name': name, 'expression': {'op': 'field', 'field': name}}
                for name in columns[:compose_plan.MAX_PLAN_COLUMNS]
            ],
            'sort': [],
            'limit': None,
        },
        columns[:6],
    )

    numeric = [field['name'] for field in fields if field.get('data_type') == 'number']
    candidate_key_fields = {
        name
        for candidate in dataset.get('modeling_evidence', {}).get('candidate_keys', [])
        if float(candidate.get('uniqueness', 0)) >= 0.98
        for name in candidate.get('fields', [])
    }
    total_rows = max(1, int(dataset.get('total_rows', 0)))
    categorical = [
        field['name'] for field in fields
        if field.get('data_type') in {'text', 'boolean'}
        and 1 < int(field.get('unique_count', 0)) <= min(2000, max(20, int(dataset.get('total_rows', 0)) // 2))
        and int(field.get('unique_count', 0)) / total_rows <= 0.5
        and field['name'] not in candidate_key_fields
    ]
    if categorical:
        dimension = categorical[0]
        aggregations = [
            {
                'name': f'{name}合计',
                'aggregation': 'sum',
                'expression': {'op': 'field', 'field': name},
            }
            for name in numeric[:3]
        ]
        aggregations.append({'name': '记录数', 'aggregation': 'count'})
        add(
            f'按{dimension}汇总',
            f'按「{dimension}」分组，汇总可计算数值并统计记录数。',
            {
                'mode': 'aggregate',
                'output_name': f'{dimension}汇总',
                'filters': [],
                'group_by': [{'field': dimension, 'name': dimension}],
                'aggregations': aggregations,
                'sort': [{'field': '记录数', 'direction': 'desc'}],
                'limit': None,
            },
            [dimension, *numeric[:3]],
        )
    negative_numeric = next((
        field['name'] for field in fields
        if field.get('data_type') == 'number' and int(field.get('negative_count', 0)) > 0
    ), None)
    if negative_numeric:
        add(
            f'{negative_numeric}负值记录核查明细',
            f'筛出「{negative_numeric}」小于零的完整记录，便于确认冲销、退回或异常值口径。',
            {
                'mode': 'detail',
                'output_name': f'{negative_numeric}负值明细',
                'filters': [{'field': negative_numeric, 'operator': 'less_than', 'value': 0}],
                'columns': [
                    {'name': name, 'expression': {'op': 'field', 'field': name}}
                    for name in columns[:compose_plan.MAX_PLAN_COLUMNS]
                ],
                'sort': [{'field': negative_numeric, 'direction': 'asc'}],
                'limit': None,
            },
            [negative_numeric, *columns[:5]],
            ['负值的业务含义需要用户确认，系统仅按真实数值筛选。'],
        )
    if len(directions) > 1:
        directions = [*directions[1:], directions[0]]
    for index, direction in enumerate(directions):
        direction['recommended'] = index == 0
    return directions[:3]


def _default_rules() -> list[dict]:
    return []


_BINDING_PREFERENCES: dict[str, tuple[str, ...]] = {
    "entity_id": ("往来单位编号", "客户编号", "客户编码", "单位编号", "门店编号", "供应商编号", "customerid"),
    "entity_name": ("往来单位名称", "客户名称", "单位名称", "门店名称", "供应商名称", "客户名", "customername"),
    "date": ("交易日期", "开单日期", "业务日期", "日期", "年月", "月份", "时间", "date", "time"),
    "amount": ("含税金额", "销售金额", "销售额", "金额", "价税合计", "收入", "amount", "sales"),
    # 单据标识通常承载销售/退货代码；若存在多个候选列，仍要求用户确认具体代码口径。
    "transaction_type": ("单据标识", "交易类型", "单据类型", "业务类型", "收支类型", "出入库类型"),
    "document_id": ("单据编号", "单据号", "单号", "订单号", "票号", "documentid", "orderid"),
    "department": ("部门名称", "销售部门", "业务部门", "部门", "事业部", "department"),
    "salesperson": ("业务员", "销售员", "负责人", "经办人", "salesperson"),
    "region": ("地区", "区域", "省份", "城市", "region"),
}


def _binding_rank(role: str, field: dict) -> tuple[int, int, float, str]:
    name = _normalize(field.get("name", "")).lower()
    preferences = _BINDING_PREFERENCES.get(role, ())
    for index, preferred in enumerate(preferences):
        target = _normalize(preferred).lower()
        if name == target:
            return (0, index, -float(field.get("confidence", 0)), name)
        if target and target in name:
            return (1, index, -float(field.get("confidence", 0)), name)
    return (2, len(preferences), -float(field.get("confidence", 0)), name)


def _role_bindings(dataset: dict) -> dict:
    bindings: dict[str, str] = {}
    by_role: dict[str, list[dict]] = defaultdict(list)
    for field in dataset.get("fields", []):
        role = field.get("role")
        if role and role != "dimension":
            by_role[role].append(field)
    for role, candidates in by_role.items():
        bindings[role] = min(candidates, key=lambda field: _binding_rank(role, field))["name"]
    return bindings


def _has_transaction_sign_rule(plan: dict) -> bool:
    rules = plan.get("business_rules", {})
    return bool(rules.get("sales_values")) and bool(rules.get("return_values")) or rules.get("transaction_sign_source") == "amount_sign"


def _transaction_values_from_message(message: str) -> tuple[set[str], set[str]]:
    """Extract explicit sales/return codes without leaking one clause into another."""
    sales_values: set[str] = set()
    return_values: set[str] = set()
    for clause in re.split(r"[，,；;。.!！?？\n]+", message.upper()):
        codes = [(match.group(), match.start()) for match in re.finditer(r"[A-Z][A-Z0-9]*", clause)]
        labels = [("sale", match.start()) for match in re.finditer("销售", clause)] + [("return", match.start()) for match in re.finditer("退货", clause)]
        if not codes or not labels:
            continue
        label_kinds = {kind for kind, _ in labels}
        if len(label_kinds) == 1:
            target = sales_values if "sale" in label_kinds else return_values
            target.update(code for code, _ in codes)
            continue
        for code, position in codes:
            nearest_kind, _ = min(labels, key=lambda item: abs(item[1] - position))
            (sales_values if nearest_kind == "sale" else return_values).add(code)
    return sales_values, return_values


def _set_outputs(plan: dict) -> None:
    if plan.get('output_kind') == 'table':
        table = plan.get('table') or {}
        plan['filters'] = table.get('filters', [])
        plan['dimensions'] = [item.get('field') for item in table.get('group_by', [])]
        plan['outputs'] = [{
            'name': table.get('output_name') or '目标表',
            'key': 'table',
            'group_by': [item.get('name') or item.get('field') for item in table.get('group_by', [])],
            'metrics': [item.get('name') for item in table.get('aggregations', [])],
        }]
        return
    kind = plan.get("output_kind", "customer_sales")
    classification_enabled = bool(plan.get("classification", {}).get("enabled"))
    bindings = plan.get("role_bindings", {})
    dimensions = list(dict.fromkeys(
        name for name in plan.get("dimensions", [])
        if name not in {bindings.get("entity_id"), bindings.get("entity_name")}
    ))
    plan["dimensions"] = dimensions
    names = plan.get("output_names", {})
    if kind == "generic_summary" and not classification_enabled:
        measures = list(plan.get("measures", []))
        plan["outputs"] = [{
            "name": _display(names.get("generic_summary"))[:31] or "数据汇总",
            "key": "generic_summary",
            "group_by": (["period"] if plan.get("time_grain") else []) + dimensions,
            "metrics": [item.get("name") or item.get("field") for item in measures] + ["row_count"],
        }]
        return
    if kind == "dimension_summary" and not classification_enabled:
        plan["outputs"] = [{
            "name": _display(names.get("dimension_summary"))[:31] or "维度汇总",
            "key": "dimension_summary",
            "group_by": (["period"] if plan.get("time_grain") else []) + dimensions,
            "metrics": ["sales_amount", "return_amount", "net_amount", "document_count", "share"],
        }]
        return
    plan["outputs"] = [{
        "name": _display(names.get("customer_sales"))[:31] or "客户销售汇总",
        "key": "customer_sales",
        "group_by": ["entity_id", "entity_name"] + (["period"] if plan.get("time_grain") else []) + dimensions,
        "metrics": ["sales_amount", "return_amount", "net_amount", "document_count", "share"],
    }]
    if classification_enabled:
        plan["outputs"].extend([
            {"name": _display(names.get("classification_summary"))[:31] or "分类汇总", "key": "classification_summary", "group_by": ["classification"], "metrics": ["customer_count", "sales_amount", "return_amount", "net_amount", "share"]},
            {"name": _display(names.get("pending_classification"))[:31] or "待确认分类", "key": "pending_classification", "group_by": ["entity_id", "entity_name"], "metrics": ["suggested_type", "reason"]},
        ])


def _dedupe_questions(questions: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for question in questions:
        if not isinstance(question, dict):
            continue
        code = _display(question.get("code"))[:60]
        text = _display(question.get("text"))[:300]
        key = code or text
        if not text or key in seen:
            continue
        seen.add(key)
        cleaned = {"code": code or f"question_{len(result) + 1}", "text": text, "blocking": bool(question.get("blocking", True))}
        evidence = _display(question.get("evidence"))[:500]
        if evidence:
            cleaned["evidence"] = evidence
        options = []
        for option in question.get("options", []) if isinstance(question.get("options"), list) else []:
            if not isinstance(option, dict):
                continue
            label = _display(option.get("label"))[:120]
            message = _display(option.get("message"))[:300]
            if label and message:
                options.append({
                    "id": _display(option.get("id"))[:60] or f"option_{len(options) + 1}",
                    "label": label,
                    "message": message,
                    "recommended": bool(option.get("recommended")),
                })
        if options:
            cleaned["options"] = options[:5]
        result.append(cleaned)
    return result[:12]


def _refresh_plan_questions(plan: dict, dataset: dict) -> dict:
    if plan.get('output_kind') == 'table':
        questions = [
            question for question in plan.get('questions', [])
            if question.get('code') != 'intent_confirmation'
        ]
        confirmation = plan.get('intent_confirmation') or {}
        if confirmation.get('required') and not confirmation.get('confirmed'):
            questions = [{
                'code': 'intent_confirmation',
                'text': '请确认系统是否正确理解了你要制作的目标表。',
                'blocking': True,
                'options': [{
                    'id': 'confirm_intent',
                    'label': '确认，按这个需求继续',
                    'message': '确认需求理解，按这个需求继续生成制表方案',
                    'recommended': True,
                }],
            }]
        elif plan.get('deferred_questions'):
            questions = list(plan.pop('deferred_questions'))
        for index, requirement in enumerate(plan.get('unresolved_requirements', [])):
            questions.append({'code': f'unsupported_{index + 1}', 'text': _display(requirement), 'blocking': True})
        plan['questions'] = _dedupe_questions(questions)
        plan['ready_for_execution'] = bool(confirmation.get('confirmed', True)) and not any(item.get('blocking') for item in plan['questions'])
        _set_outputs(plan)
        return plan
    managed_codes = {"entity_id", "amount", "transaction_sign", "zero_amount_policy", "classification_rules"}
    questions = [question for question in plan.get("questions", []) if question.get("code") not in managed_codes]
    bindings = plan.get("role_bindings", {})
    is_generic = plan.get("output_kind") == "generic_summary"
    needs_entity = plan.get("output_kind") not in {"dimension_summary", "generic_summary"} or bool(plan.get("classification", {}).get("enabled"))
    if needs_entity and not bindings.get("entity_id"):
        questions.append({"code": "entity_id", "text": "请确认哪一列是客户、供应商或其他汇总实体的稳定编号。", "blocking": True})
    if not is_generic and not bindings.get("amount"):
        questions.append({"code": "amount", "text": "请确认哪一列是需要汇总的金额。", "blocking": True})
    transaction = dataset.get("evidence", {}).get("transaction", {})
    if not is_generic and bindings.get("transaction_type") and not _has_transaction_sign_rule(plan):
        field = next((item for item in dataset.get("fields", []) if item.get("name") == bindings["transaction_type"]), {})
        examples = "、".join(_display(value) for value in field.get("examples", [])[:5] if _display(value))
        agreement = float(transaction.get("agreement_rate", 0))
        mappings = transaction.get("mappings", [])
        mapping_text = "；".join(
            f"{item['value']}：正数 {int(item.get('positive_rows', 0)):,} 行，负数 {int(item.get('negative_rows', 0)):,} 行"
            for item in mappings[:6]
        )
        evidence = mapping_text or (f"「{bindings['transaction_type']}」示例值：{examples}。" if examples else "尚未形成稳定的编码与金额关系。")
        recommendation = "系统建议按金额正负识别销售和退货。" if transaction.get("supports_amount_sign") else "请指定销售和退货的识别方式。"
        options = [{
            "id": "amount_sign",
            "label": "按金额正负识别（推荐）",
            "message": "按金额正负识别，正数是销售，负数是退货",
            "recommended": True,
        }]
        if transaction.get("supports_code_mapping"):
            sales = "、".join(transaction.get("sales_values", []))
            returns = "、".join(transaction.get("return_values", []))
            options.append({
                "id": "transaction_codes",
                "label": f"按{bindings['transaction_type']}编码识别",
                "message": f"按{bindings['transaction_type']}识别，销售为 {sales}，退货为 {returns}",
                "recommended": False,
            })
        questions.append({
            "code": "transaction_sign",
            "text": recommendation,
            "evidence": f"全量非零记录的一致率为 {agreement * 100:.2f}%。{evidence}",
            "options": options,
            "blocking": True,
        })
    zero_rows = int(transaction.get("zero_rows", 0))
    if not is_generic and zero_rows > 0 and not plan.get("business_rules", {}).get("zero_amount_policy_confirmed"):
        questions.append({
            "code": "zero_amount_policy",
            "text": f"发现 {zero_rows:,} 条零金额记录。它们不影响金额，但是否计入销售或退货单数？",
            "evidence": "零金额记录会单独进入质量报告，不会被静默忽略。",
            "options": [
                {"id": "zero_exclude", "label": "不计入单数（推荐）", "message": "零金额记录不计入销售和退货单数，但在质量报告中单独显示", "recommended": True},
                {"id": "zero_count", "label": "按交易类型计入单数", "message": "零金额记录按交易类型计入销售或退货单数", "recommended": False},
            ],
            "blocking": True,
        })
    classification = plan.get("classification", {})
    if classification.get("enabled") and not classification.get("rules"):
        questions.append({"code": "classification_rules", "text": "请说明分类依据、分类名称和对应关键词、前缀或精确值；未确认的映射不会用于全量结果。", "blocking": True})
    plan["questions"] = _dedupe_questions(questions)
    plan["ready_for_execution"] = not any(question.get("blocking") for question in plan["questions"])
    _set_outputs(plan)
    return plan


def _sanitize_model_update(plan: dict, dataset: dict, update: dict, facts: list[dict]) -> dict:
    columns = set(dataset.get("schema", {}).get("columns", []))
    corpus = "\n".join(_display(item.get("text")) for item in facts)
    latest_message = _display(facts[-1].get("text")) if facts else ""
    title = _display(update.get("direction_title"))[:60]
    if title:
        plan["direction_title"] = title
    if update.get("output_kind") in {"customer_sales", "dimension_summary", "classification", "generic_summary"}:
        plan["output_kind"] = update["output_kind"]
    if isinstance(update.get("output_names"), dict):
        allowed_output_keys = {"customer_sales", "dimension_summary", "generic_summary", "classification_summary", "pending_classification"}
        output_names = dict(plan.get("output_names", {}))
        for key, value in update["output_names"].items():
            name = re.sub(r"[\\/*?:\[\]]", "_", _display(value))[:31]
            if key in allowed_output_keys and name:
                output_names[key] = name
        plan["output_names"] = output_names
    if isinstance(update.get("role_bindings"), dict):
        allowed_roles = {"entity_id", "entity_name", "amount", "date", "transaction_type", "document_id", "department", "salesperson", "region"}
        bindings = dict(plan.get("role_bindings", {}))
        for role, field in update["role_bindings"].items():
            if role in allowed_roles and field in columns and (not bindings.get(role) or field == bindings.get(role) or field in latest_message):
                bindings[role] = field
        plan["role_bindings"] = bindings
    if isinstance(update.get("dimensions"), list):
        existing_dimensions = list(plan.get("dimensions", []))
        proposed_dimensions = [name for name in update["dimensions"] if name in columns]
        removed_dimensions = {
            name for name in existing_dimensions
            if name in latest_message and any(word in latest_message for word in ("去掉", "移除", "取消", "不要"))
        }
        plan["dimensions"] = list(dict.fromkeys(
            [name for name in existing_dimensions if name not in removed_dimensions] + proposed_dimensions
        ))[:5]
    if isinstance(update.get("measures"), list):
        measures = []
        numeric_fields = {
            field["name"] for field in dataset.get("fields", [])
            if field.get("data_type") == "number"
        }
        for item in update["measures"][:8]:
            if not isinstance(item, dict) or item.get("field") not in numeric_fields:
                continue
            aggregation = item.get("aggregation") if item.get("aggregation") in {"sum", "average", "min", "max"} else "sum"
            measures.append({
                "field": item["field"],
                "aggregation": aggregation,
                "name": _display(item.get("name"))[:60] or f"{item['field']}合计",
            })
        if measures:
            plan["measures"] = measures
    if "time_grain" in update and update.get("time_grain") in {None, "year", "quarter", "month"}:
        proposed_grain = update.get("time_grain")
        if proposed_grain == plan.get("time_grain") or any(word in latest_message for word in ("年度", "按年", "季度", "月份", "月度", "不按时间", "取消时间")):
            plan["time_grain"] = proposed_grain
    if isinstance(update.get("filters"), list) and (not plan.get("filters") or any(word in latest_message for word in ("只看", "仅看", "筛选", "过滤", "取消筛选"))):
        filters = []
        for item in update["filters"][:8]:
            if not isinstance(item, dict):
                continue
            column = item.get("column")
            operator = item.get("operator")
            value = _display(item.get("value"))
            if column in columns and operator in {"equals", "contains"} and value:
                filters.append({"column": column, "operator": operator, "value": value})
        plan["filters"] = filters
    if isinstance(update.get("business_rules"), dict):
        incoming = update["business_rules"]
        rules = plan.setdefault("business_rules", {})
        amount_sign_confirmed = "正负" in latest_message or (
            any(word in latest_message for word in ("正数", "正金额"))
            and any(word in latest_message for word in ("负数", "负金额"))
        )
        transaction_type_confirmed = any(word in latest_message for word in ("单据类型", "交易类型", "单据标识", "业务类型"))
        if incoming.get("transaction_sign_source") == "amount_sign" and amount_sign_confirmed:
            rules["transaction_sign_source"] = "amount_sign"
        elif incoming.get("transaction_sign_source") == "transaction_type" and transaction_type_confirmed:
            rules["transaction_sign_source"] = "transaction_type"
        for key in ("sales_values", "return_values"):
            if isinstance(incoming.get(key), list):
                confirmed_values = [_display(value) for value in incoming[key] if _display(value) and _display(value) in corpus]
                if confirmed_values:
                    rules[key] = list(dict.fromkeys(confirmed_values))[:30]
        if "零" in latest_message and incoming.get("zero_amount_policy") in {"exclude_from_amounts", "count_documents"}:
            rules["zero_amount_policy"] = incoming["zero_amount_policy"]
            rules["zero_amount_policy_confirmed"] = True
    if isinstance(update.get("classification"), dict):
        incoming = update["classification"]
        classification = plan.setdefault("classification", {})
        if isinstance(incoming.get("enabled"), bool) and (
            not classification.get("enabled")
            or incoming.get("enabled")
            or any(word in latest_message for word in ("不分类", "不做分类", "取消分类"))
        ):
            classification["enabled"] = incoming["enabled"]
        accepted_rules = []
        for raw_rule in incoming.get("rules", []) if isinstance(incoming.get("rules"), list) else []:
            if not isinstance(raw_rule, dict):
                continue
            label = _display(raw_rule.get("label"))[:60]
            if not label or label not in corpus:
                continue
            conditions = []
            for raw_condition in raw_rule.get("conditions", []) if isinstance(raw_rule.get("conditions"), list) else []:
                if not isinstance(raw_condition, dict):
                    continue
                field = raw_condition.get("field")
                operator = raw_condition.get("operator")
                values = list(dict.fromkeys(_display(value) for value in raw_condition.get("values", []) if _display(value)))[:40]
                if field in columns and operator in {"contains", "prefix", "equals", "regex"} and values and all(value in corpus for value in values):
                    conditions.append({"field": field, "operator": operator, "values": values})
            if conditions:
                accepted_rules.append({"label": label, "conditions": conditions, "match": "all" if raw_rule.get("match") == "all" else "any"})
        if accepted_rules:
            merged_rules = {rule.get("label"): rule for rule in classification.get("rules", []) if rule.get("label")}
            merged_rules.update({rule["label"]: rule for rule in accepted_rules})
            classification["rules"] = list(merged_rules.values())
            classification["enabled"] = True
    if isinstance(update.get("questions"), list):
        plan["questions"] = _dedupe_questions(update["questions"])
    assistant_message = _display(update.get("assistant_message"))[:600]
    if assistant_message:
        plan["assistant_message"] = assistant_message
    return plan


def _custom_base_direction(dataset: dict) -> dict:
    columns = list(dataset.get('schema', {}).get('columns', []))
    table = {
        'mode': 'detail',
        'output_name': '自定义目标表',
        'filters': [],
        'columns': [
            {'name': name, 'expression': {'op': 'field', 'field': name}}
            for name in columns[:compose_plan.MAX_PLAN_COLUMNS]
        ],
        'sort': [],
        'limit': None,
    }
    return {
        'id': 'custom_requirement',
        'title': '自定义目标表',
        'description': '等待根据你的描述生成具体目标表方案。',
        'plan_hint': {'output_kind': 'table', 'table': table},
    }


def _make_table_plan(dataset: dict, direction: dict, facts: list[dict] | None = None) -> dict:
    hint = direction.get('plan_hint') or {}
    table = json.loads(json.dumps(hint.get('table'), ensure_ascii=False))
    compose_plan.validate_table_spec(table, dataset.get('schema', {}).get('columns', []))
    return {
        'schema_version': 3,
        'dataset_id': dataset['session_id'],
        'direction_id': direction['id'],
        'direction_title': direction['title'],
        'output_kind': 'table',
        'table': table,
        'outputs': [{
            'name': table.get('output_name') or '目标表',
            'key': 'table',
            'group_by': [item.get('name') or item.get('field') for item in table.get('group_by', [])],
            'metrics': [item.get('name') for item in table.get('aggregations', [])],
        }],
        'role_bindings': _role_bindings(dataset),
        'business_rules': {},
        'filters': table.get('filters', []),
        'time_grain': None,
        'dimensions': [item.get('field') for item in table.get('group_by', [])],
        'measures': [],
        'classification': {
            'enabled': False, 'rule_pack': 'user_confirmed_rules_v1',
            'rule_pack_version': '2.0', 'rules': [], 'unresolved_policy': 'pending_review',
        },
        'requirement_summary': direction.get('description') or direction.get('title'),
        'intent_confirmation': {'required': False, 'confirmed': True},
        'confirmed_facts': list(facts or []),
        'decision_log': list(facts or []),
        'unresolved_requirements': [],
        'questions': [],
        'ready_for_execution': True,
    }


def _explicit_requirement_gaps(dataset: dict, message: str, table: dict) -> list[str]:
    """Check only requirements that can be extracted without semantic guessing."""
    gaps: list[str] = []
    filters = table.get('filters', [])
    source_fields = set(dataset.get('schema', {}).get('columns', []))

    filter_clauses = re.findall(r'(?:只保留|仅保留|筛选)([^，。；\n]+)', message)
    expected_filter_values: set[str] = set()
    for clause in filter_clauses:
        for field in dataset.get('fields', []):
            for item in field.get('top_values', []):
                value = _display(item.get('value'))
                if value and value in clause:
                    expected_filter_values.add(value)
    actual_filter_values = {
        _display(value)
        for condition in filters if isinstance(condition, dict)
        for value in (
            condition.get('value', [])
            if isinstance(condition.get('value'), list)
            else [condition.get('value')]
        )
        if _display(value)
    }
    for value in sorted(expected_filter_values - actual_filter_values):
        gaps.append(f'用户明确要求筛选“{value}”，但计划没有对应筛选条件。')

    output_match = re.search(r'(?:输出字段(?:包括|为|：)?|输出)([^。；\n]+)', message)
    if output_match:
        output_clause = output_match.group(1)
        requested_source_fields = {name for name in source_fields if name in output_clause}
        if requested_source_fields:
            identity_fields = {
                item.get('expression', {}).get('field')
                for item in table.get('columns', [])
                if isinstance(item, dict)
                and isinstance(item.get('expression'), dict)
                and item['expression'].get('op') == 'field'
                and item.get('name') == item['expression'].get('field')
            }
            missing = requested_source_fields - identity_fields
            unexpected = identity_fields - requested_source_fields
            if missing:
                gaps.append(f'明确要求的输出字段缺失：{"、".join(sorted(missing))}。')
            if unexpected:
                gaps.append(f'计划增加了用户未要求的直接输出字段：{"、".join(sorted(unexpected))}。')

    sort_match = re.search(r'按([^，。；\n]{1,80}?)(从高到低|由高到低|降序|从低到高|由低到高|升序)', message)
    if sort_match:
        expected_direction = 'desc' if sort_match.group(2) in {'从高到低', '由高到低', '降序'} else 'asc'
        output_names = [
            item.get('name') for item in table.get('columns', [])
            if isinstance(item, dict) and _display(item.get('name'))
        ]
        expected_field = next(
            (name for name in sorted(output_names, key=len, reverse=True) if name in sort_match.group(1)),
            sort_match.group(1).strip(' “”「」'),
        )
        sorts = table.get('sort', [])
        if expected_field and not any(
            item.get('field') == expected_field and item.get('direction') == expected_direction
            for item in sorts if isinstance(item, dict)
        ):
            label = '降序' if expected_direction == 'desc' else '升序'
            gaps.append(f'用户明确要求按“{expected_field}”{label}，但计划排序不一致。')
    return gaps


def make_plan(
    dataset: dict,
    direction_id: str | None = None,
    user_text: str = "",
    facts: list[dict] | None = None,
    model_config: dict | None = None,
) -> dict:
    directions = dataset.get("directions", [])
    direction = next((d for d in directions if d["id"] == direction_id), None) if direction_id else None
    if direction is None and direction_id in {"customer_terminal", "customer_classification"}:
        direction = next((d for d in directions if d.get("plan_hint", {}).get("output_kind") == "classification"), None)
    if direction is None and direction_id == "customer_sales":
        direction = next((d for d in directions if d.get("plan_hint", {}).get("output_kind") == "customer_sales" and not d.get("plan_hint", {}).get("time_grain")), None)
    if direction is None and direction_id == "period_customer":
        direction = next((d for d in directions if d.get("plan_hint", {}).get("output_kind") == "customer_sales" and d.get("plan_hint", {}).get("time_grain")), None)
    if direction is None and direction_id == "dimension_amount":
        source = next((d for d in directions if d.get("plan_hint", {}).get("dimensions")), None)
        if source:
            direction = json.loads(json.dumps(source, ensure_ascii=False))
            direction["id"] = "dimension_amount"
            direction["title"] = "按业务维度汇总金额"
            direction["plan_hint"]["output_kind"] = "dimension_summary"
            direction["plan_hint"]["measures"] = []
            direction["plan_hint"]["output_names"] = {"dimension_summary": "维度汇总"}
    if direction is None and user_text and not direction_id:
        direction = _custom_base_direction(dataset)
    if direction is None:
        direction = next((d for d in directions if d.get("recommended")), directions[0] if directions else None)
    if direction is None:
        raise ComposeError("PLAN_INVALID", "当前数据没有可执行的目标表方向。")
    hint = direction.get("plan_hint") if isinstance(direction.get("plan_hint"), dict) else {}
    if hint.get('output_kind') == 'table':
        plan = _make_table_plan(dataset, direction, facts)
        if user_text:
            plan = apply_message(plan, dataset, user_text, facts or [], model_config=model_config)
        return _refresh_plan_questions(plan, dataset)
    bindings = _role_bindings(dataset)
    if "entity_id" not in bindings and "entity_name" in bindings:
        bindings["entity_id"] = bindings["entity_name"]
    output_kind = hint.get("output_kind") if hint.get("output_kind") in {"customer_sales", "dimension_summary", "classification", "generic_summary"} else "generic_summary"
    dimensions = [name for name in hint.get("dimensions", []) if name in dataset.get("schema", {}).get("columns", [])][:5]
    dimensions = [name for name in dimensions if name not in {bindings.get("entity_id"), bindings.get("entity_name")}]
    measures = []
    numeric_fields = {
        field["name"] for field in dataset.get("fields", [])
        if field.get("data_type") == "number"
    }
    for item in hint.get("measures", []) if isinstance(hint.get("measures"), list) else []:
        if isinstance(item, dict) and item.get("field") in numeric_fields:
            measures.append({
                "field": item["field"],
                "aggregation": item.get("aggregation") if item.get("aggregation") in {"sum", "average", "min", "max"} else "sum",
                "name": _display(item.get("name"))[:60] or f"{item['field']}合计",
            })
    plan = {
        "schema_version": 2,
        "dataset_id": dataset["session_id"],
        "direction_id": direction["id"],
        "direction_title": direction["title"],
        "output_kind": output_kind,
        "output_names": dict(hint.get("output_names", {})) if isinstance(hint.get("output_names"), dict) else {},
        "role_bindings": bindings,
        "business_rules": {
            "positive_amount": "amount > 0",
            "negative_amount": "amount < 0",
            "net_amount": "sales_amount - return_amount",
            "zero_amount_policy": "exclude_from_amounts",
            "zero_amount_policy_confirmed": False,
            "document_count_policy": "distinct_when_document_id_available",
        },
        "filters": [],
        "time_grain": hint.get("time_grain") if hint.get("time_grain") in {None, "year", "quarter", "month"} else None,
        "dimensions": dimensions,
        "measures": measures,
        "outputs": [],
        "classification": {
            "enabled": bool(hint.get("classification_requested")) or output_kind == "classification",
            "rule_pack": "user_confirmed_rules_v1",
            "rule_pack_version": "2.0",
            "rules": _default_rules(),
            "unresolved_policy": "pending_review",
        },
        "confirmed_facts": list(facts or []),
        "questions": [],
    }
    if user_text:
        plan = apply_message(plan, dataset, user_text, facts or [], model_config=model_config)
    return _refresh_plan_questions(plan, dataset)


def _apply_table_message(
    plan: dict,
    dataset: dict,
    message: str,
    facts: list[dict],
    model_config: dict | None,
) -> dict:
    if '确认需求理解' in message and plan.get('intent_confirmation', {}).get('required'):
        plan['intent_confirmation'] = {'required': True, 'confirmed': True}
        plan.setdefault('decision_log', []).append({'turn': len(facts), 'type': 'intent_confirmed', 'text': plan.get('requirement_summary', '')})
        plan['assistant_message'] = '需求理解已确认。请继续补齐下方仍会改变结果的业务口径。'
        return _refresh_plan_questions(plan, dataset)

    update, notice = compose_ai.refine_plan(dataset, plan, message, facts, model_config)
    if not update or not isinstance(update.get('table'), dict):
        plan['model_notice'] = notice or '模型没有返回可验证的通用制表计划。'
        plan['unresolved_requirements'] = ['当前无法可靠理解这条自然语言要求，请检查模型配置后重试。']
        plan['questions'] = []
        plan['intent_confirmation'] = {'required': False, 'confirmed': False}
        plan['ready_for_execution'] = False
        return _refresh_plan_questions(plan, dataset)
    source_columns = dataset.get('schema', {}).get('columns', [])
    table = compose_plan.normalize_model_table_spec(update['table'], source_columns)
    table['output_name'] = _display(table.get('output_name'))[:31] or '目标表'
    try:
        compose_plan.validate_table_spec(table, dataset.get('schema', {}).get('columns', []))
    except compose_plan.TablePlanError as exc:
        plan['model_notice'] = f'模型方案未通过确定性编译：{exc.message}'
        plan['unresolved_requirements'] = [f'方案中的 {exc.path} 无法执行：{exc.message}']
        plan['questions'] = []
        plan['intent_confirmation'] = {'required': False, 'confirmed': False}
        plan['ready_for_execution'] = False
        return _refresh_plan_questions(plan, dataset)
    gaps = _explicit_requirement_gaps(dataset, message, table)
    if gaps:
        repaired, repair_notice = compose_ai.repair_plan(dataset, update, message, gaps, facts, model_config)
        repaired_table = compose_plan.normalize_model_table_spec(repaired.get('table'), source_columns) if isinstance(repaired, dict) else None
        try:
            compose_plan.validate_table_spec(repaired_table, dataset.get('schema', {}).get('columns', []))
            repaired_gaps = _explicit_requirement_gaps(dataset, message, repaired_table)
        except compose_plan.TablePlanError as exc:
            repaired_gaps = [f'修正后的方案仍无法执行：{exc.message}']
        if repaired and isinstance(repaired_table, dict) and not repaired_gaps:
            update = repaired
            table = repaired_table
        else:
            plan['requirement_summary'] = _display(update.get('requirement_summary'))[:1600] or message
            plan['assistant_message'] = '模型方案没有完整落实你的明确要求，系统已阻止执行。请查看缺口后重新描述。'
            plan['model_notice'] = repair_notice or '模型自动修正后仍未通过需求覆盖校验。'
            plan['unresolved_requirements'] = repaired_gaps or gaps
            plan['questions'] = []
            plan['intent_confirmation'] = {'required': False, 'confirmed': False}
            plan['ready_for_execution'] = False
            return _refresh_plan_questions(plan, dataset)
    plan['table'] = table
    plan['direction_title'] = _display(update.get('direction_title'))[:60] or table['output_name']
    plan['requirement_summary'] = _display(update.get('requirement_summary'))[:1600] or message
    plan['deferred_questions'] = _dedupe_questions(update.get('questions', []))
    plan['unresolved_requirements'] = [_display(item)[:300] for item in update.get('unresolved_requirements', []) if _display(item)][:8]
    plan['assistant_message'] = _display(update.get('assistant_message'))[:1200] or plan['requirement_summary']
    plan.pop('model_notice', None)
    plan['intent_confirmation'] = {'required': True, 'confirmed': False}
    plan['questions'] = []
    plan['confirmed_facts'] = list(facts)
    _set_outputs(plan)
    return _refresh_plan_questions(plan, dataset)


def apply_message(plan: dict, dataset: dict, text: str, facts: list[dict], model_config: dict | None = None) -> dict:
    if plan.get('output_kind') == 'table':
        table_plan = json.loads(json.dumps(plan, ensure_ascii=False))
        table_plan['confirmed_facts'] = list(facts)
        return _apply_table_message(table_plan, dataset, text.strip(), facts, model_config)
    message = text.strip()
    plan = json.loads(json.dumps(plan, ensure_ascii=False))
    plan["confirmed_facts"] = list(facts)
    lowered = _normalize(message).lower()
    if any(word in message for word in ("不按时间", "取消时间", "去掉月份", "去掉月度", "每个汇总对象一行", "每个客户一行")):
        plan["time_grain"] = None
    elif "季度" in message:
        plan["time_grain"] = "quarter"
    elif "月份" in message or "月度" in message:
        plan["time_grain"] = "month"
    elif "年度" in message or "按年" in message:
        plan["time_grain"] = "year"
    if "不分类" in message or "不做分类" in message:
        plan["classification"]["enabled"] = False
        plan["classification"]["rules"] = []
    elif "分类" in message:
        plan["classification"]["enabled"] = True
    if "退货单独" in message or "退货单" in message:
        plan["business_rules"]["return_amount_output"] = "separate_column"
    if "净销售额" in message or "净额" in message:
        plan["business_rules"]["net_amount"] = "sales_amount - return_amount"
    if "零金额" in message:
        plan["business_rules"]["zero_amount_policy"] = "count_documents" if "计入单数" in message or "算单数" in message else "exclude_from_amounts"
        plan["business_rules"]["zero_amount_policy_confirmed"] = True
    sales_values, return_values = _transaction_values_from_message(message)
    if sales_values or return_values:
        mentioned = sales_values | return_values
        rules = plan["business_rules"]
        current_sales = {str(value) for value in rules.get("sales_values", [])} - mentioned
        current_returns = {str(value) for value in rules.get("return_values", [])} - mentioned
        rules["sales_values"] = sorted(current_sales | sales_values)
        rules["return_values"] = sorted(current_returns | return_values)
        if rules["sales_values"] and rules["return_values"]:
            rules["transaction_sign_source"] = "transaction_type" if any(word in message for word in ("按单据", "按交易", "按业务", "编码识别")) else "amount_sign"
    uses_positive_amount = ("正数" in message or "正金额" in message) and "销售" in message
    uses_negative_amount = ("负数" in message or "负金额" in message) and "退货" in message
    if uses_positive_amount and uses_negative_amount:
        plan["business_rules"]["transaction_sign_source"] = "amount_sign"
    role_fields = {field["name"]: field for field in dataset.get("fields", [])}
    for field_name, field in role_fields.items():
        if field_name in message and field.get("role") in {"department", "salesperson", "region", "dimension"}:
            if field_name not in plan.setdefault("dimensions", []):
                plan["dimensions"].append(field_name)
    quick_messages = {
        "按金额正负识别，正数是销售，负数是退货",
        "零金额记录不计入销售和退货单数，但在质量报告中单独显示",
        "零金额记录按交易类型计入销售或退货单数",
        "每个汇总对象一行，不按时间拆分",
        "按月份拆分，每个对象每月一行",
        "按季度拆分，每个对象每季度一行",
        "按年度拆分，每个对象每年一行",
    }
    locally_complete = message in quick_messages or bool(sales_values and return_values and "识别" in message)
    if not locally_complete:
        model_update, notice = compose_ai.refine_plan(dataset, plan, message, facts, model_config)
        if model_update:
            plan = _sanitize_model_update(plan, dataset, model_update, facts)
        elif notice:
            plan["model_notice"] = notice
    if "不分类" in message or "不做分类" in message:
        plan["classification"]["enabled"] = False
        plan["classification"]["rules"] = []
    if any(word in lowered for word in ("趋势", "建议", "原因分析", "预测")):
        plan.setdefault("questions", []).append({"code": "analysis_out_of_scope", "text": "当前功能只制作目标表，不生成趋势结论、原因分析或经营建议；请改为描述需要输出的字段、分组和计算口径。", "blocking": True})
    return _refresh_plan_questions(plan, dataset)


def validate_plan(manifest: dict, plan: dict | None = None) -> dict:
    plan = plan or manifest.get("plan")
    if manifest.get("status") not in {"READY", "EXECUTING", "COMPLETED"}:
        raise ComposeError("DATASET_INCOMPLETE", "源数据尚未完成全量导入。", details={"status": manifest.get("status")})
    if plan and plan.get('output_kind') == 'table':
        table_dataset = manifest.get('dataset') or {}
        confirmation = plan.get('intent_confirmation') or {}
        if confirmation.get('required') and not confirmation.get('confirmed'):
            raise ComposeError('PLAN_INVALID', '请先确认系统对目标表需求的理解。')
        if plan.get('unresolved_requirements') or any(item.get('blocking') for item in plan.get('questions', [])):
            raise ComposeError('PLAN_INVALID', '还有未解决的制表要求，暂不能全量生成。')
        try:
            compose_plan.validate_table_spec(plan.get('table'), table_dataset.get('schema', {}).get('columns', []))
        except compose_plan.TablePlanError as exc:
            raise ComposeError('PLAN_INVALID', exc.message, details={'path': exc.path}) from exc
        if [item.get('key') for item in plan.get('outputs', [])] != ['table']:
            raise ComposeError('PLAN_INVALID', '通用方案必须且只能声明一个受控目标表输出。')
        return plan
    if not plan:
        raise ComposeError("PLAN_INVALID", "尚未选择目标表方向。")
    dataset = manifest.get("dataset") or {}
    columns = set(dataset.get("schema", {}).get("columns", []))
    bindings = plan.get("role_bindings") or {}
    unique_bindings = [v for role, v in bindings.items() if v and not (role == "entity_name" and v == bindings.get("entity_id"))]
    if len(unique_bindings) != len(set(unique_bindings)):
        raise ComposeError("SCHEMA_CONFLICT", "方案中多个角色绑定到了同一字段。")
    is_generic = plan.get("output_kind") == "generic_summary"
    required_roles = [] if is_generic else ["amount"]
    if plan.get("output_kind") not in {"dimension_summary", "generic_summary"} or plan.get("classification", {}).get("enabled"):
        required_roles.append("entity_id")
    for role in required_roles:
        if not bindings.get(role) or bindings[role] not in columns:
            raise ComposeError("PLAN_INVALID", f"方案缺少可用的{role}字段。", details={"role": role})
    for name in plan.get("dimensions", []):
        if name not in columns:
            raise ComposeError("PLAN_INVALID", f"方案中的维度字段不存在：{name}。")
    if is_generic:
        for measure in plan.get("measures", []):
            if measure.get("field") not in columns or measure.get("aggregation") not in {"sum", "average", "min", "max"}:
                raise ComposeError("PLAN_INVALID", "方案中包含不可执行的数值指标。")
        if not plan.get("dimensions") and not plan.get("time_grain") and not plan.get("measures"):
            # A row-count overview is still deterministic and useful for a
            # table without stable numeric or categorical fields.
            plan["measures"] = []
    for item in plan.get("filters", []):
        if item.get("column") not in columns or item.get("operator") not in {"equals", "contains"} or not item.get("value"):
            raise ComposeError("PLAN_INVALID", "方案中的筛选条件无效。")
    classification = plan.get("classification", {})
    if classification.get("rule_pack") not in {"user_confirmed_rules_v1", "generic_terminal_v1", None}:
        raise ComposeError("PLAN_INVALID", "当前分类规则包不可用。")
    for rule in classification.get("rules", []):
        if not _display(rule.get("label")):
            raise ComposeError("PLAN_INVALID", "分类规则缺少分类名称。")
        conditions = rule.get("conditions", [])
        if not conditions and not rule.get("keywords"):
            raise ComposeError("PLAN_INVALID", f"分类「{rule.get('label')}」没有可执行条件。")
        for condition in conditions:
            if condition.get("field") not in columns or condition.get("operator") not in {"contains", "prefix", "equals", "regex"} or not condition.get("values"):
                raise ComposeError("PLAN_INVALID", f"分类「{rule.get('label')}」包含无效条件。")
    output_keys = {item.get("key") for item in plan.get("outputs", [])}
    if not output_keys or not output_keys.issubset({"customer_sales", "dimension_summary", "generic_summary", "classification_summary", "pending_classification"}):
        raise ComposeError("PLAN_INVALID", "方案包含当前执行器不支持的输出表。")
    sales_values = set(map(str, plan.get("business_rules", {}).get("sales_values", [])))
    return_values = set(map(str, plan.get("business_rules", {}).get("return_values", [])))
    if sales_values & return_values:
        raise ComposeError("PLAN_INVALID", "同一个交易类型值不能同时代表销售和退货。")
    if any(question.get("blocking") for question in plan.get("questions", [])):
        raise ComposeError("PLAN_INVALID", "还有必须确认的业务口径，暂不能全量生成。")
    return plan


def invalidate_result_state(manifest: dict) -> dict:
    """Make a changed plan executable without exposing stale completed output."""
    manifest["status"] = "READY"
    manifest["quality"] = None
    manifest["result_sheets"] = []
    manifest["result_summary"] = {}
    manifest["logic_id"] = None
    if manifest.get("dataset") is not None:
        manifest["dataset"]["valid_rows"] = None
    return manifest


def _period(value: Any, grain: str | None) -> str:
    if not grain or value in (None, ""):
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("/", "-")[:19])
    except ValueError:
        return _display(value)
    if grain == "year":
        return str(parsed.year)
    if grain == "month":
        return f"{parsed.year}-{parsed.month:02d}"
    return f"{parsed.year}-Q{((parsed.month - 1) // 3) + 1}"


def _condition_matches(condition: dict, profile: dict[str, set[str]]) -> tuple[bool, list[str]]:
    source_values = {_display(value) for value in profile.get(condition.get("field"), set()) if _display(value)}
    expected_values = [_display(value) for value in condition.get("values", []) if _display(value)]
    operator = condition.get("operator")
    hits = []
    for source in source_values:
        for expected in expected_values:
            matched = False
            if operator == "equals":
                matched = source.casefold() == expected.casefold()
            elif operator == "prefix":
                matched = source.upper().startswith(expected.upper())
            elif operator == "contains":
                matched = expected.casefold() in source.casefold()
            elif operator == "regex":
                try:
                    matched = re.search(expected, source, flags=re.IGNORECASE) is not None
                except re.error:
                    matched = False
            if matched:
                hits.append(f"{condition.get('field')}={source}")
    return bool(hits), list(dict.fromkeys(hits))[:5]


def _classify(entity_id: str, entity_name: str, plan: dict, manual: dict, profile: dict[str, set[str]] | None = None) -> tuple[str, str, str]:
    if not plan.get("classification", {}).get("enabled", False):
        return "未分类", "未启用", "方案未启用终端分类"
    if entity_id in manual and _display(manual[entity_id]):
        return _display(manual[entity_id]), "已确认", "用户确认"
    profile = {key: set(values) for key, values in (profile or {}).items()}
    bindings = plan.get("role_bindings", {})
    if bindings.get("entity_id"):
        profile.setdefault(bindings["entity_id"], set()).add(entity_id)
    if bindings.get("entity_name"):
        profile.setdefault(bindings["entity_name"], set()).add(entity_name)
    text = f"{entity_id} {entity_name}"
    matches = []
    for rule in plan.get("classification", {}).get("rules", []):
        conditions = rule.get("conditions", [])
        condition_results = [_condition_matches(condition, profile) for condition in conditions]
        condition_hits = [hit for matched, hits in condition_results if matched for hit in hits]
        matched = bool(condition_results) and (all(item[0] for item in condition_results) if rule.get("match") == "all" else any(item[0] for item in condition_results))
        legacy_hits = [key for key in rule.get("keywords", []) if key and key.casefold() in text.casefold()]
        if matched or legacy_hits:
            matches.append((rule.get("label", "待确认"), condition_hits + legacy_hits))
    if len(matches) == 1:
        label, hits = matches[0]
        return label, "规则匹配", "关键词：" + "、".join(hits)
    if len(matches) > 1:
        return "待确认", "待确认", "命中多个分类：" + "、".join(m[0] for m in matches)
    return "待确认", "待确认", "未命中分类规则"


def _empty_aggregate() -> dict:
    return {"sales_amount": Decimal("0"), "return_amount": Decimal("0"), "sales_docs": set(), "return_docs": set(), "sales_rows": 0, "return_rows": 0, "last_date": "", "dimension_values": {}}


def _update_aggregate(agg: dict, sign: str, amount: Decimal, document_id: str, date_text: str) -> None:
    if date_text and date_text > agg["last_date"]:
        agg["last_date"] = date_text
    if sign == "sale":
        agg["sales_amount"] += amount
        agg["sales_rows"] += 1
        agg["sales_docs"].add(document_id)
    elif sign == "return":
        agg["return_amount"] += amount
        agg["return_rows"] += 1
        agg["return_docs"].add(document_id)


def _aggregate_generic(sid: str, plan: dict, job: Job | None = None) -> dict:
    manifest = read_manifest(sid)
    validate_plan(manifest, plan)
    dataset = manifest["dataset"]
    columns = dataset["schema"]["columns"]
    col_index = {name: index for index, name in enumerate(columns)}
    dimensions = list(plan.get("dimensions", []))
    measures = list(plan.get("measures", []))
    date_field = plan.get("role_bindings", {}).get("date")
    date_index = col_index.get(date_field) if date_field else None
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    source_sums: dict[str, Decimal] = {item["name"]: Decimal("0") for item in measures}
    processed = valid = invalid_rows = filtered_rows = zero_rows = 0
    invalid_measure_cells = missing_measure_cells = 0
    planned = int(dataset.get("total_rows", 0))
    conn = _connect(sid)
    try:
        cursor = conn.execute("SELECT source_file, sheet_name, source_row, values_json FROM source_rows ORDER BY row_id")
        for source_file, sheet_name, _source_row, raw_json in cursor:
            if job and job.cancelled:
                raise ComposeError("CANCELLED", "全量执行已取消。", status=409)
            processed += 1
            if job and (processed == 1 or processed % 5000 == 0):
                job.emit("progress", {
                    "stage": "aggregate_generic",
                    "label": "按确认的维度和指标汇总全量数据",
                    "processed_rows": processed,
                    "total_rows": planned,
                    "invalid_rows": invalid_rows,
                    "skipped_rows": dataset.get("skipped_rows", 0),
                    "current_file": source_file,
                    "current_sheet": sheet_name,
                })
            values = json.loads(raw_json)
            filtered = False
            for item in plan.get("filters", []):
                index = col_index.get(item.get("column"))
                actual = _display(values[index]) if index is not None and index < len(values) else ""
                expected = _display(item.get("value"))
                if not (actual == expected if item.get("operator") == "equals" else expected in actual):
                    filtered = True
                    break
            if filtered:
                filtered_rows += 1
                continue
            period = _period(values[date_index], plan.get("time_grain")) if date_index is not None and date_index < len(values) else ""
            dimension_values = tuple(
                _display(values[col_index[name]]) if col_index[name] < len(values) else ""
                for name in dimensions
            )
            key = ((period,) if plan.get("time_grain") else ()) + dimension_values
            if not key:
                key = ("全部数据",)
            aggregate = groups.setdefault(key, {
                "row_count": 0,
                "metrics": {
                    item["name"]: {"sum": Decimal("0"), "count": 0, "min": None, "max": None}
                    for item in measures
                },
            })
            if len(groups) > MAX_EXCEL_DATA_ROWS:
                raise ComposeError("OUTPUT_LIMIT_EXCEEDED", "预计结果超过 Excel 单个工作表的行数上限，请增加筛选条件或按时间拆分。", status=409)
            aggregate["row_count"] += 1
            row_invalid = False
            row_has_zero = False
            for item in measures:
                index = col_index[item["field"]]
                raw_value = values[index] if index < len(values) else None
                if not _display(raw_value):
                    missing_measure_cells += 1
                    continue
                numeric = _to_decimal(raw_value)
                if numeric is None:
                    invalid_measure_cells += 1
                    row_invalid = True
                    continue
                if numeric == 0:
                    row_has_zero = True
                source_sums[item["name"]] += numeric
                metric = aggregate["metrics"][item["name"]]
                metric["sum"] += numeric
                metric["count"] += 1
                metric["min"] = numeric if metric["min"] is None else min(metric["min"], numeric)
                metric["max"] = numeric if metric["max"] is None else max(metric["max"], numeric)
            if row_invalid:
                invalid_rows += 1
            else:
                valid += 1
            if row_has_zero:
                zero_rows += 1
    finally:
        conn.close()

    rows = []
    for key, aggregate in sorted(groups.items(), key=lambda item: item[0]):
        row = list(key)
        for item in measures:
            metric = aggregate["metrics"][item["name"]]
            aggregation = item["aggregation"]
            if aggregation == "average":
                value = metric["sum"] / metric["count"] if metric["count"] else None
            elif aggregation == "min":
                value = metric["min"]
            elif aggregation == "max":
                value = metric["max"]
            else:
                value = metric["sum"]
            row.append(_money(value) if isinstance(value, Decimal) else "")
        row.append(int(aggregate["row_count"]))
        rows.append(row)
    output = (plan.get("outputs") or [{"name": "数据汇总"}])[0]
    output_columns = []
    if plan.get("time_grain"):
        output_columns.append({"year": "年度", "quarter": "季度", "month": "月份"}.get(plan["time_grain"], "统计周期"))
    output_columns.extend(dimensions)
    if not output_columns:
        output_columns.append("汇总范围")
    output_columns.extend([item["name"] for item in measures])
    output_columns.append("记录数")
    reconciliations = [{
        "name": "source_to_processed_rows",
        "status": "passed" if processed == planned else "failed",
        "delta": str(processed - planned),
    }]
    for item in measures:
        if item["aggregation"] != "sum":
            continue
        output_sum = sum(
            (aggregate["metrics"][item["name"]]["sum"] for aggregate in groups.values()),
            Decimal("0"),
        )
        reconciliations.append({
            "name": f"source_to_output_{item['field']}",
            "status": "passed" if source_sums[item["name"]] == output_sum else "failed",
            "delta": _decimal_delta(source_sums[item["name"]] - output_sum),
        })
    quality = {
        "scope": "full",
        "planned_rows": planned,
        "processed_rows": processed,
        "valid_rows": valid,
        "invalid_rows": invalid_rows,
        "skipped_rows": int(dataset.get("skipped_rows", 0)),
        "zero_amount_rows": zero_rows,
        "filtered_rows": filtered_rows,
        "measure_invalid_cells": invalid_measure_cells,
        "excluded_null_measure_cells": missing_measure_cells,
        "transaction_sign_conflict_rows": 0,
        "entity_name_conflict_count": 0,
        "classification_enabled": False,
        "source_fingerprint": dataset.get("source_fingerprint", ""),
        "reconciliations": reconciliations,
        "pending_classification_count": 0,
        "can_complete": invalid_rows == 0 and all(item["status"] == "passed" for item in reconciliations),
    }
    return {
        "sheets": [{"name": _display(output.get("name"))[:31] or "数据汇总", "columns": output_columns, "rows": rows}],
        "quality": quality,
        "source_sales": "",
        "source_returns": "",
        "source_net": "",
        "source_metrics": {name: _money(value) for name, value in source_sums.items()},
    }


def _aggregate_table(sid: str, plan: dict, job: Job | None = None) -> dict:
    manifest = read_manifest(sid)
    validate_plan(manifest, plan)
    dataset = manifest['dataset']
    conn = _connect(sid)

    def source_rows() -> Iterator[tuple[str, str, int, list[Any]]]:
        cursor = conn.execute('SELECT source_file, sheet_name, source_row, values_json FROM source_rows ORDER BY row_id')
        for source_file, sheet_name, source_row, raw_json in cursor:
            yield source_file, sheet_name, source_row, json.loads(raw_json)

    def progress(processed: int, source_file: str, sheet_name: str) -> None:
        if job:
            job.emit('progress', {
                'stage': 'execute_table_plan',
                'label': '按确认方案计算全量数据',
                'processed_rows': processed,
                'total_rows': int(dataset.get('total_rows', 0)),
                'invalid_rows': 0,
                'skipped_rows': int(dataset.get('skipped_rows', 0)),
                'current_file': source_file,
                'current_sheet': sheet_name,
            })

    try:
        return compose_plan.execute_table_plan(
            plan['table'], dataset['schema']['columns'], source_rows(),
            planned_rows=int(dataset.get('total_rows', 0)),
            skipped_rows=int(dataset.get('skipped_rows', 0)),
            source_fingerprint=dataset.get('source_fingerprint', ''),
            progress=progress,
            cancelled=(lambda: bool(job and job.cancelled)),
        )
    except compose_plan.TablePlanError as exc:
        code = 'CANCELLED' if exc.path == 'execution' else 'PLAN_INVALID'
        raise ComposeError(code, exc.message, status=409, details={'path': exc.path}) from exc
    finally:
        conn.close()


def _aggregate(sid: str, plan: dict, job: Job | None = None) -> dict:
    if plan.get('output_kind') == 'table':
        return _aggregate_table(sid, plan, job)
    if plan.get("output_kind") == "generic_summary":
        return _aggregate_generic(sid, plan, job)
    manifest = read_manifest(sid)
    validate_plan(manifest, plan)
    dataset = manifest["dataset"]
    columns = dataset["schema"]["columns"]
    col_index = {name: i for i, name in enumerate(columns)}
    bindings = plan["role_bindings"]
    idx = {role: col_index.get(field) for role, field in bindings.items()}
    conn = _connect(sid)
    cursor = conn.execute("SELECT source_file, sheet_name, source_row, values_json FROM source_rows ORDER BY row_id")
    customers: dict[tuple[str, ...], dict] = {}
    dimension_aggregates: dict[tuple[str, ...], dict] = {}
    entity_names: dict[str, Counter] = defaultdict(Counter)
    entity_profiles: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    classifications: dict[str, tuple[str, str, str]] = {}
    source_net = Decimal("0")
    source_sales = Decimal("0")
    source_returns = Decimal("0")
    source_sales_docs: set[str] = set()
    source_return_docs: set[str] = set()
    processed = valid = invalid = zero_rows = filtered_rows = sign_conflicts = 0
    planned = int(dataset.get("total_rows", 0))
    dimensions = list(plan.get("dimensions", []))
    classification_fields = {
        condition.get("field")
        for rule in plan.get("classification", {}).get("rules", [])
        for condition in rule.get("conditions", [])
        if condition.get("field") in col_index
    }
    needs_entity = plan.get("output_kind") != "dimension_summary" or bool(plan.get("classification", {}).get("enabled"))
    business_rules = plan.get("business_rules", {})
    sign_source = business_rules.get("transaction_sign_source", "amount_sign")
    return_values = {_normalize(v) for v in business_rules.get("return_values", []) if _normalize(v)}
    sales_values = {_normalize(v) for v in business_rules.get("sales_values", []) if _normalize(v)}
    for source_file, sheet_name, source_row, raw_json in cursor:
        if job and job.cancelled:
            conn.close()
            raise ComposeError("CANCELLED", "全量执行已取消。", status=409)
        processed += 1
        if job and (processed == 1 or processed % 5000 == 0):
            job.emit("progress", {"stage": "import_rows", "label": "全量读取并统计流水", "processed_rows": processed, "total_rows": planned, "invalid_rows": invalid, "skipped_rows": dataset.get("skipped_rows", 0), "current_file": source_file, "current_sheet": sheet_name})
        values = json.loads(raw_json)
        filtered = False
        for item in plan.get("filters", []):
            filter_index = col_index.get(item.get("column"))
            filter_value = _display(values[filter_index]) if filter_index is not None and filter_index < len(values) else ""
            expected = _display(item.get("value"))
            matched = filter_value == expected if item.get("operator") == "equals" else expected in filter_value
            if not matched:
                filtered = True
                break
        if filtered:
            filtered_rows += 1
            continue
        entity_id = _display(values[idx["entity_id"]]) if idx.get("entity_id") is not None and idx["entity_id"] < len(values) else ""
        entity_name = _display(values[idx["entity_name"]]) if idx.get("entity_name") is not None and idx["entity_name"] < len(values) else entity_id
        amount = _to_decimal(values[idx["amount"]] if idx.get("amount") is not None and idx["amount"] < len(values) else None)
        if (needs_entity and not entity_id) or amount is None:
            invalid += 1
            continue
        if not entity_id:
            entity_id = "__all__"
            entity_name = "全部数据"
        valid += 1
        if amount == 0:
            zero_rows += 1
        trans = _normalize(values[idx["transaction_type"]]) if idx.get("transaction_type") is not None and idx["transaction_type"] < len(values) else ""
        amount_sign = "sale" if amount > 0 else "return" if amount < 0 else "zero"
        mapped_sign = "return" if trans in return_values else "sale" if trans in sales_values else None
        if sign_source == "transaction_type":
            if mapped_sign is None and amount != 0:
                invalid += 1
                valid -= 1
                continue
            sign = mapped_sign or "zero"
        else:
            sign = amount_sign
            if mapped_sign and amount_sign != "zero" and mapped_sign != amount_sign:
                sign_conflicts += 1
            if amount_sign == "zero" and business_rules.get("zero_amount_policy") == "count_documents" and mapped_sign:
                sign = mapped_sign
        doc = _display(values[idx["document_id"]]) if idx.get("document_id") is not None and idx["document_id"] < len(values) else f"{source_file}:{sheet_name}:{source_row}"
        date_value = values[idx["date"]] if idx.get("date") is not None and idx["date"] < len(values) else None
        date_text = _display(date_value)
        period = _period(date_value, plan.get("time_grain"))
        dimension_values = tuple(
            _display(values[col_index[dimension]]) if col_index[dimension] < len(values) else ""
            for dimension in dimensions
        )
        key = (entity_id, period, *dimension_values)
        agg = customers.setdefault(key, _empty_aggregate())
        if len(customers) > MAX_EXCEL_DATA_ROWS:
            conn.close()
            raise ComposeError("OUTPUT_LIMIT_EXCEEDED", "客户汇总结果超过 Excel 单个工作表的行数上限，请按时间或业务维度拆分。", status=409)
        agg["entity_name_values"] = agg.get("entity_name_values", Counter())
        agg["entity_name_values"][entity_name or entity_id] += 1
        agg["dimension_values"] = dict(zip(dimensions, dimension_values))
        dimension_key = (period, *dimension_values)
        dimension_agg = dimension_aggregates.setdefault(dimension_key, _empty_aggregate())
        if len(dimension_aggregates) > MAX_EXCEL_DATA_ROWS:
            conn.close()
            raise ComposeError("OUTPUT_LIMIT_EXCEEDED", "维度汇总结果超过 Excel 单个工作表的行数上限，请减少分组维度。", status=409)
        dimension_agg["dimension_values"] = dict(zip(dimensions, dimension_values))
        entity_names[entity_id][entity_name or entity_id] += 1
        for field in classification_fields:
            field_index = col_index[field]
            if field_index < len(values) and _display(values[field_index]):
                entity_profiles[entity_id][field].add(_display(values[field_index]))
        gross_amount = amount.copy_abs()
        _update_aggregate(agg, sign, gross_amount, doc, date_text)
        _update_aggregate(dimension_agg, sign, gross_amount, doc, date_text)
        if sign == "sale":
            source_sales_docs.add(doc)
            source_sales += gross_amount
            source_net += gross_amount
        elif sign == "return":
            source_return_docs.add(doc)
            source_returns += gross_amount
            source_net -= gross_amount
    conn.close()

    manual = manifest.get("classification_mapping", {})
    customer_rows = []
    terminal = defaultdict(_empty_aggregate)
    pending_rows = []
    classification_enabled = bool(plan.get("classification", {}).get("enabled"))
    for entity_id, names in entity_names.items():
        entity_name = names.most_common(1)[0][0]
        classifications[entity_id] = _classify(entity_id, entity_name, plan, manual, entity_profiles.get(entity_id))
        terminal_type, status, reason = classifications[entity_id]
        if classification_enabled and status == "待确认":
            pending_rows.append([entity_id, entity_name, "", reason, "待确认", ""])
    for key, agg in sorted(customers.items(), key=lambda pair: pair[0]):
        entity_id, period, *group_dimensions = key
        entity_name = agg["entity_name_values"].most_common(1)[0][0]
        terminal_type, status, reason = classifications.get(entity_id, ("未分类", "未启用", "方案未启用分类"))
        net = agg["sales_amount"] - agg["return_amount"]
        row = [entity_id, entity_name]
        if plan.get("time_grain"):
            row.append(period)
        row.extend(group_dimensions)
        if classification_enabled:
            row.extend([terminal_type, status, reason])
        row.extend([
            len(agg["sales_docs"]), len(agg["return_docs"]),
            _money(agg["sales_amount"]), _money(agg["return_amount"]), _money(net),
            _percent(net / source_net) if source_net != 0 else "", agg["last_date"],
        ])
        customer_rows.append(row)
        if classification_enabled:
            target = terminal[terminal_type]
            target["customer_ids"] = target.get("customer_ids", set()) | {entity_id}
            target["sales_amount"] += agg["sales_amount"]
            target["return_amount"] += agg["return_amount"]
            target["sales_docs"].update(agg["sales_docs"])
            target["return_docs"].update(agg["return_docs"])
    total_net = source_sales - source_returns
    total_customers = len(classifications)
    terminal_rows = []
    for terminal_type, agg in sorted(terminal.items()):
        net = agg["sales_amount"] - agg["return_amount"]
        terminal_rows.append([
            terminal_type,
            len(agg.get("customer_ids", set())),
            _percent(Decimal(len(agg.get("customer_ids", set()))) / Decimal(total_customers)) if total_customers else "",
            _money(agg["sales_amount"]), _money(agg["return_amount"]), _money(net),
            _percent(net / total_net) if total_net != 0 else "",
            _percent(agg["return_amount"] / agg["sales_amount"]) if agg["sales_amount"] != 0 else "",
        ])
    dimension_rows = []
    for (period, *group_dimensions), agg in sorted(dimension_aggregates.items(), key=lambda pair: pair[0]):
        net = agg["sales_amount"] - agg["return_amount"]
        row = []
        if not plan.get("time_grain") and not dimensions:
            row.append("全部数据")
        if plan.get("time_grain"):
            row.append(period)
        row.extend(group_dimensions)
        row.extend([
            len(agg["sales_docs"]), len(agg["return_docs"]),
            _money(agg["sales_amount"]), _money(agg["return_amount"]), _money(net),
            _percent(net / total_net) if total_net != 0 else "", agg["last_date"],
        ])
        dimension_rows.append(row)
    # Reconcile the unrounded aggregates. Display cells are serialized after
    # aggregation and must not introduce a per-customer rounding delta.
    customer_sum = sum((agg["sales_amount"] - agg["return_amount"] for agg in customers.values()), Decimal("0"))
    terminal_sum = sum((agg["sales_amount"] - agg["return_amount"] for agg in terminal.values()), Decimal("0"))
    dimension_sum = sum((agg["sales_amount"] - agg["return_amount"] for agg in dimension_aggregates.values()), Decimal("0"))
    customer_sales_docs = set().union(*(agg["sales_docs"] for agg in customers.values())) if customers else set()
    customer_return_docs = set().union(*(agg["return_docs"] for agg in customers.values())) if customers else set()
    reconciliations = [
        {"name": "source_to_customer_amount", "status": "passed" if source_net == customer_sum else "failed", "delta": _decimal_delta(source_net - customer_sum)},
        {"name": "source_to_dimension_amount", "status": "passed" if source_net == dimension_sum else "failed", "delta": _decimal_delta(source_net - dimension_sum)},
        {"name": "source_to_processed_rows", "status": "passed" if processed == planned else "failed", "delta": str(processed - planned)},
        {"name": "source_to_customer_sales_documents", "status": "passed" if source_sales_docs == customer_sales_docs else "failed", "delta": str(len(source_sales_docs) - len(customer_sales_docs))},
        {"name": "source_to_customer_return_documents", "status": "passed" if source_return_docs == customer_return_docs else "failed", "delta": str(len(source_return_docs) - len(customer_return_docs))},
    ]
    if classification_enabled:
        reconciliations.append({"name": "customer_to_category_amount", "status": "passed" if customer_sum == terminal_sum else "failed", "delta": _decimal_delta(customer_sum - terminal_sum)})
    name_conflicts = sum(1 for names in entity_names.values() if len(names) > 1)
    quality = {
        "scope": "full",
        "planned_rows": planned,
        "processed_rows": processed,
        "valid_rows": valid,
        "invalid_rows": invalid,
        "skipped_rows": int(dataset.get("skipped_rows", 0)),
        "zero_amount_rows": zero_rows,
        "source_sales_document_count": len(source_sales_docs),
        "source_return_document_count": len(source_return_docs),
        "filtered_rows": filtered_rows,
        "transaction_sign_conflict_rows": sign_conflicts,
        "entity_name_conflict_count": name_conflicts,
        "classification_enabled": classification_enabled,
        "source_fingerprint": dataset.get("source_fingerprint", ""),
        "reconciliations": reconciliations,
        "pending_classification_count": len(pending_rows),
        "can_complete": invalid == 0 and sign_conflicts == 0 and not pending_rows and all(r["status"] == "passed" for r in reconciliations),
    }
    available_sheets = {
        "customer_sales": {"name": "客户销售汇总", "columns": _customer_columns(plan), "rows": customer_rows},
        "dimension_summary": {"name": "维度汇总", "columns": _dimension_columns(plan), "rows": dimension_rows},
        "classification_summary": {"name": "分类汇总", "columns": ["分类名称", "客户数", "客户数占比", "销售金额", "退货金额", "净销售额", "净销售额占比", "退货率"], "rows": terminal_rows},
        "pending_classification": {"name": "待确认分类", "columns": ["往来单位编号", "往来单位名称", "建议类型", "判断依据", "分类状态", "用户确认类型"], "rows": pending_rows},
    }
    sheets = []
    for output in plan.get("outputs", []):
        sheet = available_sheets.get(output.get("key"))
        if sheet:
            sheets.append({**sheet, "name": _display(output.get("name"))[:31] or sheet["name"]})
    return {"sheets": sheets, "quality": quality, "source_sales": _money(source_sales), "source_returns": _money(source_returns), "source_net": _money(source_net)}


def _customer_columns(plan: dict) -> list[str]:
    columns = ["往来单位编号", "往来单位名称"]
    if plan.get("time_grain"):
        columns.append({"year": "年度", "month": "月份", "quarter": "季度"}.get(plan["time_grain"], "统计周期"))
    columns.extend(plan.get("dimensions", []))
    if plan.get("classification", {}).get("enabled"):
        columns.extend(["分类名称", "分类状态", "分类依据"])
    columns.extend(["销售单数", "退货单数", "销售金额", "退货金额", "净销售额", "净销售额占比", "最近交易日期"])
    return columns


def _dimension_columns(plan: dict) -> list[str]:
    columns = []
    if not plan.get("time_grain") and not plan.get("dimensions"):
        columns.append("汇总范围")
    if plan.get("time_grain"):
        columns.append({"year": "年度", "month": "月份", "quarter": "季度"}.get(plan["time_grain"], "统计周期"))
    columns.extend(plan.get("dimensions", []))
    columns.extend(["销售单数", "退货单数", "销售金额", "退货金额", "净销售额", "净销售额占比", "最近交易日期"])
    return columns


def _write_results(sid: str, result: dict) -> None:
    manifest = read_manifest(sid)
    path = _session_dir(sid) / "result" / "result.sqlite3"
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE result_rows (sheet_name TEXT NOT NULL, row_index INTEGER NOT NULL, values_json TEXT NOT NULL, PRIMARY KEY(sheet_name, row_index))")
    summaries = []
    for sheet in result["sheets"]:
        rows = sheet["rows"]
        conn.executemany("INSERT INTO result_rows(sheet_name,row_index,values_json) VALUES (?,?,?)", [(sheet["name"], i, _json_dumps(row)) for i, row in enumerate(rows)])
        summaries.append({"name": sheet["name"], "columns": sheet["columns"], "rows": len(rows), "total_rows": len(rows), "preview": rows[:8]})
    conn.commit()
    conn.close()
    manifest["quality"] = result["quality"]
    if manifest.get("dataset") is not None:
        manifest["dataset"]["valid_rows"] = result["quality"].get("valid_rows")
    manifest["result_sheets"] = summaries
    manifest["result_summary"] = {
        "source_sales": result.get("source_sales", ""),
        "source_returns": result.get("source_returns", ""),
        "source_net": result.get("source_net", ""),
        "source_metrics": result.get("source_metrics", {}),
    }
    write_manifest(sid, manifest)


def run_import(sid: str, job: Job, model_config: dict | None = None) -> None:
    manifest = read_manifest(sid)
    if not manifest.get("files"):
        raise ComposeError("DATASET_INCOMPLETE", "至少上传一个源数据文件。")
    manifest["status"] = "SCANNING"
    write_manifest(sid, manifest)
    conn = _prepare_db(sid)
    entries = []
    samples_by_sheet: dict[str, list[list[Any]]] = defaultdict(list)
    sample_seen: dict[str, int] = defaultdict(int)
    sample_rng = random.Random(20260715)
    schema_columns: list[str] = []
    schema_indexes: dict[str, int] = {}
    profile_states: list[dict[str, Any]] | None = None
    skipped_rows = 0
    total_rows = 0
    try:
        for file_entry in manifest["files"]:
            path = _session_dir(sid) / "source" / file_entry["stored_name"]
            for sheet_name, raw_rows in _iter_workbook(path):
                if job.cancelled:
                    raise ComposeError("CANCELLED", "文件导入已取消。", status=409)
                preview = list(itertools.islice(raw_rows, 20))
                header_idx = _find_header(preview)
                columns = _unique_headers(preview[header_idx])
                if len(columns) > MAX_SOURCE_COLUMNS:
                    raise ComposeError("SCHEMA_TOO_WIDE", f"工作表 {sheet_name} 包含 {len(columns):,} 列，超过 {MAX_SOURCE_COLUMNS:,} 列的安全上限。")
                signature = hashlib.sha256(_json_dumps([_normalize(c).lower() for c in columns]).encode()).hexdigest()[:16]
                sheet_indexes = []
                for column in columns:
                    normalized = _normalize(column).lower()
                    if normalized not in schema_indexes:
                        schema_indexes[normalized] = len(schema_columns)
                        schema_columns.append(column)
                        state = _new_profile_states(1)[0]
                        state['rows'] = total_rows
                        if profile_states is None:
                            profile_states = []
                        profile_states.append(state)
                    sheet_indexes.append(schema_indexes[normalized])
                sheet_rows = 0
                batch = []
                stream = itertools.chain(preview[header_idx + 1 :], raw_rows)
                for source_row, raw in enumerate(stream, start=header_idx + 2):
                    sheet_values = list(raw[: len(columns)]) + [None] * max(0, len(columns) - len(raw))
                    if _empty_row(sheet_values):
                        skipped_rows += 1
                        continue
                    if [_normalize(v).lower() for v in sheet_values] == [_normalize(c).lower() for c in columns]:
                        skipped_rows += 1
                        continue
                    safe_values = [None] * len(schema_columns)
                    for source_index, target_index in enumerate(sheet_indexes):
                        safe_values[target_index] = _safe_json(sheet_values[source_index])
                    if profile_states is not None:
                        _update_profile_states(profile_states, safe_values)
                    batch.append((file_entry["filename"], sheet_name, source_row, _json_dumps(safe_values)))
                    sheet_rows += 1
                    total_rows += 1
                    if total_rows > MAX_SOURCE_ROWS:
                        raise ComposeError("DATASET_TOO_LARGE", f"源数据超过 {MAX_SOURCE_ROWS:,} 行的单任务安全上限，请拆分后处理。", status=413)
                    sample_seen[sheet_name] += 1
                    sheet_sample = samples_by_sheet[sheet_name]
                    if len(sheet_sample) < MAX_SAMPLE_ROWS:
                        sheet_sample.append(safe_values)
                    else:
                        replacement = sample_rng.randrange(sample_seen[sheet_name])
                        if replacement < MAX_SAMPLE_ROWS:
                            sheet_sample[replacement] = safe_values
                    if len(batch) >= INSERT_BATCH:
                        conn.executemany("INSERT INTO source_rows(source_file,sheet_name,source_row,values_json) VALUES (?,?,?,?)", batch)
                        conn.commit()
                        batch.clear()
                    if job and (total_rows == 1 or total_rows % 5000 == 0):
                        job.emit("progress", {"stage": "import_rows", "label": "读取源数据并建立本地缓存", "processed_rows": total_rows, "total_rows": 0, "invalid_rows": 0, "skipped_rows": skipped_rows, "current_file": file_entry["filename"], "current_sheet": sheet_name})
                if batch:
                    conn.executemany("INSERT INTO source_rows(source_file,sheet_name,source_row,values_json) VALUES (?,?,?,?)", batch)
                    conn.commit()
                entries.append({"file": file_entry["filename"], "sheet": sheet_name, "columns": columns, "signature": signature, "rows": sheet_rows})
                job.emit("progress", {"stage": "scan_sheet", "label": f"已识别工作表：{sheet_name}", "processed_rows": total_rows, "total_rows": total_rows, "invalid_rows": 0, "skipped_rows": skipped_rows, "current_file": file_entry["filename"], "current_sheet": sheet_name})
        conn.close()
        if not schema_columns:
            raise ComposeError("SCHEMA_CONFLICT", "没有读到有效数据表。")
        schema_signature = hashlib.sha256(_json_dumps([_normalize(c).lower() for c in schema_columns]).encode()).hexdigest()[:16]
        source_fingerprint = hashlib.sha256("".join(f["sha256"] for f in manifest["files"]).encode()).hexdigest()
        samples = [row for rows in samples_by_sheet.values() for row in rows]
        dataset = {
            "session_id": sid,
            "file_count": len(manifest["files"]),
            "sheet_count": len(entries),
            "planned_rows": total_rows,
            "total_rows": total_rows,
            "valid_rows": None,
            "skipped_rows": skipped_rows,
            "source_fingerprint": source_fingerprint,
            "schema": {"columns": schema_columns, "signature": schema_signature},
            "sheets": entries,
            "sample": {"scope": "per_sheet_reservoir_sample", "rows": len(samples), "max_per_sheet": MAX_SAMPLE_ROWS},
        }
        dataset["fields"] = _profile_fields(schema_columns, samples, _finalize_profile_states(profile_states or []))
        job.emit("progress", {"stage": "profile_evidence", "label": "核对字段关系和业务口径证据", "processed_rows": total_rows, "total_rows": total_rows, "invalid_rows": 0, "skipped_rows": skipped_rows})
        dataset["evidence"], edge_rows = _relationship_evidence(sid, dataset)
        preview_rows = []
        seen_preview = set()
        for row in [*edge_rows, *samples]:
            marker = _json_dumps(row)
            if marker in seen_preview:
                continue
            seen_preview.add(marker)
            preview_rows.append(row)
            if len(preview_rows) >= MAX_PREVIEW_SOURCE_ROWS:
                break
        _write_preview_samples(sid, schema_columns, preview_rows)
        dataset["readiness"] = _dataset_readiness(dataset)
        job.emit("progress", {"stage": "plan_directions", "label": "根据真实数据画像生成定制方案", "processed_rows": total_rows, "total_rows": total_rows, "invalid_rows": 0, "skipped_rows": skipped_rows})
        dataset["directions"], dataset["planning"] = _make_directions(dataset, model_config)
        job.emit("progress", {"stage": "preview_directions", "label": "验证候选方案并生成真实表格预览", "processed_rows": total_rows, "total_rows": total_rows, "invalid_rows": 0, "skipped_rows": skipped_rows})
        preview_candidates = list(dataset["directions"])
        preview_errors: list[str] = []
        _append_unique_directions(preview_candidates, _basic_table_directions(dataset), preview_errors, limit=6)
        dataset["directions"] = preview_candidates
        previews = _preview_directions(sid, dataset, preview_candidates, preview_rows, limit=12)
        accepted_directions = []
        for direction in preview_candidates:
            preview = previews.get(direction["id"])
            quality = preview.get("quality", {}) if isinstance(preview, dict) else {}
            if not preview or preview.get("error") or quality.get("can_complete") is False:
                preview_errors.append(f'「{direction.get("title")}」未通过真实样本执行校验。')
                continue
            direction["preview"] = preview
            accepted_directions.append(direction)
            if len(accepted_directions) >= 3:
                break
        for index, direction in enumerate(accepted_directions):
            direction["recommended"] = index == next((i for i, item in enumerate(accepted_directions) if item.get("recommended")), 0)
        dataset["directions"] = accepted_directions
        if preview_errors:
            dataset["planning"]["notice"] = ' '.join(filter(None, [
                dataset["planning"].get("notice", ""),
                f'样本执行阶段排除或跳过 {len(preview_errors)} 个候选。',
            ]))
            dataset["planning"]["rejected_count"] = int(dataset["planning"].get("rejected_count", 0)) + len(preview_errors)
        dataset["planning"]["accepted_count"] = len(accepted_directions)
        manifest["status"] = "READY"
        manifest["dataset"] = dataset
        write_manifest(sid, manifest)
        job.finish("success", {"dataset": dataset, "directions": dataset["directions"], "plan": None})
    except Exception:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            failed = read_manifest(sid)
            if failed.get("status") == "SCANNING":
                failed["status"] = "CANCELLED" if job.cancelled else "FAILED"
                write_manifest(sid, failed)
        except Exception:  # noqa: BLE001
            pass
        raise


def run_execute(sid: str, job: Job) -> None:
    manifest = read_manifest(sid)
    plan = validate_plan(manifest)
    manifest["status"] = "EXECUTING"
    write_manifest(sid, manifest)
    job.emit("progress", {"stage": "aggregate", "label": "按方案执行全量确定性计算", "processed_rows": 0, "total_rows": manifest["dataset"]["total_rows"], "invalid_rows": 0, "skipped_rows": manifest["dataset"].get("skipped_rows", 0)})
    try:
        result = _aggregate(sid, plan, job)
        if job.cancelled:
            raise ComposeError("CANCELLED", "全量执行已取消。", status=409)
        _write_results(sid, result)
        manifest = read_manifest(sid)
        manifest["status"] = "COMPLETED"
        write_manifest(sid, manifest)
        job.finish("success", {"quality": result["quality"], "result_sheets": manifest["result_sheets"], "result_summary": manifest["result_summary"]})
    except Exception:
        manifest = read_manifest(sid)
        if manifest.get("status") == "EXECUTING":
            manifest["status"] = "READY"
            write_manifest(sid, manifest)
        raise


def get_result_page(sid: str, sheet: str | None, offset: int, limit: int) -> dict:
    manifest = read_manifest(sid)
    if manifest.get("status") != "COMPLETED":
        raise ComposeError("DATASET_INCOMPLETE", "结果尚未生成。")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    summaries = manifest.get("result_sheets", [])
    chosen = next((s for s in summaries if s["name"] == sheet), summaries[0] if summaries else None)
    if chosen is None:
        raise ComposeError("RESULT_NOT_FOUND", "结果表不存在。", status=404)
    conn = sqlite3.connect(_session_dir(sid) / "result" / "result.sqlite3")
    rows = [json.loads(row[0]) for row in conn.execute("SELECT values_json FROM result_rows WHERE sheet_name=? ORDER BY row_index LIMIT ? OFFSET ?", (chosen["name"], limit, offset))]
    conn.close()
    return {"sheet": {"name": chosen["name"], "columns": chosen["columns"], "offset": offset, "limit": limit, "total_rows": chosen["total_rows"], "rows": rows}, "quality": manifest.get("quality"), "dataset": _dataset_summary(manifest.get("dataset")), "result_summary": manifest.get("result_summary", {})}


def _dataset_summary(dataset: dict | None) -> dict | None:
    if not dataset:
        return None
    return {key: value for key, value in dataset.items() if key not in {"directions", "fields"}}


def _preview_sample_path(sid: str) -> Path:
    return _session_dir(sid) / "cache" / "preview_sample.json"


def _write_preview_samples(sid: str, columns: list[str], rows: list[list[Any]]) -> None:
    _preview_sample_path(sid).write_text(
        json.dumps({"columns": columns, "rows": rows[:MAX_PREVIEW_SOURCE_ROWS]}, ensure_ascii=False, default=_safe_json),
        encoding="utf-8",
    )


def _read_preview_samples(sid: str) -> list[list[Any]]:
    path = _preview_sample_path(sid)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("rows", []))[:MAX_PREVIEW_SOURCE_ROWS]
    except Exception as exc:  # noqa: BLE001
        raise ComposeError("SESSION_CORRUPT", "方案预览样本损坏，请重新上传源表。", status=500) from exc


def _preview_plans(
    dataset: dict,
    plans: dict[str, dict],
    source_rows: list[list[Any]],
    *,
    limit: int,
) -> dict[str, dict]:
    if not source_rows:
        return {key: {"error": "没有可用于预览的代表性数据。"} for key in plans}
    preview_sid = uuid.uuid4().hex[:12]
    create_session(preview_sid)
    try:
        conn = _prepare_db(preview_sid)
        conn.executemany(
            "INSERT INTO source_rows(source_file,sheet_name,source_row,values_json) VALUES (?,?,?,?)",
            [("代表性样本", "方案预览", index + 2, _json_dumps(row)) for index, row in enumerate(source_rows)],
        )
        conn.commit()
        conn.close()
        preview_dataset = json.loads(json.dumps(dataset, ensure_ascii=False))
        preview_dataset["session_id"] = preview_sid
        preview_dataset["file_count"] = 1
        preview_dataset["sheet_count"] = 1
        preview_dataset["planned_rows"] = len(source_rows)
        preview_dataset["total_rows"] = len(source_rows)
        preview_dataset["valid_rows"] = None
        preview_dataset["skipped_rows"] = 0
        preview_dataset["source_fingerprint"] = f"{dataset.get('source_fingerprint', '')}:preview"
        preview_dataset["sheets"] = [{
            "file": "代表性样本",
            "sheet": "方案预览",
            "columns": preview_dataset.get("schema", {}).get("columns", []),
            "signature": preview_dataset.get("schema", {}).get("signature", ""),
            "rows": len(source_rows),
        }]
        results = {}
        manifest = read_manifest(preview_sid)
        manifest["dataset"] = preview_dataset
        for key, raw_plan in plans.items():
            plan = json.loads(json.dumps(raw_plan, ensure_ascii=False))
            plan["dataset_id"] = preview_sid
            plan["questions"] = []
            plan["ready_for_execution"] = True
            rules = plan.setdefault("business_rules", {})
            if plan.get("output_kind") != "generic_summary" and not _has_transaction_sign_rule(plan):
                rules["transaction_sign_source"] = "amount_sign"
            rules["zero_amount_policy_confirmed"] = True
            _set_outputs(plan)
            manifest["status"] = "READY"
            manifest["plan"] = plan
            write_manifest(preview_sid, manifest)
            try:
                result = _aggregate(preview_sid, plan)
                sheets = [{
                    "name": sheet["name"],
                    "columns": sheet["columns"],
                    "rows": sheet["rows"][:limit],
                    "sample_result_rows": len(sheet["rows"]),
                } for sheet in result["sheets"]]
                quality = result["quality"]
                warnings = []
                if int(quality.get("invalid_rows", 0)):
                    warnings.append(f"代表性样本中有 {int(quality['invalid_rows']):,} 行无法按当前规则计算。")
                if int(quality.get("zero_amount_rows", 0)):
                    warnings.append(f"代表性样本包含 {int(quality['zero_amount_rows']):,} 条零值记录。")
                results[key] = {
                    "scope": "representative_sample",
                    "source_rows": len(source_rows),
                    "max_rows": limit,
                    "sheets": sheets,
                    "warnings": warnings,
                    "quality": quality,
                }
            except ComposeError as exc:
                results[key] = {"error": exc.message, "code": exc.code}
        return results
    finally:
        cleanup_session(preview_sid)


def _preview_directions(
    sid: str,
    dataset: dict,
    directions: list[dict],
    source_rows: list[list[Any]] | None = None,
    *,
    limit: int = 12,
) -> dict[str, dict]:
    plans = {}
    for direction in directions:
        try:
            plans[direction["id"]] = make_plan(dataset, direction["id"])
        except ComposeError:
            continue
    previews = _preview_plans(dataset, plans, source_rows or _read_preview_samples(sid), limit=max(1, min(limit, MAX_PREVIEW_OUTPUT_ROWS)))
    by_id = {direction["id"]: direction for direction in directions}
    for direction_id, preview in previews.items():
        preview["estimated_total_rows"] = by_id.get(direction_id, {}).get("estimated_rows")
    return previews


def get_preview(sid: str, direction_id: str | None = None, limit: int = 100) -> dict:
    manifest = read_manifest(sid)
    dataset = manifest.get("dataset")
    if not dataset:
        return get_source_preview(sid, max_rows=max(1, min(int(limit), 20)))
    safe_limit = max(1, min(int(limit), MAX_PREVIEW_OUTPUT_ROWS))
    if direction_id:
        direction = next((item for item in dataset.get("directions", []) if item.get("id") == direction_id), None)
        if direction is None:
            raise ComposeError("PLAN_INVALID", "所选方案不存在或已经失效。", status=404)
        plan = make_plan(dataset, direction_id)
        preview = _preview_plans(dataset, {direction_id: plan}, _read_preview_samples(sid), limit=safe_limit)[direction_id]
        preview["estimated_total_rows"] = direction.get("estimated_rows")
        return preview
    plan = manifest.get("plan")
    if not plan:
        raise ComposeError("PLAN_INVALID", "请先选择或描述一个目标表方向。")
    return _preview_plans(dataset, {"plan": plan}, _read_preview_samples(sid), limit=safe_limit)["plan"]


def export_result(sid: str) -> Path:
    manifest = read_manifest(sid)
    if manifest.get("status") != "COMPLETED":
        raise ComposeError("DATASET_INCOMPLETE", "结果尚未生成。")
    path = _session_dir(sid) / "export" / "智能制表结果.xlsx"
    workbook = Workbook(write_only=True)
    conn = sqlite3.connect(_session_dir(sid) / "result" / "result.sqlite3")
    money_headers = {"销售金额", "退货金额", "净销售额", "金额", "销售额", "退货额", "净额"}
    percent_headers = {"净销售额占比", "客户数占比", "退货率", "占比"}
    count_headers = {"销售单数", "退货单数", "客户数", "行数", "有效行数", "异常行数"}
    used_titles: set[str] = set()
    try:
        for summary in manifest.get("result_sheets", []):
            base_title = re.sub(r"[\\/*?:\[\]]", "_", summary["name"])[:31] or "结果"
            title = base_title
            suffix = 2
            while title in used_titles:
                ending = f"_{suffix}"
                title = base_title[: 31 - len(ending)] + ending
                suffix += 1
            used_titles.add(title)
            ws = workbook.create_sheet(title=title)
            ws.freeze_panes = "A2"
            columns = summary["columns"]
            header_cells = []
            for header in columns:
                cell = WriteOnlyCell(ws, value=header)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="0F8A4D")
                cell.alignment = Alignment(horizontal="center", vertical="center")
                header_cells.append(cell)
            ws.append(header_cells)
            for index, header in enumerate(columns, start=1):
                width = 30 if "名称" in header or "依据" in header else 22 if "编号" in header else 18 if header in money_headers else 15
                ws.column_dimensions[get_column_letter(index)].width = width
            for (raw,) in conn.execute("SELECT values_json FROM result_rows WHERE sheet_name=? ORDER BY row_index", (summary["name"],)):
                row = json.loads(raw)
                converted = []
                for index, value in enumerate(row):
                    header = columns[index] if index < len(columns) else ""
                    cell_value: Any = value
                    number_format = None
                    if header in money_headers:
                        decimal_value = _to_decimal(value)
                        cell_value = float(decimal_value) if decimal_value is not None else value
                        number_format = "#,##0.00;[Red]-#,##0.00"
                    elif header in percent_headers and isinstance(value, str) and value.endswith("%"):
                        decimal_value = _to_decimal(value[:-1])
                        cell_value = float(decimal_value / Decimal("100")) if decimal_value is not None else value
                        number_format = "0.00%"
                    elif header in count_headers:
                        decimal_value = _to_decimal(value)
                        cell_value = int(decimal_value) if decimal_value is not None else value
                        number_format = "#,##0"
                    elif isinstance(cell_value, str) and cell_value.startswith(("=", "+", "-", "@")):
                        # Source values are data, never executable spreadsheet
                        # formulas. Prefixing with an apostrophe keeps the
                        # visible text while preventing formula injection.
                        cell_value = "'" + cell_value
                    cell = WriteOnlyCell(ws, value=cell_value)
                    if number_format:
                        cell.number_format = number_format
                    if header in money_headers or header in percent_headers or header in count_headers:
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    converted.append(cell)
                ws.append(converted)
            if columns:
                ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{int(summary.get('total_rows', 0)) + 1}"
        workbook.save(path)
    finally:
        conn.close()
    return path


def apply_classification(sid: str, mapping: dict[str, str]) -> dict:
    manifest = read_manifest(sid)
    if manifest.get("status") not in {"COMPLETED", "READY"}:
        raise ComposeError("DATASET_INCOMPLETE", "当前阶段不能修改分类。")
    cleaned = {str(k).strip(): str(v).strip() for k, v in mapping.items() if str(k).strip() and str(v).strip()}
    manifest["classification_mapping"] = {**manifest.get("classification_mapping", {}), **cleaned}
    write_manifest(sid, manifest)
    if manifest.get("plan"):
        result = _aggregate(sid, manifest["plan"])
        _write_results(sid, result)
        manifest = read_manifest(sid)
        manifest["status"] = "COMPLETED"
        write_manifest(sid, manifest)
        return {"quality": result["quality"], "result_sheets": manifest["result_sheets"], "result_summary": manifest["result_summary"]}
    return {"quality": manifest.get("quality"), "result_sheets": manifest.get("result_sheets", [])}


def execute_plan_on_frames(plan: dict, frames: dict[str, Any], classification_mapping: dict[str, str] | None = None) -> dict:
    """Apply a saved smart-table plan to an in-memory source handle.

    The existing ``/api/parse`` route still exposes pandas handles for the
    legacy run flow. This adapter keeps the new logic deterministic and uses
    the same SQLite-backed aggregator, without sending the data to a model.
    """
    if not frames:
        raise ComposeError("DATASET_INCOMPLETE", "没有可处理的源数据表。")
    sid = uuid.uuid4().hex[:12]
    create_session(sid)
    try:
        names = list(frames)
        first = frames[names[0]]
        columns = [str(column) for column in first.columns]
        signature = hashlib.sha256(_json_dumps([_normalize(c).lower() for c in columns]).encode()).hexdigest()[:16]
        conn = _prepare_db(sid)
        samples: list[list[Any]] = []
        total_rows = 0
        for name in names:
            frame = frames[name]
            actual_columns = [str(column) for column in frame.columns]
            actual_signature = hashlib.sha256(_json_dumps([_normalize(c).lower() for c in actual_columns]).encode()).hexdigest()[:16]
            if actual_signature != signature:
                raise ComposeError("SCHEMA_CONFLICT", f"工作表 {name} 的字段结构与已保存方案不一致。")
            batch = []
            for row_number, raw in enumerate(frame.itertuples(index=False, name=None), start=2):
                values = [_safe_json(value) for value in raw]
                if _empty_row(values):
                    continue
                batch.append((name, name, row_number, _json_dumps(values)))
                total_rows += 1
                if len(samples) < MAX_SAMPLE_ROWS:
                    samples.append(values)
                if len(batch) >= INSERT_BATCH:
                    conn.executemany("INSERT INTO source_rows(source_file,sheet_name,source_row,values_json) VALUES (?,?,?,?)", batch)
                    conn.commit()
                    batch.clear()
            if batch:
                conn.executemany("INSERT INTO source_rows(source_file,sheet_name,source_row,values_json) VALUES (?,?,?,?)", batch)
        conn.commit()
        conn.close()
        dataset = {
            "session_id": sid,
            "file_count": len(names),
            "sheet_count": len(names),
            "planned_rows": total_rows,
            "total_rows": total_rows,
            "skipped_rows": 0,
            "source_fingerprint": signature,
            "schema": {"columns": columns, "signature": signature},
            "sheets": [{"file": name, "sheet": name, "columns": columns, "signature": signature, "rows": len(frames[name])} for name in names],
            "sample": {"scope": "handle_sample", "rows": len(samples), "max_per_sheet": MAX_SAMPLE_ROWS},
        }
        dataset["fields"] = _profile_fields(columns, samples)
        manifest = read_manifest(sid)
        manifest["status"] = "READY"
        manifest["dataset"] = dataset
        manifest["plan"] = json.loads(json.dumps(plan, ensure_ascii=False))
        manifest["plan"]["dataset_id"] = sid
        manifest["classification_mapping"] = classification_mapping or {}
        write_manifest(sid, manifest)
        return _aggregate(sid, manifest["plan"])
    finally:
        cleanup_session(sid)


def cleanup_session(sid: str) -> None:
    directory = _session_dir(sid)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
