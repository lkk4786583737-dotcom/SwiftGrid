from __future__ import annotations

import unittest

import pandas as pd

from scripts.modeling_multi_agent.compiler import ExecutionPlan, PlanNode
from scripts.modeling_multi_agent.executor import ExecutionResult
from scripts.modeling_multi_agent.ir import (
    Additivity, Composition, DerivedExpression, DimensionBinding, DimensionIntent,
    DimensionTransform, MetricIntent, MetricSemantic, MetricUnit, Operation,
    ClarificationQuestion, RiskLevel, SheetIntent, SortDirection, SortIntent,
    TransformKind, WorkbookIntentIR,
)
from scripts.modeling_multi_agent.result_audit import FullDataResultAuditor
from scripts.modeling_multi_agent.orchestrator import ModelingContext
from scripts.modeling_multi_agent_demo import DeepSeekDecisionComplianceAgent


class FullDataResultAuditorTests(unittest.TestCase):
    def fixture(self, wrong: bool = False, misallocated: bool = False):
        raw = pd.DataFrame({"region": ["东", "西", "南"], "sales_value": [100.0, 80.0, 60.0], "cost_value": [60.0, 50.0, 45.0]})
        binding = DimensionBinding("b", "source", ("region",), DimensionTransform(TransformKind.IDENTITY), ("e",))
        dimension = DimensionIntent("region", "区域", (binding,))
        def source_metric(metric_id, field):
            return MetricIntent(metric_id, metric_id, "source", (field,), ("region",), ("region",), ("b",), Operation.SUM, MetricSemantic.ADDITIVE_MEASURE, (), Additivity.ADDITIVE, Composition.SUM_PARTIALS, ("e",), business_definition=metric_id, unit=MetricUnit.CURRENCY, risk_level=RiskLevel.LOW)
        sales, cost = source_metric("sales", "sales_value"), source_metric("cost", "cost_value")
        profit = MetricIntent("profit", "毛利额", None, (), (), ("region",), (), Operation.DERIVED, MetricSemantic.DERIVED_MEASURE, (), Additivity.NON_ADDITIVE, Composition.RECOMPUTE, ("e",), dependencies=("sales", "cost"), expression=DerivedExpression("subtract", ("sales", "cost")), business_definition="销售额减成本", unit=MetricUnit.CURRENCY, risk_level=RiskLevel.HIGH)
        sheet = SheetIntent("summary", "汇总", "毛利前两名", (dimension,), ("region",), (sales, cost, profit), (SortIntent("profit", SortDirection.DESC),), 2)
        intent = WorkbookIntentIR("wb", "wb", "audit", (sheet,))
        nodes, frames = [], {}
        for metric, field in ((sales, "sales_value"), (cost, "cost_value")):
            partial = f"__{metric.id}_partial"
            input_id = f"{metric.id}__input"
            input_frame = raw.loc[:, ["region", field]].copy()
            pre = input_frame.rename(columns={field: partial})
            if misallocated:
                pre.loc[:, partial] = pre[partial].iloc[::-1].reset_index(drop=True)
            roll = pre.rename(columns={partial: metric.id})
            pre_id, roll_id = f"{metric.id}__preaggregate", f"{metric.id}__rollup"
            nodes.extend((
                PlanNode(pre_id, "pre_aggregate_to_source_grain", (input_id,), ("region",), {"partial_column": partial, "operation": "sum"}),
                PlanNode(roll_id, "aggregate_at_conformed_grain", (pre_id,), ("region",), {"operation": "sum_partials", "partial_column": partial, "partial_columns": ()}),
            ))
            frames[input_id], frames[pre_id], frames[roll_id] = input_frame, pre, roll
        internal = frames["sales__rollup"].merge(frames["cost__rollup"], on="region")
        internal["profit"] = internal.sales - internal.cost + (1 if wrong else 0)
        frames["derived"] = internal
        project = PlanNode("output", "project_sheet", ("derived",), ("region",), {"metric_ids": ("sales", "cost", "profit"), "dimension_names": {"region": "区域"}, "metric_names": {"sales": "sales", "cost": "cost", "profit": "profit"}, "order_by": ({"column_id": "profit", "direction": "desc"},), "limit": 2})
        nodes.append(project)
        output = internal.sort_values("profit", ascending=False).head(2).rename(columns={"region": "区域"})
        execution = ExecutionResult({"summary": output}, {}, (), frames)
        return ExecutionPlan("wb", tuple(nodes), {"summary": "output"}), intent, execution, {"source": raw}

    def test_verifies_full_source_formula_and_top_n(self):
        report = FullDataResultAuditor().audit(*self.fixture())
        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.verified, 8)

    def test_blocks_wrong_derived_result(self):
        report = FullDataResultAuditor().audit(*self.fixture(wrong=True))
        self.assertFalse(report.passed)
        self.assertGreater(report.failed, 0)

    def test_blocks_group_misallocation_even_when_totals_still_match(self):
        report = FullDataResultAuditor().audit(*self.fixture(misallocated=True))
        self.assertFalse(report.passed)
        failed_layers = {item.layer for item in report.checks if item.status.value == "failed"}
        self.assertIn("source_grain_recalculation", failed_layers)


class DecisionComplianceGateTests(unittest.TestCase):
    class Gateway:
        def __init__(self, response):
            self.response = response

        def call(self, *_args, **_kwargs):
            return self.response

    def review(self, response):
        intent = WorkbookIntentIR("wb", "wb", "test", ())
        context = ModelingContext(
            "goal", {}, (), confirmed_decisions={
                "Q1": {"selected_option": "remove metric"},
            },
        )
        return DeepSeekDecisionComplianceAgent(self.Gateway(response)).review(
            intent, intent, context
        )

    def test_missing_or_rejected_decision_check_fails_closed(self):
        self.assertFalse(self.review({"checks": []})["passed"])
        self.assertFalse(self.review({"checks": [{
            "decision_id": "Q1",
            "selected_option": "remove metric",
            "implementation": "metric still exists",
            "implemented": False,
        }]})["passed"])

    def test_complete_implemented_decision_check_passes(self):
        self.assertTrue(self.review({"checks": [{
            "decision_id": "Q1",
            "selected_option": "remove metric",
            "implementation": "metric removed",
            "implemented": True,
        }]})["passed"])

    def test_mismatched_echoed_option_fails_closed(self):
        self.assertFalse(self.review({"checks": [{
            "decision_id": "Q1",
            "selected_option": "keep metric",
            "implementation": "metric removed",
            "implemented": True,
        }]})["passed"])

    def test_weighted_choice_requires_first_class_executable_contract(self):
        metric = MetricIntent(
            "M1", "平均时薪", "员工", ("时薪",), ("工号",), ("部门",), (),
            Operation.AVERAGE, MetricSemantic.NON_ADDITIVE_MEASURE, (),
            Additivity.NON_ADDITIVE, Composition.WEIGHTED_AVERAGE, (),
            required_decision_ids=("Q_WEIGHT",), business_definition="普通平均时薪",
            unit=MetricUnit.CURRENCY, risk_level=RiskLevel.MEDIUM,
        )
        sheet = SheetIntent("S1", "部门汇总", "测试", (), ("部门",), (metric,))
        clarification = ClarificationQuestion(
            "Q_WEIGHT", "当前按简单平均，选择哪个口径？", "影响成本口径",
            ("M1",), True, ("简单平均", "按加班时长加权"),
        )
        intent = WorkbookIntentIR(
            "wb", "wb", "test", (sheet,), clarifications=(clarification,),
        )
        context = ModelingContext(
            "goal", {}, (), confirmed_decisions={
                "Q_WEIGHT": {"selected_option": "按加班时长加权"},
            },
        )
        response = {"checks": [{
            "decision_id": "Q_WEIGHT",
            "selected_option": "按加班时长加权",
            "implementation": "updated business definition",
            "implemented": True,
        }]}
        result = DeepSeekDecisionComplianceAgent(self.Gateway(response)).review(
            intent, intent, context
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["deterministic_findings"][0]["code"],
            "WEIGHT_CONTRACT_NOT_EXECUTABLE",
        )

    def test_hidden_group_choice_requires_executable_suppression(self):
        sheet = SheetIntent("S1", "城市汇总", "测试", (), (), ())
        clarification = ClarificationQuestion(
            "Q_HIDE", "小分组是否显示？", "隐私保护", ("S1",), True,
            ("显示杭州", "不显示杭州"),
        )
        intent = WorkbookIntentIR(
            "wb", "wb", "test", (sheet,), clarifications=(clarification,),
        )
        context = ModelingContext(
            "goal", {}, (), confirmed_decisions={
                "Q_HIDE": {"selected_option": "不显示杭州"},
            },
        )
        response = {"checks": [{
            "decision_id": "Q_HIDE",
            "selected_option": "不显示杭州",
            "implementation": "documented privacy suppression",
            "implemented": True,
        }]}
        result = DeepSeekDecisionComplianceAgent(self.Gateway(response)).review(
            intent, intent, context
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["deterministic_findings"][0]["code"],
            "ROW_SUPPRESSION_NOT_EXECUTABLE",
        )


if __name__ == "__main__":
    unittest.main()
