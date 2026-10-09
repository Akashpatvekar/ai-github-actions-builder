# tools/template_tools.py
"""Agent-callable tools. Each wraps one Renderer method with a narrow, well-described contract."""

from typing import Callable, Dict, List, Set, Tuple

from core.llm import Tool
from tools.renderer import Renderer


class ToolKit:
    """Builds Tool objects bound to one Renderer and records which calls actually happened,
    so the orchestrator can complete anything the model skipped."""

    def __init__(self, renderer: Renderer):
        self.r = renderer
        self.called: Set[Tuple[str, str]] = set()
        self.app_names = sorted(renderer.apps)

    def _per_app(self, name: str, description: str, fn: Callable[[str], List[str]]) -> Tool:
        def run(app_name: str) -> str:
            files = fn(app_name)
            self.called.add((name, app_name))
            return "Wrote: " + ", ".join(files)

        schema = {
            "type": "object",
            "properties": {"app_name": {"type": "string", "enum": self.app_names}},
            "required": ["app_name"],
        }
        return Tool(name, description, schema, run)

    def _global(self, name: str, description: str, fn: Callable[[], List[str]]) -> Tool:
        def run() -> str:
            files = fn()
            self.called.add((name, ""))
            return "Wrote: " + ", ".join(files)

        return Tool(name, description, {"type": "object", "properties": {}}, run)

    def all(self) -> Dict[str, Tool]:
        r = self.r
        tools = [
            self._per_app("render_ci_action", "Create the composite GitHub Action that sets up the runtime and runs lint, test and build for an app's technology.", r.ci_action),
            self._global("render_security_actions", "Create the shared composite actions for CodeQL and Trivy scanning.", r.security_actions),
            self._per_app("render_validation_workflow", "Create the validation workflow (CI composite, CodeQL, dependency review, secret scan, Dockerfile lint, Trivy, gate job) for an app.", r.validation_workflow),
            self._per_app("render_dockerfile", "Create a multi-stage, non-root Dockerfile and .dockerignore for an app.", r.dockerfile),
            self._global("render_registry_login_action", "Create the composite action that authenticates to the selected container registry (ACR/ECR/JFrog/Nexus).", r.registry_login_action),
            self._global("render_build_actions", "Create the shared composite actions for docker build/push and image scanning.", r.build_actions),
            self._per_app("render_build_workflow", "Create the docker build/scan/push workflow for an app.", r.build_workflow),
            self._global("render_deploy_actions", "Create the shared composite actions for image resolution, cluster context, namespace preparation and helm/kubectl deployment.", r.deploy_actions),
            self._global("render_deploy_workflow", "Create the shared reusable deployment workflow (cluster auth, pull secret, helm or kubectl, rollout verification).", r.deploy_workflow),
            self._per_app("render_release_workflow", "Create the per-app release workflow promoting through the configured environments.", r.release_workflow),
            self._per_app("render_deploy_assets", "Create the Helm chart or kubectl manifests (per environment) for an app.", r.deploy_assets),
            self._global("render_support_files", "Create repo-level support files such as Dependabot config for action updates.", r.support_files),
        ]
        return {t.name: t for t in tools}

    def select(self, names: List[str]) -> List[Tool]:
        every = self.all()
        return [every[n] for n in names]

    def missing(self, names: List[str]) -> List[Tuple[str, str]]:
        """(tool, app) pairs among `names` the model did not call."""
        out: List[Tuple[str, str]] = []
        every = self.all()
        for n in names:
            per_app = "app_name" in every[n].input_schema["properties"]
            for app in self.app_names if per_app else [""]:
                if (n, app) not in self.called:
                    out.append((n, app))
        return out

    def complete(self, names: List[str]) -> List[Tuple[str, str]]:
        """Run anything in `names` that was never called. Returns what had to be filled in."""
        every = self.all()
        gaps = self.missing(names)
        for n, app in gaps:
            every[n].fn(**({"app_name": app} if app else {}))
        return gaps
