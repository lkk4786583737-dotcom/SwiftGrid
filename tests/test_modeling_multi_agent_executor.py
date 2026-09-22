from __future__ import annotations

import unittest

import pandas as pd
from pandas.testing import assert_frame_equal

from scripts.modeling_multi_agent.compiler import (
    CompilationStatus,
    ExecutionPlan,
    PlanCompiler,
    PlanNode,
)
from scripts.modeling_multi_agent.evidence import (
    EvidenceKind,
    EvidenceRecord,
    EvidenceStore,
    join_evidence,
    unique_key_evidence,
)
from scripts.modeling_multi_agent.executor import ExecutionError, PandasPlanExecutor
from scripts.modeling_multi_agent.ir import (
    Additivity,
    ClarificationQuestion,
    Composition,
    DecisionLedger,
    DerivedExpression,
    DimensionBinding,
    DimensionIntent,
    DimensionTransform,
    JoinCardinality,
    JoinIntent,
    MetricIntent,
    MetricSemantic,
    Operation,
    SheetIntent,
    SortDirection,
    SortIntent,
    TransformKind,
    WorkbookIntentIR,
)


CARD = "打卡记录"
OVERTIME = "加班申请"
EMPLOYEE = "员工信息"
SALES = "销售明细"
TARGET = "销售目标"


def binding(
    binding_id: str,
    source_sheet: str,
    source_field: str,
    evidence_ref: str,
    *,
    kind: TransformKind = TransformKind.IDENTITY,
    parameters: dict | None = None,
    decisions: tuple[str, ...] = (),
) -> DimensionBinding:
    return DimensionBinding(
        binding_id,
        source_sheet,
        (source_field,),
        DimensionTransform(kind, parameters or {}),
        (evidence_ref,),
        decisions,
    )


def dimension(dimension_id: str, *bindings: DimensionBinding) -> DimensionIntent:
    return DimensionIntent(dimension_id, dimension_id, bindings)


def metric(
    metric_id: str,
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
        metric_id,
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
    return WorkbookIntentIR("wb", "执行测试", "全量确定性执行", sheets, joins, clarifications)


class ModelingMultiAgentExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = EvidenceStore(schemas={
            CARD: frozenset({"工号", "日期", "状态"}),
            OVERTIME: frozenset({"工号", "日期", "加班小时"}),
            EMPLOYEE: frozenset({"工号", "姓名", "部门", "基本工资"}),
            SALES: frozenset({"订单号", "日期", "区域", "销售额", "渠道"}),
            TARGET: frozenset({"月份", "区域", "目标额"}),
        })
        for record in (
            EvidenceRecord("E_CARD", EvidenceKind.FIELD_PROFILE, {"source_sheet": CARD}),
            EvidenceRecord("E_OT", EvidenceKind.FIELD_PROFILE, {"source_sheet": OVERTIME}),
            EvidenceRecord("E_EMP", EvidenceKind.FIELD_PROFILE, {"source_sheet": EMPLOYEE}),
            EvidenceRecord("E_SALES", EvidenceKind.FIELD_PROFILE, {"source_sheet": SALES}),
            EvidenceRecord("E_TARGET", EvidenceKind.FIELD_PROFILE, {"source_sheet": TARGET}),
            unique_key_evidence("E_EMP_UNIQUE", EMPLOYEE, ("工号",), is_unique=True),
            join_evidence(
                "E_OT_EMP",
                OVERTIME,
                EMPLOYEE,
                ("工号",),
                ("工号",),
                JoinCardinality.MANY_TO_ONE,
                right_unique=True,
            ),
        ):
            self.evidence.add(record)
        self.ot_employee = JoinIntent(
            "j_ot_employee",
            OVERTIME,
            EMPLOYEE,
            ("工号",),
            ("工号",),
            JoinCardinality.MANY_TO_ONE,
            "E_OT_EMP",
        )
        self.executor = PandasPlanExecutor()

    def test_project_sheet_applies_deterministic_top_n(self) -> None:
        scan = PlanNode(
            "scan", "scan_source", (), ("region",),
            {
                "source_sheet": "ranking",
                "fields": ("region", "sales"),
                "field_provenance": {"region": "ranking.region", "sales": "ranking.sales"},
            },
        )
        project = PlanNode(
            "output", "project_sheet", ("scan",), ("region",),
            {
                "title": "Top 2",
                "metric_ids": ("sales",),
                "order_by": ({"column_id": "sales", "direction": "desc"},),
                "limit": 2,
            },
        )
        sources = {
            "ranking": pd.DataFrame(
                {"region": ["东", "西", "南"], "sales": [10, 30, 20]}
            )
        }
        result = self.executor.execute(
            ExecutionPlan("wb", (scan, project), {"ranking": "output"}), sources
        )
        self.assertEqual(["西", "南"], result.sheets["ranking"]["region"].tolist())
        self.assertEqual([30, 20], result.sheets["ranking"]["sales"].tolist())

    def test_project_sheet_applies_minimum_group_suppression_before_sort(self) -> None:
        scan = PlanNode(
            "scan", "scan_source", (), ("city",),
            {
                "source_sheet": "groups",
                "fields": ("city", "customers"),
                "field_provenance": {
                    "city": "groups.city", "customers": "groups.customers",
                },
            },
        )
        project = PlanNode(
            "output", "project_sheet", ("scan",), ("city",),
            {
                "title": "Privacy safe groups",
                "metric_ids": ("customers",),
                "order_by": ({"column_id": "customers", "direction": "desc"},),
                "limit": 10,
                "minimum_group_size": 3,
                "group_count_metric_id": "customers",
            },
        )
        sources = {
            "groups": pd.DataFrame(
                {"city": ["A", "B", "C"], "customers": [5, 1, 3]}
            )
        }
        result = self.executor.execute(
            ExecutionPlan("wb", (scan, project), {"groups": "output"}), sources
        )
        self.assertEqual(["A", "C"], result.sheets["groups"]["city"].tolist())
        self.assertEqual(1, result.audit[-1].details["suppressed_rows"])

    def compile(
        self,
        intent: WorkbookIntentIR,
        decisions: DecisionLedger | None = None,
    ) -> ExecutionPlan:
        result = PlanCompiler(self.evidence, decisions).compile(intent)
        self.assertEqual(CompilationStatus.READY, result.status, result.diagnostics)
        self.assertIsNotNone(result.plan)
        return result.plan

    def test_attendance_employee_count_is_distinct_people_not_rows(self) -> None:
        department = dimension("department", binding("b_department", EMPLOYEE, "部门", "E_EMP"))
        people = metric(
            "m_people",
            OVERTIME,
            (),
            ("工号",),
            ("department",),
            ("b_department",),
            Operation.COUNT_DISTINCT,
            MetricSemantic.ENTITY_COUNT,
            ("工号",),
            Additivity.NON_ADDITIVE,
            Composition.DISTINCT_SET,
            ("E_OT",),
        )
        plan = self.compile(workbook(sheet("s_department", (department,), (people,)), joins=(self.ot_employee,)))
        sources = {
            OVERTIME: pd.DataFrame({
                "工号": ["E1", "E1", "E2", "E3"],
                "日期": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-01", "2025-01-01"]),
                "加班小时": [2, 3, 1, 4],
            }),
            EMPLOYEE: pd.DataFrame({
                "工号": ["E1", "E2", "E3"],
                "姓名": ["甲", "乙", "丙"],
                "部门": ["A", "A", "B"],
                "基本工资": [100, 120, 110],
            }),
        }

        result = self.executor.execute(plan, sources)
        actual = result.sheets["s_department"].sort_values("department").reset_index(drop=True)
        expected = pd.DataFrame({"department": ["A", "B"], "m_people": [2, 1]})
        assert_frame_equal(expected, actual, check_dtype=False)
        self.assertTrue(result.audit)
        self.assertIn("m_people", result.provenance["s_department"])

    def test_same_source_department_rollup_retains_target_dimension(self) -> None:
        department = dimension("department", binding("b_department", EMPLOYEE, "部门", "E_EMP"))
        salary = metric(
            "m_salary",
            EMPLOYEE,
            ("基本工资",),
            ("工号",),
            ("department",),
            ("b_department",),
            Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE,
            (),
            Additivity.ADDITIVE,
            Composition.SUM_PARTIALS,
            ("E_EMP",),
        )
        plan = self.compile(workbook(sheet("salary", (department,), (salary,))))
        sources = {EMPLOYEE: pd.DataFrame({
            "工号": ["E1", "E2", "E3"],
            "姓名": ["甲", "乙", "丙"],
            "部门": ["A", "A", "B"],
            "基本工资": [100.0, 120.0, 110.0],
        })}

        output = self.executor.execute(plan, sources).sheets["salary"].sort_values("department").reset_index(drop=True)
        expected = pd.DataFrame({"department": ["A", "B"], "m_salary": [220.0, 110.0]})
        assert_frame_equal(expected, output)

    def test_actual_target_are_aggregated_before_achievement_ratio(self) -> None:
        dimensions = (
            dimension(
                "month",
                binding(
                    "b_sales_month",
                    SALES,
                    "日期",
                    "E_SALES",
                    kind=TransformKind.DATE_BUCKET,
                    parameters={"grain": "month"},
                ),
                binding("b_target_month", TARGET, "月份", "E_TARGET"),
            ),
            dimension(
                "region",
                binding("b_sales_region", SALES, "区域", "E_SALES"),
                binding("b_target_region", TARGET, "区域", "E_TARGET"),
            ),
        )
        canonical = ("month", "region")
        actual = metric(
            "m_actual", SALES, ("销售额",), ("订单号", "日期", "区域"), canonical,
            ("b_sales_month", "b_sales_region"), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        target = metric(
            "m_target", TARGET, ("目标额",), ("月份", "区域"), canonical,
            ("b_target_month", "b_target_region"), Operation.SUM,
            MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_TARGET",),
        )
        achievement = metric(
            "m_achievement", None, (), (), canonical, (), Operation.DERIVED,
            MetricSemantic.DERIVED_MEASURE, (), Additivity.NON_ADDITIVE,
            Composition.RATIO_OF_SUMS, ("E_SALES", "E_TARGET"),
            dependencies=("m_actual", "m_target"),
            expression=DerivedExpression("divide", ("m_actual", "m_target"), "error"),
        )
        plan = self.compile(workbook(sheet("achievement", dimensions, (actual, target, achievement))))
        sources = {
            SALES: pd.DataFrame({
                "订单号": ["O1", "O2", "O3"],
                "日期": pd.to_datetime(["2025-01-03", "2025-01-15", "2025-01-18"]),
                "区域": ["东", "东", "西"],
                "销售额": [40.0, 60.0, 50.0],
                "渠道": ["门店", "电商", "门店"],
            }),
            TARGET: pd.DataFrame({
                "月份": pd.to_datetime(["2025-01-01", "2025-01-01"]),
                "区域": ["东", "西"],
                "目标额": [80.0, 100.0],
            }),
        }

        output = self.executor.execute(plan, sources).sheets["achievement"].sort_values("region").reset_index(drop=True)
        expected = pd.DataFrame({
            "month": pd.to_datetime(["2025-01-01", "2025-01-01"]),
            "region": ["东", "西"],
            "m_actual": [100.0, 50.0],
            "m_target": [80.0, 100.0],
            "m_achievement": [1.25, 0.5],
        })
        assert_frame_equal(expected, output)

    def test_date_numeric_and_categorical_buckets_are_deterministic(self) -> None:
        dimensions = (
            dimension(
                "month",
                binding(
                    "b_month", SALES, "日期", "E_SALES",
                    kind=TransformKind.DATE_BUCKET,
                    parameters={"grain": "month"},
                ),
            ),
            dimension(
                "amount_band",
                binding(
                    "b_amount", SALES, "销售额", "E_SALES",
                    kind=TransformKind.NUMERIC_BUCKET,
                    parameters={"boundaries": [100, 500], "labels": ["低", "中", "高"], "closed": "left"},
                    decisions=("D_AMOUNT_BUCKET",),
                ),
            ),
            dimension(
                "channel_class",
                binding(
                    "b_channel", SALES, "渠道", "E_SALES",
                    kind=TransformKind.CATEGORICAL_RULE,
                    parameters={
                        "rules": [
                            {"label": "线上", "operator": "in", "values": ["电商", "小程序"]},
                            {"label": "缺失", "operator": "is_empty"},
                        ],
                        "default_label": "线下",
                    },
                    decisions=("D_CHANNEL_BUCKET",),
                ),
            ),
        )
        grain = tuple(item.id for item in dimensions)
        rows = metric(
            "m_rows", SALES, (), ("订单号", "日期", "销售额", "渠道"), grain,
            tuple(item.bindings[0].id for item in dimensions), Operation.COUNT_ROWS,
            MetricSemantic.RECORD_COUNT, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        questions = (
            ClarificationQuestion(
                "D_AMOUNT_BUCKET", "确认金额分桶", "决定金额区间归属", ("b_amount",), True,
            ),
            ClarificationQuestion(
                "D_CHANNEL_BUCKET", "确认渠道分组", "决定渠道类别归属", ("b_channel",), True,
            ),
        )
        decisions = DecisionLedger()
        decisions.confirm("D_AMOUNT_BUCKET", [100, 500])
        decisions.confirm("D_CHANNEL_BUCKET", {"线上": ["电商", "小程序"]})
        plan = self.compile(
            workbook(sheet("buckets", dimensions, (rows,)), clarifications=questions),
            decisions,
        )
        sources = {SALES: pd.DataFrame({
            "订单号": ["O1", "O2", "O3", "O4"],
            "日期": ["2025-01-01", "2025-01-20", "2025-02-01", "2025-02-03"],
            "区域": ["东", "东", "西", "西"],
            "销售额": [99, 100, 500, 700],
            "渠道": ["电商", "门店", None, "经销"],
        })}

        output = self.executor.execute(plan, sources).sheets["buckets"]
        self.assertEqual(["低", "中", "高", "高"], output["amount_band"].tolist())
        self.assertEqual(["线上", "线下", "缺失", "线下"], output["channel_class"].tolist())
        self.assertEqual(
            pd.to_datetime(["2025-01-01", "2025-01-01", "2025-02-01", "2025-02-01"]).tolist(),
            output["month"].tolist(),
        )
        self.assertEqual([1, 1, 1, 1], output["m_rows"].tolist())

    def test_remaining_rollups_average_min_max_nonempty_and_value(self) -> None:
        department = dimension("department", binding("b_department", EMPLOYEE, "部门", "E_EMP"))
        employee_id = dimension("employee_id", binding("b_employee", EMPLOYEE, "工号", "E_EMP"))
        grain = ("department",)
        average = metric(
            "m_average", EMPLOYEE, ("基本工资",), ("工号",), grain, ("b_department",),
            Operation.AVERAGE, MetricSemantic.NON_ADDITIVE_MEASURE, (), Additivity.NON_ADDITIVE,
            Composition.WEIGHTED_AVERAGE, ("E_EMP",),
        )
        minimum = metric(
            "m_min", EMPLOYEE, ("基本工资",), ("工号",), grain, ("b_department",),
            Operation.MIN, MetricSemantic.NON_ADDITIVE_MEASURE, (), Additivity.SEMI_ADDITIVE,
            Composition.MINMAX, ("E_EMP",),
        )
        maximum = metric(
            "m_max", EMPLOYEE, ("基本工资",), ("工号",), grain, ("b_department",),
            Operation.MAX, MetricSemantic.NON_ADDITIVE_MEASURE, (), Additivity.SEMI_ADDITIVE,
            Composition.MINMAX, ("E_EMP",),
        )
        named = metric(
            "m_named", EMPLOYEE, ("姓名",), ("工号",), grain, ("b_department",),
            Operation.COUNT_NONEMPTY, MetricSemantic.RECORD_COUNT, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_EMP",),
        )
        rows = metric(
            "m_rows", EMPLOYEE, (), ("工号",), grain, ("b_department",),
            Operation.COUNT_ROWS, MetricSemantic.RECORD_COUNT, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_EMP",),
        )
        name_value = metric(
            "m_name", EMPLOYEE, ("姓名",), ("工号",), ("employee_id",), ("b_employee",),
            Operation.VALUE, MetricSemantic.ATTRIBUTE_VALUE, (), Additivity.NON_ADDITIVE,
            Composition.RECOMPUTE, ("E_EMP_UNIQUE",), uniqueness_required=True,
        )
        intent = workbook(
            sheet("statistics", (department,), (average, minimum, maximum, named, rows)),
            sheet("employee", (employee_id,), (name_value,)),
        )
        plan = self.compile(intent)
        sources = {EMPLOYEE: pd.DataFrame({
            "工号": ["E1", "E2", "E3"],
            "姓名": ["甲", "", "丙"],
            "部门": ["A", "A", "B"],
            "基本工资": [100.0, 120.0, 90.0],
        })}

        result = self.executor.execute(plan, sources)
        statistics = result.sheets["statistics"].sort_values("department").reset_index(drop=True)
        expected = pd.DataFrame({
            "department": ["A", "B"],
            "m_average": [110.0, 90.0],
            "m_min": [100.0, 90.0],
            "m_max": [120.0, 90.0],
            "m_named": [1, 1],
            "m_rows": [2, 1],
        })
        assert_frame_equal(expected, statistics, check_dtype=False)
        self.assertEqual(["甲", "", "丙"], result.sheets["employee"]["m_name"].tolist())

    def test_non_unique_lookup_is_rejected_before_row_multiplication(self) -> None:
        department = dimension("department", binding("b_department", EMPLOYEE, "部门", "E_EMP"))
        hours = metric(
            "m_hours", OVERTIME, ("加班小时",), ("工号",), ("department",), ("b_department",),
            Operation.SUM, MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_OT",),
        )
        plan = self.compile(workbook(sheet("hours", (department,), (hours,)), joins=(self.ot_employee,)))
        sources = {
            OVERTIME: pd.DataFrame({"工号": ["E1", "E1"], "日期": ["2025-01-01", "2025-01-02"], "加班小时": [2, 3]}),
            EMPLOYEE: pd.DataFrame({
                "工号": ["E1", "E1"],
                "姓名": ["甲", "甲重"],
                "部门": ["A", "B"],
                "基本工资": [100, 100],
            }),
        }
        with self.assertRaisesRegex(ExecutionError, "NON_UNIQUE_LOOKUP"):
            self.executor.execute(plan, sources)

    def test_raw_many_to_many_is_rejected_at_validation_boundary(self) -> None:
        evidence = join_evidence(
            "E_RAW_M2M", SALES, TARGET, ("区域",), ("区域",),
            JoinCardinality.MANY_TO_MANY, safe_raw_join=False,
        )
        self.evidence.add(evidence)
        unsafe = JoinIntent(
            "j_raw_m2m", SALES, TARGET, ("区域",), ("区域",),
            JoinCardinality.MANY_TO_MANY, "E_RAW_M2M",
        )
        region = dimension("region", binding("b_region", SALES, "区域", "E_SALES"))
        sales = metric(
            "m_sales", SALES, ("销售额",), ("订单号", "区域"), ("region",), ("b_region",),
            Operation.SUM, MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        result = PlanCompiler(self.evidence).compile(workbook(sheet("sales", (region,), (sales,)), joins=(unsafe,)))
        self.assertEqual(CompilationStatus.INVALID, result.status)
        self.assertIsNone(result.plan)
        self.assertIn("UNSAFE_MANY_TO_MANY", {item.code for item in result.diagnostics})

    def test_duplicate_canonical_grain_is_rejected(self) -> None:
        scan = PlanNode(
            "scan",
            "scan_source",
            (),
            ("region",),
            {
                "source_sheet": "branch",
                "fields": ("region", "m_value"),
                "field_provenance": {"region": "branch.region", "m_value": "branch.m_value"},
            },
        )
        merge = PlanNode(
            "merge",
            "merge_metric_branches",
            ("scan",),
            ("region",),
            {
                "join_type": "full",
                "canonical_keys": ("region",),
                "inputs_already_unique_on_canonical_grain": True,
            },
        )
        plan = ExecutionPlan("wb", (scan, merge), {"sheet": "merge"})
        with self.assertRaisesRegex(ExecutionError, "DUPLICATE_CANONICAL_GRAIN"):
            self.executor.execute(plan, {"branch": pd.DataFrame({"region": ["东", "东"], "m_value": [1, 2]})})

    def test_missing_field_and_unknown_operation_fail_closed(self) -> None:
        scan = PlanNode(
            "scan",
            "scan_source",
            (),
            (),
            {
                "source_sheet": "source",
                "fields": ("missing",),
                "field_provenance": {"missing": "source.missing"},
            },
        )
        with self.assertRaisesRegex(ExecutionError, "MISSING_COLUMN"):
            self.executor.execute(ExecutionPlan("wb", (scan,), {"sheet": "scan"}), {"source": pd.DataFrame({"x": [1]})})

        unsupported = PlanNode("raw", "raw_many_to_many_join", (), (), {})
        with self.assertRaisesRegex(ExecutionError, "UNKNOWN_NODE_KIND"):
            self.executor.execute(ExecutionPlan("wb", (unsupported,), {}), {})

    def test_division_by_zero_fails_closed_even_with_null_policy(self) -> None:
        dimensions = (dimension("region", binding("b_sales_region", SALES, "区域", "E_SALES")),)
        actual = metric(
            "m_actual", SALES, ("销售额",), ("订单号", "区域"), ("region",), ("b_sales_region",),
            Operation.SUM, MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_SALES",),
        )
        target = metric(
            "m_target", TARGET, ("目标额",), ("月份", "区域"), ("region",), ("b_target_region",),
            Operation.SUM, MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE,
            Composition.SUM_PARTIALS, ("E_TARGET",),
        )
        region_with_target = dimension(
            "region",
            binding("b_sales_region", SALES, "区域", "E_SALES"),
            binding("b_target_region", TARGET, "区域", "E_TARGET"),
        )
        ratio = metric(
            "m_ratio", None, (), (), ("region",), (), Operation.DERIVED,
            MetricSemantic.DERIVED_MEASURE, (), Additivity.NON_ADDITIVE,
            Composition.RATIO_OF_SUMS, ("E_SALES", "E_TARGET"),
            dependencies=("m_actual", "m_target"),
            expression=DerivedExpression("divide", ("m_actual", "m_target"), "null"),
        )
        plan = self.compile(workbook(sheet("ratio", (region_with_target,), (actual, target, ratio))))
        sources = {
            SALES: pd.DataFrame({"订单号": ["O1"], "日期": ["2025-01-01"], "区域": ["东"], "销售额": [10], "渠道": ["门店"]}),
            TARGET: pd.DataFrame({"月份": ["2025-01"], "区域": ["东"], "目标额": [0]}),
        }
        with self.assertRaisesRegex(ExecutionError, "DIVISION_BY_ZERO"):
            self.executor.execute(plan, sources)

    def test_supported_derived_operators_and_parameter_boundaries(self) -> None:
        scan = PlanNode(
            "scan",
            "scan_source",
            (),
            ("region",),
            {
                "source_sheet": "metrics",
                "fields": ("region", "m_a", "m_b"),
                "field_provenance": {
                    "region": "metrics.region",
                    "m_a": "metrics.m_a",
                    "m_b": "metrics.m_b",
                },
            },
        )
        merge = PlanNode(
            "merge",
            "merge_metric_branches",
            ("scan",),
            ("region",),
            {
                "join_type": "full",
                "canonical_keys": ("region",),
                "inputs_already_unique_on_canonical_grain": True,
            },
        )
        specifications = (
            ("d_ratio", "ratio", ("m_a", "m_b"), {}),
            ("d_divide", "divide", ("m_a", "m_b"), {}),
            ("d_subtract", "subtract", ("m_a", "m_b"), {}),
            ("d_add", "add", ("m_a", "m_b"), {}),
            ("d_multiply", "multiply", ("m_a", "m_b"), {}),
            ("d_add_constant", "add_constant", ("m_a",), {"value": 5}),
            ("d_subtract_constant", "subtract_constant", ("m_a",), {"value": 1}),
            ("d_multiply_constant", "multiply_constant", ("m_a",), {"factor": 3}),
            ("d_divide_constant", "divide_constant", ("m_a",), {"divisor": 2}),
        )
        nodes = [scan, merge]
        current = "merge"
        for metric_id, operator, operands, parameters in specifications:
            node_id = f"{metric_id}__derive"
            nodes.append(PlanNode(
                node_id,
                "derive_metric",
                (current, *("scan" for _ in operands)),
                ("region",),
                {
                    "metric_id": metric_id,
                    "operator": operator,
                    "operands": operands,
                    "composition": "recompute",
                    "zero_division": "error",
                    "parameters": parameters,
                },
            ))
            current = node_id
        project = PlanNode(
            "output",
            "project_sheet",
            (current,),
            ("region",),
            {"title": "派生指标", "metric_ids": tuple(item[0] for item in specifications)},
        )
        nodes.append(project)
        sources = {"metrics": pd.DataFrame({"region": ["东"], "m_a": [10], "m_b": [2]})}
        result = self.executor.execute(ExecutionPlan("wb", tuple(nodes), {"derived": "output"}), sources)
        row = result.sheets["derived"].iloc[0]
        self.assertEqual(
            [5, 5, 8, 12, 20, 15, 9, 30, 5],
            [row[item[0]] for item in specifications],
        )

        invalid = PlanNode(
            "invalid",
            "derive_metric",
            ("merge", "scan", "scan"),
            ("region",),
            {
                "metric_id": "d_invalid",
                "operator": "add",
                "operands": ("m_a", "m_b"),
                "composition": "recompute",
                "zero_division": "error",
                "parameters": {"invented": 1},
            },
        )
        invalid_plan = ExecutionPlan("wb", (scan, merge, invalid), {"derived": "invalid"})
        with self.assertRaisesRegex(ExecutionError, "INVALID_DERIVED_PARAMETERS"):
            self.executor.execute(invalid_plan, sources)


if __name__ == "__main__":
    unittest.main()
