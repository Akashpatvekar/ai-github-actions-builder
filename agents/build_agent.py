# agents/build_agent.py

from agents.base_agent import BaseAgent
from config import settings
from core.schemas import PipelineSpec


class BuildAgent(BaseAgent):
    """Generates Dockerfiles, registry authentication and the image build/scan/push workflow."""

    name = "BuildAgent"
    model = settings.MODEL_GENERATOR
    tool_names = ["render_registry_login_action", "render_build_actions", "render_dockerfile", "render_build_workflow"]
    system_prompt = (
        "You generate container build pipelines by calling tools. Never write YAML or Dockerfiles yourself. "
        "Call render_registry_login_action and render_build_actions once, then for every application call render_dockerfile and "
        "render_build_workflow. Finish with a one-paragraph summary."
    )

    def build_task(self, spec: PipelineSpec) -> str:
        apps = "\n".join(f"- {a.name} ({a.tech})" for a in spec.apps)
        return f"Registry: {spec.registry}. Create build assets for:\n{apps}"
