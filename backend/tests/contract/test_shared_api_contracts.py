"""Schemas de contrato (docs/contracts.md) em riftlab.shared.api: exemplos
validos e invalidos, limites de fronteira e invariantes entre campos —
CC-106. Nenhum destes schemas esta montado como rota real ainda."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from riftlab.shared.api.analyses import (
    AnalysisQuality,
    AnalysisResult,
    AnalysisSupport,
    AnalysisVersions,
    UserProvidedBudget,
)
from riftlab.shared.api.common import CursorPage, MatchId, PaginationParams
from riftlab.shared.api.enums import AnalysisEngine, AnalysisStatus, BudgetSource, ImportMode
from riftlab.shared.api.feedback import FeedbackRequest
from riftlab.shared.api.imports import ImportRequest
from riftlab.shared.api.learning import (
    LearningAttemptAnswerRequest,
    LearningAttemptHintRequest,
    LearningAttemptPublicState,
    LearningAttemptRevealRequest,
    LearningAttemptState,
)
from riftlab.shared.api.matches import (
    DecisionBudget,
    DecisionInventory,
    DecisionState,
    DecisionStateSummary,
    InventoryItem,
    MatchDetail,
    MatchParticipant,
    MatchPreparation,
    MatchSummary,
)
from riftlab.shared.api.objectives import ObjectiveForm, ObjectiveInterpretRequest

# ---------------------------------------------------------------------------
# matchId discriminado por mode (POST /imports)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("match_id", "mode"),
    [
        ("BR1_1234567890", "RIOT"),
        ("BR1_1", "RIOT"),
        ("DEMO-0001", "DEMO"),
        ("DEMO-0007", "DEMO"),
    ],
)
def test_import_request_accepts_matching_id_and_mode(match_id: str, mode: str) -> None:
    request = ImportRequest.model_validate({"matchId": match_id, "mode": mode})
    assert request.match_id == match_id
    assert request.mode == ImportMode(mode)


@pytest.mark.parametrize(
    ("match_id", "mode"),
    [
        ("DEMO-0001", "RIOT"),  # id demo com mode real
        ("BR1_123", "DEMO"),  # id real com mode demo
        ("BR1_abc", "RIOT"),  # caracteres nao numericos
        ("DEMO-1", "DEMO"),  # manifesto exige 4 digitos
        ("http://evil.example/BR1_1", "RIOT"),  # nunca aceitar URL/host
        ("../../etc/passwd", "DEMO"),  # nunca aceitar caminho local
        ("BR1_" + "1" * 21, "RIOT"),  # acima de 20 digitos
    ],
)
def test_import_request_rejects_mismatched_or_unsafe_match_id(match_id: str, mode: str) -> None:
    with pytest.raises(ValidationError):
        ImportRequest.model_validate({"matchId": match_id, "mode": mode})


def test_import_request_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        ImportRequest.model_validate({"matchId": "BR1_1", "mode": "LIVE"})


# ---------------------------------------------------------------------------
# MatchId geral (GET /matches/{id} e afins)
# ---------------------------------------------------------------------------


def test_match_id_accepts_real_and_demo_formats() -> None:
    from pydantic import BaseModel

    class Wrapper(BaseModel):
        match_id: MatchId

    assert Wrapper(match_id="BR1_42").match_id == "BR1_42"
    assert Wrapper(match_id="DEMO-0003").match_id == "DEMO-0003"


def test_match_id_rejects_host_or_path() -> None:
    from pydantic import BaseModel

    class Wrapper(BaseModel):
        match_id: MatchId

    for offending in ("https://riotgames.com/BR1_1", "/etc/passwd", "BR1_1;DROP TABLE"):
        with pytest.raises(ValidationError):
            Wrapper(match_id=offending)


# ---------------------------------------------------------------------------
# Paginacao (limit 20/100)
# ---------------------------------------------------------------------------


def test_pagination_default_limit_is_20() -> None:
    assert PaginationParams().limit == 20


@pytest.mark.parametrize("limit", [0, 101, -1])
def test_pagination_rejects_limit_outside_1_100(limit: int) -> None:
    with pytest.raises(ValidationError):
        PaginationParams.model_validate({"limit": limit})


def test_cursor_page_next_cursor_defaults_to_none_not_empty_string() -> None:
    page = CursorPage[int](items=[1, 2, 3])
    assert page.next_cursor is None
    assert page.model_dump(by_alias=True)["nextCursor"] is None


# ---------------------------------------------------------------------------
# Orcamento (0..100000, source fixo)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gold", [0, 100_000])
def test_user_provided_budget_accepts_boundary_values(gold: int) -> None:
    budget = UserProvidedBudget.model_validate({"gold": gold, "source": "USER_PROVIDED"})
    assert budget.gold == gold


@pytest.mark.parametrize("gold", [-1, 100_001])
def test_user_provided_budget_rejects_out_of_range(gold: int) -> None:
    with pytest.raises(ValidationError):
        UserProvidedBudget.model_validate({"gold": gold, "source": "USER_PROVIDED"})


def test_user_provided_budget_rejects_server_only_sources() -> None:
    """Cliente nunca pode declarar FRAME_ESTIMATE/UNKNOWN — so o servidor
    atribui essas fontes (docs/contracts.md)."""
    for source in ("FRAME_ESTIMATE", "UNKNOWN"):
        with pytest.raises(ValidationError):
            UserProvidedBudget.model_validate({"gold": 100, "source": source})


# ---------------------------------------------------------------------------
# Objetivo em texto livre (ate 500 caracteres)
# ---------------------------------------------------------------------------


def test_objective_interpret_request_accepts_up_to_500_chars() -> None:
    text = "x" * 500
    assert ObjectiveInterpretRequest(text=text).text == text


def test_objective_interpret_request_rejects_over_500_chars() -> None:
    with pytest.raises(ValidationError):
        ObjectiveInterpretRequest(text="x" * 501)


def test_objective_interpret_request_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        ObjectiveInterpretRequest(text="")


def test_objective_form_rejects_unknown_priority() -> None:
    with pytest.raises(ValidationError):
        ObjectiveForm.model_validate({"priority": "AGGRESSIVE"})


# ---------------------------------------------------------------------------
# Resultado de analise: "sem evidencia: candidates=[] e reasonCodes obrigatorio"
# ---------------------------------------------------------------------------


def _base_analysis_kwargs() -> dict:
    return dict(
        analysis_id=uuid4(),
        decision_id=uuid4(),
        engine=AnalysisEngine.CONTEXTUAL_RANKER,
        objective=ObjectiveForm(priority="BALANCED"),
        versions=AnalysisVersions(catalog="16.17.1", features="v1", candidates="v1"),
        quality=AnalysisQuality(budget_source=BudgetSource.FRAME_ESTIMATE, frame_age_ms=1000),
    )


def test_analysis_result_available_may_have_candidates_without_reason_codes() -> None:
    result = AnalysisResult(
        **_base_analysis_kwargs(),
        status=AnalysisStatus.AVAILABLE,
        support=AnalysisSupport(level="FULL", reason_codes=[]),
        candidates=[],
    )
    assert result.status == AnalysisStatus.AVAILABLE


def test_analysis_result_without_evidence_rejects_nonempty_candidates() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult(
            **_base_analysis_kwargs(),
            status=AnalysisStatus.INSUFFICIENT_EVIDENCE,
            support=AnalysisSupport(level="LIMITED", reason_codes=["SMALL_CONTEXT_SAMPLE"]),
            candidates=[
                {
                    "itemId": 100000,
                    "rank": 1,
                    "score": 0.1,
                    "remainingCost": 100,
                    "affordability": "UNKNOWN",
                    "evidenceCodes": [],
                }
            ],
        )


def test_analysis_result_without_evidence_requires_reason_codes() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult(
            **_base_analysis_kwargs(),
            status=AnalysisStatus.UNSUPPORTED,
            support=AnalysisSupport(level="NONE", reason_codes=[]),
            candidates=[],
        )


# ---------------------------------------------------------------------------
# AnalysisQuality.frameAgeMs: null e mascara de ausencia, nao 0 (revisao CC-106)
# ---------------------------------------------------------------------------


def test_analysis_quality_accepts_null_frame_age_when_budget_source_unknown() -> None:
    quality = AnalysisQuality(budget_source=BudgetSource.UNKNOWN, frame_age_ms=None)
    assert quality.frame_age_ms is None


def test_analysis_quality_rejects_negative_frame_age() -> None:
    with pytest.raises(ValidationError):
        AnalysisQuality(budget_source=BudgetSource.FRAME_ESTIMATE, frame_age_ms=-1)


# ---------------------------------------------------------------------------
# Feedback: grade 0..3 ou null
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grade", [0, 1, 2, 3, None])
def test_feedback_request_accepts_grade_in_range_or_null(grade: int | None) -> None:
    request = FeedbackRequest.model_validate(
        {
            "itemId": 100000,
            "grade": grade,
            "reason": "plausivel dado o contexto",
            "rubricVersion": "v1",
            "reviewerAlias": "revisor-01",
        }
    )
    assert request.grade == grade


@pytest.mark.parametrize("grade", [-1, 4])
def test_feedback_request_rejects_grade_outside_0_3(grade: int) -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(
            {
                "itemId": 100000,
                "grade": grade,
                "reason": "x",
                "rubricVersion": "v1",
                "reviewerAlias": "revisor-01",
            }
        )


# ---------------------------------------------------------------------------
# Decision state: STALE_FRAME consistente com frameAgeMs > 90s
# ---------------------------------------------------------------------------


def _budget(**overrides: object) -> DecisionBudget:
    base: dict[str, object] = {
        "source": BudgetSource.FRAME_ESTIMATE,
        "frame_age_ms": 90_001,
        "stale_frame": True,
        "immediate_purchase_assertable": False,
    }
    base.update(overrides)
    return DecisionBudget(**base)  # type: ignore[arg-type]


def test_decision_budget_rejects_stale_frame_false_above_threshold() -> None:
    with pytest.raises(ValidationError):
        _budget(frame_age_ms=90_001, stale_frame=False)


def test_decision_budget_accepts_stale_frame_true_above_threshold() -> None:
    assert _budget(frame_age_ms=90_001, stale_frame=True).stale_frame is True
    assert _budget(frame_age_ms=90_000, stale_frame=False).stale_frame is False  # limite


def test_decision_budget_accepts_null_frame_age_and_value_without_forcing_stale() -> None:
    """Sem snapshot anterior: null e mascara de ausencia (docs/domain-model.md) —
    nao e o mesmo que STALE_FRAME, e o valor desconhecido nunca vira 0."""
    budget = _budget(
        source=BudgetSource.UNKNOWN,
        frame_age_ms=None,
        stale_frame=False,
        value=None,
        codes=["UNKNOWN_BUDGET"],
    )
    assert budget.frame_age_ms is None and budget.value is None


def test_decision_summary_stale_frame_is_consistent_with_age() -> None:
    kwargs: dict[str, object] = {
        "decision_id": uuid4(),
        "participant_id": 1,
        "event_source_index": 0,
        "timestamp_ms": 1000,
        "eligible": True,
        "budget_source": BudgetSource.FRAME_ESTIMATE,
        "frame_age_ms": 90_001,
    }
    with pytest.raises(ValidationError):
        DecisionStateSummary(stale_frame=False, **kwargs)  # type: ignore[arg-type]
    assert DecisionStateSummary(stale_frame=True, **kwargs).stale_frame is True  # type: ignore[arg-type]


def test_decision_schemas_have_no_field_that_could_reveal_the_purchase() -> None:
    """Modo Estudo: antes de revelar, so informacao
    pre-decisao. Nenhum campo de item comprado, classe do alvo, bloco
    retrospectivo, features, qualidade cumulativa ou hash pessoal."""
    forbidden = {
        "target_item_id",
        "purchased_item_id",
        "target_class",
        "retrospective_target",
        "features",
        "quality",
        "subject_hash",
    }
    for model in (DecisionState, DecisionStateSummary):
        assert not set(model.model_fields) & forbidden, model.__name__
    assert set(DecisionInventory.model_fields) == {"items", "trinket_item_id"}
    # multiconjunto; `name` vem do catálogo fixado pelo ponto (null sem catálogo)
    assert set(InventoryItem.model_fields) == {"item_id", "quantity", "name"}
    assert InventoryItem(item_id=3006, quantity=1).name is None


# ---------------------------------------------------------------------------
# Partidas (CC-205): lista/detalhe sem identificador pessoal e com preparação
# ---------------------------------------------------------------------------

_MATCH_SUMMARY = {
    "matchId": "DEMO-0008",
    "queue": 420,
    "map": 11,
    "patch": "16.17.1",
    "startAt": "2026-09-01T12:00:00Z",
    "preparationStatus": "READY",
}


def test_match_summary_requires_preparation_status_and_rejects_personal_fields() -> None:
    assert MatchSummary.model_validate(_MATCH_SUMMARY).preparation_status == "READY"
    with pytest.raises(ValidationError):
        MatchSummary.model_validate(
            {k: v for k, v in _MATCH_SUMMARY.items() if k != "preparationStatus"}
        )
    with pytest.raises(ValidationError):
        MatchSummary.model_validate({**_MATCH_SUMMARY, "subjectHash": "x" * 64})
    with pytest.raises(ValidationError):
        MatchSummary.model_validate({**_MATCH_SUMMARY, "preparationStatus": "DONE"})


def test_match_detail_has_participants_without_hash_and_a_preparation_block() -> None:
    detail = MatchDetail.model_validate(
        {
            **_MATCH_SUMMARY,
            "preparationStatus": "FAILED",
            "participants": [{"participantId": 1, "championId": 22, "team": 100, "role": None}],
            "preparation": {
                "status": "FAILED",
                "errorCode": "TIMELINE_REPLAY_ERROR",
                "attempts": 1,
            },
        }
    )
    assert detail.participants[0].role is None  # ausente ≠ inventado
    assert detail.preparation.error_code == "TIMELINE_REPLAY_ERROR"
    assert set(MatchParticipant.model_fields) == {"participant_id", "champion_id", "team", "role"}
    with pytest.raises(ValidationError):
        MatchParticipant.model_validate(
            {"participantId": 1, "championId": 22, "team": 100, "subjectHash": "x" * 64}
        )
    with pytest.raises(ValidationError):
        MatchDetail.model_validate({**_MATCH_SUMMARY, "participants": []})  # sem preparation


def test_match_preparation_defaults_are_neutral() -> None:
    preparation = MatchPreparation.model_validate({"status": "NOT_REQUESTED"})
    assert preparation.error_code is None and preparation.retry_at is None
    assert preparation.attempts == 0


# ---------------------------------------------------------------------------
# Modo Estudo: sem rank/score antes do reveal; explicit:true obrigatorio
# ---------------------------------------------------------------------------


def test_learning_attempt_public_state_rejects_option_with_extra_ranking_fields() -> None:
    with pytest.raises(ValidationError):
        LearningAttemptPublicState(
            attempt_id=uuid4(),
            status=LearningAttemptState.OPEN,
            context={},
            options=[{"itemId": 100000, "rank": 1}],
            hint_level=0,
        )


def test_learning_attempt_public_state_rejects_analysis_id_before_reveal() -> None:
    with pytest.raises(ValidationError):
        LearningAttemptPublicState(
            attempt_id=uuid4(),
            status=LearningAttemptState.ANSWERED,
            context={},
            options=[{"itemId": 100000}],
            hint_level=0,
            analysis_id=uuid4(),
        )


def test_learning_attempt_answer_requires_item_unless_skipped() -> None:
    with pytest.raises(ValidationError):
        LearningAttemptAnswerRequest(skipped=False)
    assert LearningAttemptAnswerRequest(skipped=True).item_id is None


def test_learning_attempt_answer_rejects_rationale_over_1000_chars() -> None:
    with pytest.raises(ValidationError):
        LearningAttemptAnswerRequest(item_id=1, rationale="x" * 1001, skipped=False)


def test_learning_attempt_hint_rejects_level_outside_1_or_2() -> None:
    with pytest.raises(ValidationError):
        LearningAttemptHintRequest(level=3)


def test_learning_attempt_reveal_rejects_explicit_false() -> None:
    with pytest.raises(ValidationError):
        LearningAttemptRevealRequest.model_validate({"explicit": False})


def test_learning_attempt_reveal_requires_explicit_true() -> None:
    assert LearningAttemptRevealRequest.model_validate({"explicit": True}).explicit is True


# ---------------------------------------------------------------------------
# Contrato fechado: campo desconhecido e rejeitado (extra="forbid")
# ---------------------------------------------------------------------------


def test_camel_model_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ObjectiveInterpretRequest.model_validate({"text": "curto", "unexpectedField": True})
