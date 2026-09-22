from __future__ import annotations

import unittest
from dataclasses import replace

from scripts.modeling_multi_agent import (
    Additivity,
    ClarificationQuestion,
    CompilationStatus,
    Composition,
    DecisionLedger,
    DerivedExpression,
    DimensionBinding,
    DimensionIntent,
    DimensionTransform,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStore,
    IRParseError,
    JoinCardinality,
    JoinIntent,
    MetricIntent,
    MetricSemantic,
    ModelingContext,
    MultiAgentOrchestrator,
    Operation,
    PlanCompiler,
    SheetIntent,
    SortDirection,
    SortIntent,
    TransformKind,
    ValueReview,
    WorkbookIntentIR,
    canonicalize_workbook_intent,
    join_evidence,
    parse_workbook_intent,
    unique_key_evidence,
    workbook_intent_json_schema,
    workbook_intent_to_dict,
)


CARD = "打卡记录"
OVERTIME = "加班申请"
EMPLOYEE = "员工信息"
SALES = "销售明细"
TARGET = "销售目标"
REGION = "区域信息"
PRODUCT = "商品信息"


def binding(
    binding_id: str,
    sheet: str,
    source_field: str,
    evidence: str,
    *,
    kind: TransformKind = TransformKind.IDENTITY,
    parameters: dict | None = None,
    decisions: tuple[str, ...] = (),
) -> DimensionBinding:
    return DimensionBinding(
        binding_id,
        sheet,
        (source_field,),
        DimensionTransform(kind, parameters or {}),
        (evidence,),
        decisions,
    )


def dimension(dimension_id: str, *bindings: DimensionBinding) -> DimensionIntent:
    return DimensionIntent(dimension_id, dimension_id, bindings)


def metric(
    metric_id: str,
    name: str,
    source_sheet: str | None,
    source_fields: tuple[str, ...],
    source_grain: tuple[str, ...],
    target_grain: tuple[str, ...],
    dimension_bindings: tuple[str, ...],
    operation: Operation,
    semantic: MetricSemantic,
    entity_key: tuple[str, ...],
    additivity: Additivity,
    composition: Composition,
    evidence_refs: tuple[str, ...],
    **kwargs,
) -> MetricIntent:
    return MetricIntent(
        metric_id,
        name,
        source_sheet,
        source_fields,
        source_grain,
        target_grain,
        dimension_bindings,
        operation,
        semantic,
        entity_key,
        additivity,
        composition,
        evidence_refs,
        **kwargs,
    )


def sheet(
    sheet_id: str,
    dimensions: tuple[DimensionIntent, ...],
    metrics: tuple[MetricIntent, ...],
) -> SheetIntent:
    grain = tuple(item.id for item in dimensions)
    return SheetIntent(sheet_id, sheet_id, sheet_id, dimensions, grain, metrics)


def workbook(
    *sheets: SheetIntent,
    joins: tuple[JoinIntent, ...] = (),
    clarifications: tuple[ClarificationQuestion, ...] = (),
) -> WorkbookIntentIR:
    return WorkbookIntentIR("wb", "测试工作簿", "确定性建模", sheets, joins, clarifications)


class ModelingMultiAgentIRTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = EvidenceStore(
            schemas={
                CARD: frozenset({"日期", "工号", "上班打卡", "下班打卡"}),
                OVERTIME: frozenset({"日期", "工号", "加班小时", "加班类型"}),
                EMPLOYEE: frozenset({"工号", "姓名", "部门", "基本工资"}),
                SALES: frozenset({"订单号", "日期", "区域代码", "商品代码", "销售额", "渠道", "客户ID"}),
                TARGET: frozenset({"月份", "区域代码", "目标额"}),
                REGION: frozenset({"区域代码", "区域"}),
                PRODUCT: frozenset({"商品代码", "品类"}),
            }
        )
        for item in (
            EvidenceRecord("E_CARD", EvidenceKind.FIELD_PROFILE, {"source_sheet": CARD}),
            EvidenceRecord("E_OT", EvidenceKind.FIELD_PROFILE, {"source_sheet": OVERTIME}),
            EvidenceRecord("E_EMP", EvidenceKind.FIELD_PROFILE, {"source_sheet": EMPLOYEE}),
            EvidenceRecord("E_SALES", EvidenceKind.FIELD_PROFILE, {"source_sheet": SALES}),
            EvidenceRecord("E_TARGET", EvidenceKind.FIELD_PROFILE, {"source_sheet": TARGET}),
            EvidenceRecord("E_REGION", EvidenceKind.FIELD_PROFILE, {"source_sheet": REGION}),
            EvidenceRecord("E_PRODUCT", EvidenceKind.FIELD_PROFILE, {"source_sheet": PRODUCT}),
            unique_key_evidence("E_EMP_UNIQUE", EMPLOYEE, ("工号",), is_unique=True),
            unique_key_evidence("E_CARD_DAY_DUP", CARD, ("工号", "日期"), is_unique=False),
            join_evidence(
                "E_CARD_EMP", CARD, EMPLOYEE, ("工号",), ("工号",),
                JoinCardinality.MANY_TO_ONE, right_unique=True,
            ),
            join_evidence(
                "E_OT_EMP", OVERTIME, EMPLOYEE, ("工号",), ("工号",),
                JoinCardinality.MANY_TO_ONE, right_unique=True,
            ),
            join_evidence(
                "E_SALES_REGION", SALES, REGION, ("区域代码",), ("区域代码",),
                JoinCardinality.MANY_TO_ONE, right_unique=True,
            ),
            join_evidence(
                "E_SALES_PRODUCT", SALES, PRODUCT, ("商品代码",), ("商品代码",),
                JoinCardinality.MANY_TO_ONE, right_unique=True,
            ),
        ):
            self.evidence.add(item)
        self.card_employee = JoinIntent(
            "j_card_employee", CARD, EMPLOYEE, ("工号",), ("工号",),
            JoinCardinality.MANY_TO_ONE, "E_CARD_EMP",
        )
        self.overtime_employee = JoinIntent(
            "j_ot_employee", OVERTIME, EMPLOYEE, ("工号",), ("工号",),
            JoinCardinality.MANY_TO_ONE, "E_OT_EMP",
        )
        self.sales_region = JoinIntent(
            "j_sales_region", SALES, REGION, ("区域代码",), ("区域代码",),
            JoinCardinality.MANY_TO_ONE, "E_SALES_REGION",
        )
        self.sales_product = JoinIntent(
            "j_sales_product", SALES, PRODUCT, ("商品代码",), ("商品代码",),
            JoinCardinality.MANY_TO_ONE, "E_SALES_PRODUCT",
        )

    def compile(self, value: WorkbookIntentIR, decisions: DecisionLedger | None = None):
        return PlanCompiler(self.evidence, decisions).compile(value)

    def test_canonicalizer_resolves_only_unambiguous_redundant_model_fields(self) -> None:
        region = dimension(
            "region",
            binding("b_sales_region", SALES, "区域代码", "E_SALES"),
            binding("b_target_region", TARGET, "区域代码", "E_TARGET"),
        )
        noisy_sales = metric(
            "m_sales_noisy", "销售额", SALES, ("销售额",), ("订单号", "区域代码"),
            ("region",), ("region",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        noisy_derived = metric(
            "m_derived_noisy", "销售额副本", SALES, ("销售额",), ("region",),
            ("region",), ("region",), Operation.DERIVED,
            MetricSemantic.DERIVED_MEASURE, ("订单号",), Additivity.NON_ADDITIVE,
            Composition.RECOMPUTE, ("E_SALES",), dependencies=("m_sales_noisy",),
            expression=DerivedExpression("multiply_constant", ("m_sales_noisy",), parameters={"factor": 1}),
        )
        normalized = canonicalize_workbook_intent(workbook(sheet(
            "s_canonicalize", (region,), (noisy_sales, noisy_derived),
        )))
        sales_metric, derived_metric = normalized.sheets[0].metrics
        self.assertEqual(("b_sales_region",), sales_metric.dimension_binding_ids)
        self.assertIsNone(derived_metric.source_sheet)
        self.assertEqual((), derived_metric.source_fields)
        self.assertEqual((), derived_metric.source_grain)
        self.assertEqual((), derived_metric.dimension_binding_ids)
        self.assertEqual((), derived_metric.entity_key)

    def test_same_source_target_dimension_is_in_scan_required_fields(self) -> None:
        dept = dimension("department", binding("b_emp_dept", EMPLOYEE, "部门", "E_EMP"))
        salary = metric(
            "m_salary", "部门工资", EMPLOYEE, ("基本工资",), ("工号",),
            ("department",), ("b_emp_dept",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_EMP",),
        )
        result = self.compile(workbook(sheet("s_salary", (dept,), (salary,))))
        self.assertEqual(CompilationStatus.READY, result.status)
        scan = next(node for node in result.plan.nodes if node.id == "m_salary__scan")
        self.assertEqual({"工号", "基本工资", "部门"}, set(scan.params["fields"]))

    def test_enrichment_projection_explicitly_brings_back_target_fields(self) -> None:
        dimensions = (
            dimension("employee_id", binding("b_emp_id", EMPLOYEE, "工号", "E_EMP")),
            dimension("employee_name", binding("b_emp_name", EMPLOYEE, "姓名", "E_EMP")),
            dimension("department", binding("b_emp_dept", EMPLOYEE, "部门", "E_EMP")),
        )
        attendance = metric(
            "m_days", "出勤天数", CARD, (), ("工号", "日期"),
            tuple(item.id for item in dimensions),
            ("b_emp_id", "b_emp_name", "b_emp_dept"),
            Operation.COUNT_DISTINCT, MetricSemantic.ENTITY_COUNT, ("日期",),
            Additivity.NON_ADDITIVE, Composition.DISTINCT_SET, ("E_CARD",),
        )
        result = self.compile(workbook(
            sheet("s_employee", dimensions, (attendance,)),
            joins=(self.card_employee,),
        ))
        self.assertEqual(CompilationStatus.READY, result.status)
        enrich = next(node for node in result.plan.nodes if node.kind == "enrich_many_to_one")
        self.assertEqual({"工号", "姓名", "部门"}, set(enrich.params["projection"]))
        self.assertEqual(
            {"工号": f"{EMPLOYEE}.工号", "姓名": f"{EMPLOYEE}.姓名", "部门": f"{EMPLOYEE}.部门"},
            enrich.params["projection_provenance"],
        )

    def test_multiple_lookup_targets_compile_as_an_enrichment_tree(self) -> None:
        dimensions = (
            dimension("region_name", binding("b_region_name", REGION, "区域", "E_REGION")),
            dimension("product_category", binding("b_product_category", PRODUCT, "品类", "E_PRODUCT")),
        )
        sales = metric(
            "m_sales", "销售额", SALES, ("销售额",), ("订单号", "区域代码", "商品代码"),
            ("region_name", "product_category"), ("b_region_name", "b_product_category"),
            Operation.SUM, MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        result = self.compile(workbook(
            sheet("s_tree", dimensions, (sales,)),
            joins=(self.sales_region, self.sales_product),
        ))
        self.assertEqual(CompilationStatus.READY, result.status)
        enrich = [node for node in result.plan.nodes if node.kind == "enrich_many_to_one"]
        self.assertEqual(2, len(enrich))
        self.assertEqual({SALES}, {node.params["from_sheet"] for node in enrich})
        projection_by_target = {node.params["to_sheet"]: set(node.params["projection"]) for node in enrich}
        self.assertTrue({"区域代码", "区域"} <= projection_by_target[REGION])
        self.assertTrue({"商品代码", "品类"} <= projection_by_target[PRODUCT])

    def test_legacy_key_uniqueness_evidence_supports_value_semantics(self) -> None:
        card = {"sheets": [{"name": CARD, "columns": ["工号", "日期", "上班打卡"]}]}
        records = [{
            "evidence_id": "E_UNIQUE",
            "status": "verified",
            "tool": "check_key_uniqueness",
            "arguments": {"source_sheet": CARD, "key_fields": ["工号", "日期"]},
            "result": {
                "source_sheet": CARD,
                "key_fields": ["工号", "日期"],
                "blank_key_rows": 0,
                "duplicate_key_rows": 0,
                "unique_on_keys": True,
                "full_data_computation": True,
            },
        }]
        store = EvidenceStore.from_legacy_safe_tools(card, records)
        dimensions = (
            dimension("employee_id", binding("b_card_id", CARD, "工号", "E_UNIQUE")),
            dimension("work_date", binding("b_card_date", CARD, "日期", "E_UNIQUE")),
        )
        clock = metric(
            "m_clock", "上班打卡", CARD, ("上班打卡",), ("工号", "日期"),
            ("employee_id", "work_date"), ("b_card_id", "b_card_date"),
            Operation.VALUE, MetricSemantic.ATTRIBUTE_VALUE, (), Additivity.NON_ADDITIVE,
            Composition.RECOMPUTE, ("E_UNIQUE",), uniqueness_required=True,
        )
        result = PlanCompiler(store).compile(workbook(sheet("s_clock", dimensions, (clock,))))
        self.assertEqual(CompilationStatus.READY, result.status)
        self.assertEqual(EvidenceKind.UNIQUE_KEY, store.records["E_UNIQUE"].kind)

    def test_all_dimension_transforms_compile_to_deterministic_nodes(self) -> None:
        dimensions = (
            dimension("region_code", binding("b_region", SALES, "区域代码", "E_SALES")),
            dimension("sales_month", binding(
                "b_month", SALES, "日期", "E_SALES",
                kind=TransformKind.DATE_BUCKET, parameters={"grain": "month"},
            )),
            dimension("amount_band", binding(
                "b_amount_band", SALES, "销售额", "E_SALES",
                kind=TransformKind.NUMERIC_BUCKET,
                parameters={"boundaries": [1000, 5000], "labels": ["低", "中", "高"], "closed": "left"},
                decisions=("D_AMOUNT_BAND",),
            )),
            dimension("channel_class", binding(
                "b_channel", SALES, "渠道", "E_SALES",
                kind=TransformKind.CATEGORICAL_RULE,
                parameters={
                    "rules": [{"label": "线上", "operator": "in", "values": ["电商", "小程序"]}],
                    "default_label": "线下",
                },
                decisions=("D_CHANNEL_CLASS",),
            )),
        )
        rows = metric(
            "m_rows", "记录数", SALES, (),
            ("订单号", "日期", "区域代码", "销售额", "渠道"),
            tuple(item.id for item in dimensions),
            tuple(item.bindings[0].id for item in dimensions),
            Operation.COUNT_ROWS, MetricSemantic.RECORD_COUNT, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        questions = (
            ClarificationQuestion("D_AMOUNT_BAND", "请确认金额分层", "边界影响分组", ("b_amount_band",), True),
            ClarificationQuestion("D_CHANNEL_CLASS", "请确认渠道分组", "规则影响分组", ("b_channel",), True),
        )
        decisions = DecisionLedger()
        decisions.confirm("D_AMOUNT_BAND", [1000, 5000])
        decisions.confirm("D_CHANNEL_CLASS", {"线上": ["电商", "小程序"]})
        result = self.compile(workbook(sheet("s_transforms", dimensions, (rows,)), clarifications=questions), decisions)
        self.assertEqual(CompilationStatus.READY, result.status)
        transforms = {
            node.params["transform"]: node
            for node in result.plan.nodes
            if node.kind == "derive_dimension"
        }
        self.assertEqual({item.value for item in TransformKind}, set(transforms))
        self.assertTrue(all(node.params["deterministic"] for node in transforms.values()))
        self.assertEqual("month", transforms["date_bucket"].params["parameters"]["grain"])

    def test_business_bucket_decision_blocks_only_when_declared(self) -> None:
        band_binding = binding(
            "b_amount_band", SALES, "销售额", "E_SALES",
            kind=TransformKind.NUMERIC_BUCKET,
            parameters={"boundaries": [1000], "labels": ["低", "高"], "closed": "left"},
            decisions=("D_BAND",),
        )
        band = dimension("amount_band", band_binding)
        rows = metric(
            "m_rows", "记录数", SALES, (), ("订单号", "销售额"),
            ("amount_band",), ("b_amount_band",), Operation.COUNT_ROWS,
            MetricSemantic.RECORD_COUNT, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        question = ClarificationQuestion(
            "D_BAND", "请确认分层边界", "边界会改变每组归属", ("b_amount_band",), True,
        )
        intent = workbook(sheet("s_band", (band,), (rows,)), clarifications=(question,))
        self.assertEqual(CompilationStatus.BLOCKED, self.compile(intent).status)
        decisions = DecisionLedger()
        decisions.confirm("D_BAND", [1000])
        self.assertEqual(CompilationStatus.READY, self.compile(intent, decisions).status)

    def test_business_bucket_without_decision_is_rejected(self) -> None:
        band = dimension(
            "amount_band",
            binding(
                "b_unconfirmed_band",
                SALES,
                "销售额",
                "E_SALES",
                kind=TransformKind.NUMERIC_BUCKET,
                parameters={"boundaries": [1000], "labels": ["低", "高"], "closed": "left"},
            ),
        )
        rows = metric(
            "m_unconfirmed_rows", "记录数", SALES, (), ("订单号", "销售额"),
            ("amount_band",), ("b_unconfirmed_band",), Operation.COUNT_ROWS,
            MetricSemantic.RECORD_COUNT, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        result = self.compile(workbook(sheet("s_unconfirmed_band", (band,), (rows,))))
        self.assertEqual(CompilationStatus.INVALID, result.status)
        self.assertIn("UNCONFIRMED_BUSINESS_BUCKET", {item.code for item in result.diagnostics})

    def test_actual_and_target_merge_on_canonical_grain_and_compute_ratio_of_sums(self) -> None:
        dimensions = (
            dimension(
                "month",
                binding(
                    "b_sales_month", SALES, "日期", "E_SALES",
                    kind=TransformKind.DATE_BUCKET, parameters={"grain": "month"},
                ),
                binding("b_target_month", TARGET, "月份", "E_TARGET"),
            ),
            dimension(
                "region",
                binding("b_sales_region", SALES, "区域代码", "E_SALES"),
                binding("b_target_region", TARGET, "区域代码", "E_TARGET"),
            ),
        )
        canonical = ("month", "region")
        actual = metric(
            "m_actual", "实际", SALES, ("销售额",), ("订单号", "日期", "区域代码"),
            canonical, ("b_sales_month", "b_sales_region"), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        target = metric(
            "m_target", "目标", TARGET, ("目标额",), ("月份", "区域代码"),
            canonical, ("b_target_month", "b_target_region"), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_TARGET",),
        )
        achievement = metric(
            "m_achievement", "达成率", None, (), (), canonical, (),
            Operation.DERIVED, MetricSemantic.DERIVED_MEASURE, (), Additivity.NON_ADDITIVE,
            Composition.RATIO_OF_SUMS, ("E_SALES", "E_TARGET"),
            dependencies=("m_actual", "m_target"),
            expression=DerivedExpression("divide", ("m_actual", "m_target")),
        )
        result = self.compile(workbook(sheet("s_achievement", dimensions, (actual, target, achievement))))
        self.assertEqual(CompilationStatus.READY, result.status)
        merge = next(node for node in result.plan.nodes if node.kind == "merge_metric_branches")
        self.assertEqual(canonical, merge.params["canonical_keys"])
        self.assertEqual(("m_actual__rollup", "m_target__rollup"), merge.inputs)
        self.assertTrue(all(
            next(i for i, node in enumerate(result.plan.nodes) if node.id == branch)
            < next(i for i, node in enumerate(result.plan.nodes) if node.id == merge.id)
            for branch in merge.inputs
        ))
        derive = next(node for node in result.plan.nodes if node.id == "m_achievement__derive")
        self.assertEqual("ratio_of_sums", derive.params["composition"])
        self.assertEqual("divide", derive.params["operator"])

        invalid_ratio = replace(
            achievement,
            dependencies=("m_actual", "m_bad_average"),
            expression=DerivedExpression("divide", ("m_actual", "m_bad_average")),
        )
        bad_average = replace(
            target,
            id="m_bad_average",
            operation=Operation.AVERAGE,
            semantic=MetricSemantic.NON_ADDITIVE_MEASURE,
            additivity=Additivity.NON_ADDITIVE,
            composition=Composition.WEIGHTED_AVERAGE,
        )
        rejected = self.compile(workbook(sheet(
            "s_bad_ratio",
            dimensions,
            (actual, bad_average, invalid_ratio),
        )))
        self.assertEqual(CompilationStatus.INVALID, rejected.status)
        self.assertIn("INVALID_RATIO_OF_SUMS", {item.code for item in rejected.diagnostics})

    def test_raw_many_to_many_fact_join_remains_forbidden(self) -> None:
        self.evidence.add(join_evidence(
            "E_FACT_M2M",
            SALES,
            TARGET,
            ("区域代码",),
            ("区域代码",),
            JoinCardinality.MANY_TO_MANY,
            safe_raw_join=False,
        ))
        unsafe = JoinIntent(
            "j_fact_m2m",
            SALES,
            TARGET,
            ("区域代码",),
            ("区域代码",),
            JoinCardinality.MANY_TO_MANY,
            "E_FACT_M2M",
        )
        region = dimension("region", binding("b_sales_region", SALES, "区域代码", "E_SALES"))
        actual = metric(
            "m_actual", "实际", SALES, ("销售额",), ("订单号", "区域代码"),
            ("region",), ("b_sales_region",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        result = self.compile(workbook(sheet("s_actual", (region,), (actual,)), joins=(unsafe,)))
        self.assertEqual(CompilationStatus.INVALID, result.status)
        self.assertIn("UNSAFE_MANY_TO_MANY", {item.code for item in result.diagnostics})

    def test_reasonable_intermediate_metric_is_compiled_without_name_inference(self) -> None:
        dept = dimension("department", binding("b_emp_dept", EMPLOYEE, "部门", "E_EMP"))
        hours = metric(
            "m_hours", "任意展示名称", OVERTIME, ("加班小时",), ("工号",),
            ("department",), ("b_emp_dept",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        result = self.compile(workbook(
            sheet("s_dept", (dept,), (hours,)),
            joins=(self.overtime_employee,),
        ))
        self.assertEqual(CompilationStatus.READY, result.status)
        nodes = {node.id: node for node in result.plan.nodes}
        self.assertEqual("pre_aggregate_to_source_grain", nodes["m_hours__preaggregate"].kind)
        self.assertEqual("sum_partials", nodes["m_hours__rollup"].params["operation"])
        self.assertNotIn(hours.name, nodes["m_hours__preaggregate"].params["partial_column"])

    def test_count_rows_cannot_represent_people(self) -> None:
        employee = dimension("employee_id", binding("b_ot_employee", OVERTIME, "工号", "E_OT"))
        people = metric(
            "m_people", "加班员工数", OVERTIME, (), ("工号",),
            ("employee_id",), ("b_ot_employee",), Operation.COUNT_ROWS,
            MetricSemantic.ENTITY_COUNT, ("工号",), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        result = self.compile(workbook(sheet("s_people", (employee,), (people,))))
        self.assertEqual(CompilationStatus.INVALID, result.status)
        self.assertTrue({"INVALID_COMPOSITION", "ROW_COUNT_HAS_ENTITY"} <= {item.code for item in result.diagnostics})

    def test_department_rollup_requires_composable_operation(self) -> None:
        dept = dimension("department", binding("b_emp_dept", EMPLOYEE, "部门", "E_EMP"))
        wrong = metric(
            "m_salary", "部门工资", EMPLOYEE, ("基本工资",), ("工号",),
            ("department",), ("b_emp_dept",), Operation.VALUE,
            MetricSemantic.ATTRIBUTE_VALUE, (), Additivity.NON_ADDITIVE,
            Composition.RECOMPUTE, ("E_EMP_UNIQUE",), uniqueness_required=True,
        )
        intent_sheet = sheet("s_salary", (dept,), (wrong,))
        rejected = self.compile(workbook(intent_sheet))
        self.assertEqual(CompilationStatus.INVALID, rejected.status)
        self.assertIn("NON_COMPOSABLE_ROLLUP", {item.code for item in rejected.diagnostics})

        correct = replace(
            wrong,
            operation=Operation.SUM,
            semantic=MetricSemantic.ADDITIVE_MEASURE,
            additivity=Additivity.ADDITIVE,
            composition=Composition.SUM_PARTIALS,
            uniqueness_required=False,
            evidence_refs=("E_EMP",),
        )
        accepted = self.compile(workbook(replace(intent_sheet, metrics=(correct,))))
        self.assertEqual(CompilationStatus.READY, accepted.status)

    def test_unique_value_can_retain_additional_identity_attributes(self) -> None:
        dimensions = (
            dimension("employee_id", binding("b_emp_id", EMPLOYEE, "工号", "E_EMP")),
            dimension("department", binding("b_emp_dept", EMPLOYEE, "部门", "E_EMP")),
        )
        salary = metric(
            "m_salary", "employee salary", EMPLOYEE, ("基本工资",), ("工号",),
            ("employee_id", "department"), ("b_emp_id", "b_emp_dept"),
            Operation.VALUE, MetricSemantic.ATTRIBUTE_VALUE, (), Additivity.NON_ADDITIVE,
            Composition.RECOMPUTE, ("E_EMP_UNIQUE",), uniqueness_required=True,
        )
        result = self.compile(workbook(sheet("s_salary", dimensions, (salary,))))
        self.assertEqual(CompilationStatus.READY, result.status)

    def test_false_uniqueness_claim_is_rejected_by_local_evidence(self) -> None:
        dimensions = (
            dimension("employee_id", binding("b_card_emp", CARD, "工号", "E_CARD")),
            dimension("work_date", binding("b_card_date", CARD, "日期", "E_CARD")),
        )
        clock = metric(
            "m_clock", "每日打卡值", CARD, ("上班打卡",), ("工号", "日期"),
            ("employee_id", "work_date"), ("b_card_emp", "b_card_date"),
            Operation.VALUE, MetricSemantic.ATTRIBUTE_VALUE, (), Additivity.NON_ADDITIVE,
            Composition.RECOMPUTE, ("E_CARD_DAY_DUP",), uniqueness_required=True,
        )
        result = self.compile(workbook(sheet("s_clock", dimensions, (clock,))))
        self.assertEqual(CompilationStatus.INVALID, result.status)
        self.assertIn("FALSE_UNIQUENESS_CLAIM", {item.code for item in result.diagnostics})

    def test_unconfirmed_derived_metric_blocks_until_user_decision(self) -> None:
        work_date = dimension("work_date", binding("b_ot_date", OVERTIME, "日期", "E_OT"))
        hours = metric(
            "m_hours", "加班小时", OVERTIME, ("加班小时",), ("日期",),
            ("work_date",), ("b_ot_date",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        cost = metric(
            "m_cost", "加班费", None, (), (), ("work_date",), (),
            Operation.DERIVED, MetricSemantic.DERIVED_MEASURE, (), Additivity.ADDITIVE,
            Composition.RECOMPUTE, ("E_OT",), dependencies=("m_hours",),
            required_decision_ids=("D_RATE",),
            expression=DerivedExpression("multiply_constant", ("m_hours",), parameters={"factor": 1.5}),
        )
        question = ClarificationQuestion(
            "D_RATE", "请确认加班费率", "费率会改变加班费计算结果", ("m_cost",), True,
        )
        intent = workbook(sheet("s_cost", (work_date,), (hours, cost)), clarifications=(question,))
        self.assertEqual(CompilationStatus.BLOCKED, self.compile(intent).status)
        decisions = DecisionLedger()
        decisions.confirm("D_RATE", {"weekday": 1.5})
        self.assertEqual(CompilationStatus.READY, self.compile(intent, decisions).status)

    def test_unique_question_binding_canonicalizes_mismatched_decision_id(self) -> None:
        work_date = dimension("work_date", binding("b_ot_date", OVERTIME, "日期", "E_OT"))
        hours = metric(
            "m_hours", "overtime hours", OVERTIME, ("加班小时",), ("日期",),
            ("work_date",), ("b_ot_date",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        cost = metric(
            "m_cost", "overtime cost", None, (), (), ("work_date",), (),
            Operation.DERIVED, MetricSemantic.DERIVED_MEASURE, (), Additivity.ADDITIVE,
            Composition.RECOMPUTE, ("E_OT",), dependencies=("m_hours",),
            required_decision_ids=("DECIDE_RATE",),
            expression=DerivedExpression("multiply_constant", ("m_hours",), parameters={"factor": 1.5}),
        )
        question = ClarificationQuestion(
            "CLARIFY_RATE", "confirm rate", "rate changes result", ("m_cost",), True,
        )
        normalized = canonicalize_workbook_intent(
            workbook(sheet("s_cost", (work_date,), (hours, cost)), clarifications=(question,))
        )
        self.assertEqual(("CLARIFY_RATE",), normalized.sheets[0].metrics[1].required_decision_ids)
        self.assertEqual(CompilationStatus.BLOCKED, self.compile(normalized).status)

    def test_unknown_derived_operator_is_rejected(self) -> None:
        work_date = dimension("work_date", binding("b_unknown_op_date", OVERTIME, "日期", "E_OT"))
        hours = metric(
            "m_unknown_op_hours", "加班小时", OVERTIME, ("加班小时",), ("日期",),
            ("work_date",), ("b_unknown_op_date",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        invented = metric(
            "m_invented", "模型发明公式", None, (), (), ("work_date",), (),
            Operation.DERIVED, MetricSemantic.DERIVED_MEASURE, (), Additivity.NON_ADDITIVE,
            Composition.RECOMPUTE, ("E_OT",), dependencies=("m_unknown_op_hours",),
            expression=DerivedExpression("model_magic_formula", ("m_unknown_op_hours",)),
        )
        result = self.compile(workbook(sheet("s_unknown_operator", (work_date,), (hours, invented))))
        self.assertEqual(CompilationStatus.INVALID, result.status)
        self.assertIn("UNSUPPORTED_DERIVED_OPERATOR", {item.code for item in result.diagnostics})

    def test_optional_question_does_not_block_current_plan(self) -> None:
        work_date = dimension("work_date", binding("b_ot_date", OVERTIME, "日期", "E_OT"))
        hours = metric(
            "m_hours", "加班小时", OVERTIME, ("加班小时",), ("日期",),
            ("work_date",), ("b_ot_date",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        optional = ClarificationQuestion("Q_OPTION", "是否展示类型", "只影响扩展", ("m_hours",), False)
        result = self.compile(workbook(
            sheet("s_time", (work_date,), (hours,)),
            clarifications=(optional,),
        ))
        self.assertEqual(CompilationStatus.READY, result.status)

    def test_correct_employee_department_and_time_plan_compiles(self) -> None:
        employee_dimensions = (
            dimension("employee_id", binding("b_emp_id", EMPLOYEE, "工号", "E_EMP")),
            dimension("employee_name", binding("b_emp_name", EMPLOYEE, "姓名", "E_EMP")),
            dimension("employee_department", binding("b_emp_department", EMPLOYEE, "部门", "E_EMP")),
        )
        days = metric(
            "m_days", "出勤天数", CARD, (), ("工号", "日期"),
            tuple(item.id for item in employee_dimensions),
            ("b_emp_id", "b_emp_name", "b_emp_department"),
            Operation.COUNT_DISTINCT, MetricSemantic.ENTITY_COUNT, ("日期",),
            Additivity.NON_ADDITIVE, Composition.DISTINCT_SET, ("E_CARD",),
        )
        employee_sheet = sheet("s_employee", employee_dimensions, (days,))

        dept = dimension("department", binding("b_dept", EMPLOYEE, "部门", "E_EMP"))
        hours = metric(
            "m_hours", "加班小时", OVERTIME, ("加班小时",), ("工号",),
            ("department",), ("b_dept",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        people = metric(
            "m_people", "加班人数", OVERTIME, (), ("工号",),
            ("department",), ("b_dept",), Operation.COUNT_DISTINCT,
            MetricSemantic.ENTITY_COUNT, ("工号",), Additivity.NON_ADDITIVE,
            Composition.DISTINCT_SET, ("E_OT",),
        )
        department_sheet = sheet("s_department", (dept,), (hours, people))

        work_date = dimension("overtime_date", binding("b_overtime_date", OVERTIME, "日期", "E_OT"))
        daily = metric(
            "m_daily", "每日加班", OVERTIME, ("加班小时",), ("日期",),
            ("overtime_date",), ("b_overtime_date",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        time_sheet = sheet("s_time", (work_date,), (daily,))
        result = self.compile(workbook(
            employee_sheet,
            department_sheet,
            time_sheet,
            joins=(self.card_employee, self.overtime_employee),
        ))
        self.assertEqual(CompilationStatus.READY, result.status)
        self.assertEqual({"s_employee", "s_department", "s_time"}, set(result.plan.sheet_outputs))

    def test_missing_enrichment_path_is_rejected(self) -> None:
        dept = dimension("department", binding("b_emp_dept", EMPLOYEE, "部门", "E_EMP"))
        hours = metric(
            "m_hours", "部门加班", OVERTIME, ("加班小时",), ("工号",),
            ("department",), ("b_emp_dept",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        result = self.compile(workbook(sheet("s", (dept,), (hours,))))
        self.assertEqual(CompilationStatus.INVALID, result.status)
        self.assertIn("MISSING_JOIN_PATH", {item.code for item in result.diagnostics})

    def test_model_json_round_trip_and_schema_boundary(self) -> None:
        month = dimension(
            "month",
            binding(
                "b_month", SALES, "日期", "E_SALES",
                kind=TransformKind.DATE_BUCKET, parameters={"grain": "month"},
            ),
        )
        sales = metric(
            "m_sales", "销售额", SALES, ("销售额",), ("订单号", "日期"),
            ("month",), ("b_month",), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        ranked = replace(
            sheet("s_month", (month,), (sales,)),
            order_by=(SortIntent("m_sales", SortDirection.DESC),),
            limit=10,
            minimum_group_size=3,
            group_count_metric_id="m_sales",
        )
        intent = workbook(ranked)
        payload = workbook_intent_to_dict(intent)
        self.assertEqual(intent, parse_workbook_intent(payload))
        schema = workbook_intent_json_schema()
        self.assertEqual("WorkbookIntentIR", schema["title"])
        self.assertIn("dimension_binding_ids", schema["properties"]["sheets"]["items"]["properties"]["metrics"]["items"]["required"])
        self.assertIn("order_by", schema["properties"]["sheets"]["items"]["required"])
        self.assertIn("limit", schema["properties"]["sheets"]["items"]["required"])
        self.assertIn("minimum_group_size", schema["properties"]["sheets"]["items"]["required"])
        self.assertIn("group_count_metric_id", schema["properties"]["sheets"]["items"]["required"])
        payload["unexpected"] = True
        with self.assertRaises(IRParseError):
            parse_workbook_intent(payload)

    def test_orchestrator_repairs_structural_errors_and_reviews_conditionally(self) -> None:
        employee = dimension("employee_id", binding("b_ot_emp", OVERTIME, "工号", "E_OT"))
        bad = metric(
            "m_people", "员工数", OVERTIME, (), ("工号",),
            ("employee_id",), ("b_ot_emp",), Operation.COUNT_ROWS,
            MetricSemantic.ENTITY_COUNT, ("工号",), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        good = replace(
            bad,
            operation=Operation.COUNT_DISTINCT,
            additivity=Additivity.NON_ADDITIVE,
            composition=Composition.DISTINCT_SET,
        )
        bad_ir = workbook(sheet("s", (employee,), (bad,)))
        good_ir = workbook(sheet("s", (employee,), (good,)))

        class Model:
            def propose(self, _context):
                return bad_ir

        class Repair:
            calls = 0

            def repair(self, _intent, diagnostics, _context):
                self.calls += 1
                self.codes = {item.code for item in diagnostics}
                return good_ir

        class Reviewer:
            calls = 0

            def review(self, _intent, _context):
                self.calls += 1
                return ValueReview(True, ())

        repair = Repair()
        reviewer = Reviewer()
        orchestrator = MultiAgentOrchestrator(
            Model(),
            PlanCompiler(self.evidence),
            repair_agent=repair,
            value_review_agent=reviewer,
            should_review_value=lambda _intent, context: context.user_goal == "目标模糊",
        )
        result = orchestrator.run(ModelingContext("目标模糊", {}, ()))
        self.assertEqual(CompilationStatus.READY, result.compilation.status)
        self.assertEqual(1, repair.calls)
        self.assertEqual(1, reviewer.calls)
        self.assertIn("conditional_value_review", result.stages)


if __name__ == "__main__":
    unittest.main()
