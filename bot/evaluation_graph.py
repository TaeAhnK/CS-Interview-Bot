"""Single-submission evaluation workflow shared by interview session callers."""

from bot.evaluation import EvaluationProvider, EvaluationRequest, EvaluationResponse


def evaluate_submission(
    request: EvaluationRequest, provider: EvaluationProvider
) -> EvaluationResponse:
    """Evaluate one answer using only the request's current-concept dialogue."""
    return EvaluationResponse.model_validate(provider.evaluate(request))
