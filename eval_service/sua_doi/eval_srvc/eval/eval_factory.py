from eval_srvc.custom_types.eval_configs import PassFailEvalConfig, ScoreEvalConfig
from eval_srvc.custom_types.primitives import TEvalConfig
from eval_srvc.eval.base import BaseEval
from eval_srvc.eval.llm_as_judge.pass_fail_eval import PassFailEval
from eval_srvc.eval.llm_as_judge.score_eval import ScoreBasedEval

DEFAULT_JUDGE_MODEL = "gpt-6-luna"  # [VÁ CỦA MÌNH 06/11/2026 — nhóm kỹ thuật, chay_thu/eval_service/HUONG_DAN.md] gốc: "claude-sonnet-4-6" (máy không có khoá Anthropic)


class EvalFactory:

    @classmethod
    def get_eval(cls, config: TEvalConfig) -> BaseEval:
        """
        Create an evaluation instance from a configuration.

        Parameters
        ----------
        config : BaseEvalConfig
            Configuration for the evaluation to instantiate.

        Returns
        -------
        eval : BaseEval
            Instantiated evaluation object.

        Raises
        ------
        ValueError
            If the evaluation type is not supported.
        """
        if isinstance(config, PassFailEvalConfig):
            return PassFailEval(
                model=config.judge_model.value,
                pass_criteria=config.pass_criteria,
                fail_criteria=config.fail_criteria,
                na_criteria=config.na_criteria,
                additional_instructions=config.additional_instructions,
                metadata_inclusions=config.metadata_inclusions,
            )

        if isinstance(config, ScoreEvalConfig):
            return ScoreBasedEval(
                model=config.judge_model.value,
                scoring_criteria=config.scoring_criteria,
                additional_instructions=config.additional_instructions,
                metadata_inclusions=config.metadata_inclusions,
            )

        raise ValueError(f"Could not instantiate eval from spec: {config}")

    @classmethod
    def get_workflow_eval(cls, eval_type: str, config: dict) -> BaseEval:
        """Create a workflow evaluation instance from type string and config dict.

        Parameters
        ----------
        eval_type : str
            Eval type key: goal_achievement, output_quality, tool_use, quantitative_check.
        config : dict
            Eval-specific configuration.

        Returns
        -------
        eval : BaseEval
            Instantiated evaluation object.
        """
        model = config.get("judge_model", DEFAULT_JUDGE_MODEL)
        config = {k: v for k, v in config.items() if k != "judge_model"}

        if eval_type == "goal_achievement":
            from eval_srvc.eval.goal_achievement.goal_achievement_eval import (
                GoalAchievementEval,
            )
            return GoalAchievementEval(model=model, **config)

        if eval_type == "output_quality":
            from eval_srvc.eval.output_quality.output_quality_eval import (
                OutputQualityEval,
            )
            return OutputQualityEval(model=model, **config)

        if eval_type == "tool_use":
            from eval_srvc.eval.tool_use.tool_use_eval import ToolUseEval
            return ToolUseEval(model=model, **config)

        if eval_type == "quantitative_check":
            from eval_srvc.eval.quantitative_check.quantitative_check_eval import (
                QuantitativeCheckEval,
            )
            # No LLM call — `model` is unused but accepted for signature parity.
            return QuantitativeCheckEval(model=model, **config)

        raise ValueError(
            f"Unknown workflow eval type: '{eval_type}'. "
            f"Available: goal_achievement, output_quality, "
            f"tool_use, quantitative_check"
        )
