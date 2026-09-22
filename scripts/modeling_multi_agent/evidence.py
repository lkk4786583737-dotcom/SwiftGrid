from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from .ir import JoinCardinality, JoinIntent


class EvidenceKind(str, Enum):
    FIELD_PROFILE = "field_profile"
    UNIQUE_KEY = "unique_key"
    JOIN_RELATIONSHIP = "join_relationship"
    AGGREGATE = "aggregate"
    OTHER = "other"


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    kind: EvidenceKind
    payload: Mapping[str, Any]
    origin: str = "local_tool"
    verified: bool = True

    @property
    def is_trusted_fact(self) -> bool:
        return self.origin == "local_tool" and self.verified


@dataclass
class EvidenceStore:
    schemas: dict[str, frozenset[str]]
    records: dict[str, EvidenceRecord] = field(default_factory=dict)

    def add(self, record: EvidenceRecord) -> None:
        if record.id in self.records:
            raise ValueError(f"duplicate evidence id: {record.id}")
        self.records[record.id] = record

    def field_exists(self, sheet: str, field_name: str) -> bool:
        return field_name in self.schemas.get(sheet, frozenset())

    def trusted(self, evidence_id: str) -> EvidenceRecord | None:
        item = self.records.get(evidence_id)
        return item if item and item.is_trusted_fact else None

    def uniqueness(self, sheet: str, key_fields: Iterable[str], refs: Iterable[str] = ()) -> bool | None:
        keys = tuple(key_fields)
        candidates = self._candidate_records(refs)
        for item in candidates:
            if item.kind == EvidenceKind.UNIQUE_KEY:
                payload = item.payload
                if payload.get("sheet") == sheet and tuple(payload.get("key_fields", ())) == keys:
                    return bool(payload.get("is_unique")) and not int(payload.get("blank_key_rows", 0))
            if item.kind == EvidenceKind.JOIN_RELATIONSHIP:
                result = self._join_side_uniqueness(item.payload, sheet, keys)
                if result is not None:
                    return result
        return None

    def verify_join(self, intent: JoinIntent) -> tuple[bool, str]:
        item = self.trusted(intent.evidence_ref)
        if not item or item.kind != EvidenceKind.JOIN_RELATIONSHIP:
            return False, "join evidence is missing or is not trusted local evidence"
        payload = item.payload
        direct = (
            payload.get("left_sheet") == intent.left_sheet
            and payload.get("right_sheet") == intent.right_sheet
            and tuple(payload.get("left_keys", ())) == intent.left_keys
            and tuple(payload.get("right_keys", ())) == intent.right_keys
        )
        reverse = (
            payload.get("left_sheet") == intent.right_sheet
            and payload.get("right_sheet") == intent.left_sheet
            and tuple(payload.get("left_keys", ())) == intent.right_keys
            and tuple(payload.get("right_keys", ())) == intent.left_keys
        )
        if not (direct or reverse):
            return False, "join evidence does not match the declared sheets and keys"
        observed = str(payload.get("cardinality"))
        if reverse:
            observed = {
                "many_to_one": "one_to_many",
                "one_to_many": "many_to_one",
            }.get(observed, observed)
        if observed != intent.cardinality.value:
            return False, f"declared cardinality {intent.cardinality.value} conflicts with local evidence {observed}"
        if not bool(payload.get("safe_raw_join", False)):
            return False, "local evidence marks this raw join unsafe"
        return True, ""

    def _candidate_records(self, refs: Iterable[str]) -> list[EvidenceRecord]:
        ref_list = tuple(refs)
        if ref_list:
            return [item for ref in ref_list if (item := self.trusted(ref))]
        return [item for item in self.records.values() if item.is_trusted_fact]

    @staticmethod
    def _join_side_uniqueness(payload: Mapping[str, Any], sheet: str, keys: tuple[str, ...]) -> bool | None:
        for side in ("left", "right"):
            if payload.get(f"{side}_sheet") == sheet and tuple(payload.get(f"{side}_keys", ())) == keys:
                return bool(payload.get(f"{side}_unique")) and not int(payload.get(f"{side}_blank_key_rows", 0))
        return None

    @classmethod
    def from_legacy_safe_tools(
        cls,
        dataset_card: Mapping[str, Any],
        evidence_records: Iterable[Mapping[str, Any]],
    ) -> "EvidenceStore":
        schemas = {
            str(item.get("name") or item.get("sheet")): frozenset(item.get("columns", ()))
            for item in dataset_card.get("sheets", ())
        }
        store = cls(schemas=schemas)
        for index, raw in enumerate(evidence_records, start=1):
            evidence_id = str(raw.get("evidence_id") or raw.get("id") or f"legacy-{index}")
            if raw.get("status") != "verified":
                continue
            tool = raw.get("tool")
            arguments = raw.get("arguments", {})
            result = raw.get("result", {})
            if tool == "check_join_keys":
                left = result.get("left", {})
                right = result.get("right", {})
                keys = tuple(arguments.get("key_fields", result.get("key_fields", ())))
                payload = {
                    "left_sheet": arguments.get("left_sheet", left.get("sheet")),
                    "right_sheet": arguments.get("right_sheet", right.get("sheet")),
                    "left_keys": keys,
                    "right_keys": keys,
                    "cardinality": result.get("relationship_at_raw_grain"),
                    "safe_raw_join": bool(result.get("safe_raw_join")),
                    "left_unique": bool(left.get("unique_on_keys")),
                    "right_unique": bool(right.get("unique_on_keys")),
                    "left_blank_key_rows": int(left.get("blank_key_rows", 0)),
                    "right_blank_key_rows": int(right.get("blank_key_rows", 0)),
                }
                kind = EvidenceKind.JOIN_RELATIONSHIP
            elif tool == "check_key_uniqueness":
                payload = {
                    "sheet": result.get("source_sheet", arguments.get("source_sheet")),
                    "key_fields": tuple(result.get("key_fields", arguments.get("key_fields", ()))),
                    "is_unique": bool(result.get("unique_on_keys")),
                    "blank_key_rows": int(result.get("blank_key_rows", 0)),
                    "duplicate_key_rows": int(result.get("duplicate_key_rows", 0)),
                    "full_data_computation": bool(result.get("full_data_computation")),
                }
                kind = EvidenceKind.UNIQUE_KEY
            elif tool == "profile_columns":
                payload = {"source_sheet": arguments.get("source_sheet"), "profiles": result.get("profiles", ())}
                kind = EvidenceKind.FIELD_PROFILE
            elif tool in {"group_aggregate", "time_aggregate", "top_concentration"}:
                payload = {"arguments": arguments, "result": result}
                kind = EvidenceKind.AGGREGATE
            else:
                payload = {"tool": tool, "arguments": arguments, "result": result}
                kind = EvidenceKind.OTHER
            store.add(EvidenceRecord(evidence_id, kind, payload))
        return store


def unique_key_evidence(
    evidence_id: str,
    sheet: str,
    key_fields: Iterable[str],
    *,
    is_unique: bool,
    blank_key_rows: int = 0,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id,
        EvidenceKind.UNIQUE_KEY,
        {
            "sheet": sheet,
            "key_fields": tuple(key_fields),
            "is_unique": is_unique,
            "blank_key_rows": blank_key_rows,
        },
    )


def join_evidence(
    evidence_id: str,
    left_sheet: str,
    right_sheet: str,
    left_keys: Iterable[str],
    right_keys: Iterable[str],
    cardinality: JoinCardinality,
    *,
    safe_raw_join: bool = True,
    left_unique: bool = False,
    right_unique: bool = False,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id,
        EvidenceKind.JOIN_RELATIONSHIP,
        {
            "left_sheet": left_sheet,
            "right_sheet": right_sheet,
            "left_keys": tuple(left_keys),
            "right_keys": tuple(right_keys),
            "cardinality": cardinality.value,
            "safe_raw_join": safe_raw_join,
            "left_unique": left_unique,
            "right_unique": right_unique,
            "left_blank_key_rows": 0,
            "right_blank_key_rows": 0,
        },
    )
