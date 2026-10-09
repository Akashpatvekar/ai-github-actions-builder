# agents/validation_agent.py

from agents.base_agent import BaseAgent
from config import settings
from core.schemas import PipelineSpec


class ValidationAgent(BaseAgent):
    """Generates validation assets: per-technology CI composite action and the validation workflow."""

    name = "ValidationAgent"
    model = settings.MODEL_GENERATOR
    tool_names = ["render_security_actions", "render_ci_action", "render_validation_workflow", "render_support_files"]
    system_prompt = (
        "You generate GitHub Actions validation pipelines by calling tools. Never write YAML yourself. "
        "Call render_security_actions once. For every application call render_ci_action first, then "
        "render_validation_workflow, and call render_support_files once. Finish with a one-paragraph summary of what was created."
    )

    def build_task(self, spec: PipelineSpec) -> str:
        apps = "\n".join(f"- {a.name} ({a.tech})" for a in spec.apps)
        return f"Create validation assets for these applications:\n{apps}"
