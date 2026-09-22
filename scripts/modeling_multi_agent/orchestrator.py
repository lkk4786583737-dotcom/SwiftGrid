from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .canonicalize import canonicalize_workbook_intent
from .compiler import CompilationResult, CompilationStatus, PlanCompiler
from .ir import WorkbookIntentIR
from .validator import Diagnostic, Severity


@dataclass(frozen=True)
class ModelingContext:
    user_goal: str
    dataset_card: dict[str, Any]
    evidence_catalog: tuple[dict[str, Any], ...]
    confirmed_decisions: dict[str, Any] = field(default_factory=dict)
    short_term_summary: dict[str, Any] = field(default_factory=dict)
    recalled_memory: tuple[dict[str, Any], ...] = ()


class ModelingAgent(Protocol):
    def propose(self, context: ModelingContext) -> WorkbookIntentIR:
        ...


class RepairAgent(Protocol):
    def repair(
        self,
        intent: WorkbookIntentIR,
        diagnostics: tuple[Diagnostic, ...],
        context: ModelingContext,
    ) -> WorkbookIntentIR:
        ...


@dataclass(frozen=True)
class ValueReview:
    accepted: bool
    findings: tuple[str, ...] = ()
    scores: dict[str, int] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)


class ValueReviewAgent(Protocol):
    def review(self, intent: WorkbookIntentIR, context: ModelingContext) -> ValueReview:
        ...


@dataclass(frozen=True)
class OrchestrationResult:
    intent: WorkbookIntentIR
    compilation: CompilationResult
    stages: tuple[str, ...]
    repair_attempts: int
    value_review: ValueReview | None = None


class MultiAgentOrchestrator:
    def __init__(
        self,
        modeling_agent: ModelingAgent,
        compiler: PlanCompiler,
        *,
        repair_agent: RepairAgent | None = None,
        value_review_agent: ValueReviewAgent | None = None,
        should_review_value: Callable[[WorkbookIntentIR, ModelingContext], bool] | None = None,
        max_repairs: int = 1,
    ) -> None:
        self.modeling_agent = modeling_agent
        self.compiler = compiler
        self.repair_agent = repair_agent
        self.value_review_agent = value_review_agent
        self.should_review_value = should_review_value or (lambda _intent, _context: False)
        self.max_repairs = max(0, max_repairs)

    def run(self, context: ModelingContext) -> OrchestrationResult:
        stages = ["modeling"]
        intent = canonicalize_workbook_intent(self.modeling_agent.propose(context))
        compilation = self.compiler.compile(intent)
        stages.append("compiling")
        repairs = 0

        while self._can_repair(compilation, repairs):
            assert self.repair_agent is not None
            stages.append("repairing")
            intent = canonicalize_workbook_intent(
                self.repair_agent.repair(intent, compilation.diagnostics, context)
            )
            repairs += 1
            compilation = self.compiler.compile(intent)
            stages.append("recompiling")

        review = None
        if (
            compilation.status == CompilationStatus.READY
            and self.value_review_agent is not None
            and self.should_review_value(intent, context)
        ):
            stages.append("conditional_value_review")
            review = self.value_review_agent.review(intent, context)
        return OrchestrationResult(intent, compilation, tuple(stages), repairs, review)

    def _can_repair(self, compilation: CompilationResult, attempts: int) -> bool:
        if compilation.status != CompilationStatus.INVALID:
            return False
        if not self.repair_agent or attempts >= self.max_repairs:
            return False
        errors = [item for item in compilation.diagnostics if item.severity == Severity.ERROR]
        return bool(errors)
