# agents/release_agent.py

from agents.base_agent import BaseAgent
from config import settings
from core.schemas import PipelineSpec


class ReleaseAgent(BaseAgent):
    """Generates the shared deploy workflow, per-app release workflows and Helm/kubectl assets."""

    name = "ReleaseAgent"
    model = settings.MODEL_GENERATOR
    tool_names = ["render_deploy_actions", "render_deploy_workflow", "render_deploy_assets", "render_release_workflow"]
    system_prompt = (
        "You generate Kubernetes release pipelines by calling tools. Never write YAML yourself. "
        "Call render_deploy_actions and render_deploy_workflow once, then for every application call render_deploy_assets and "
        "render_release_workflow. Finish with a one-paragraph summary."
    )

    def build_task(self, spec: PipelineSpec) -> str:
        apps = ", ".join(a.name for a in spec.apps)
        return (
            f"Deployment method: {spec.deploy_method} on {spec.cluster}. "
            f"Environments: {', '.join(spec.environments)}. Applications: {apps}."
        )
