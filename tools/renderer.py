# tools/renderer.py
"""Deterministic Jinja2 rendering of every generated file. No LLM involved: this is what makes
the output valid and consistent. Agents decide *what* to render; this decides *how*."""

import json
import os
import shlex
from typing import Any, Dict, List

import jinja2

from config import TECH_CONFIG
from core.bundle import FileBundle
from core.schemas import AppSpec, PipelineSpec

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

# `${{ }}` (GitHub) and `{{ }}` (Helm) must pass through untouched, so Jinja uses << >> / <% %>.
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
    variable_start_string="<<",
    variable_end_string=">>",
    block_start_string="<%",
    block_end_string="%>",
    comment_start_string="<#",
    comment_end_string="#>",
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)
_env.globals["actions"] = TECH_CONFIG["actions"]


def _ci_action_name(app: AppSpec, variant: str) -> str:
    return f"ci-{app.tech}-{variant}"


def resolve_app(app: AppSpec) -> Dict[str, Any]:
    """Merge AppSpec with technology defaults into a flat template context."""
    t = TECH_CONFIG["technologies"][app.tech]
    variant = app.variant or t["default_variant"]
    if variant not in t["variants"]:
        raise ValueError(f"{app.tech} has no build variant '{variant}' (choose from {list(t['variants'])})")
    version = app.version or t["default_version"]
    port = app.port or t["default_port"]
    start = app.start_command or t["default_start"]
    if app.tech == "dotnet" and not app.start_command:
        start = f"dotnet {app.name}.dll"
    norm = os.path.normpath(app.path or ".")
    base = "" if norm == "." else norm.strip("/")
    return {
        "app": app,
        "t": t,
        "v": t["variants"][variant],
        "variant": variant,
        "version": version,
        "version_major_minor": ".".join(version.split(".")[:2]),
        "port": port,
        "start_cmd_json": json.dumps(shlex.split(start)),
        "path_glob": f"{base}/**" if base else "**",
        "ci_action": _ci_action_name(app, variant),
    }


class Renderer:
    """Renders templates for one PipelineSpec into a FileBundle."""

    def __init__(self, spec: PipelineSpec, bundle: FileBundle):
        self.spec = spec
        self.bundle = bundle
        self.apps = {a.name: a for a in spec.apps}
        self.reg = TECH_CONFIG["registries"][spec.registry]

    def _app(self, name: str) -> AppSpec:
        if name not in self.apps:
            raise ValueError(f"Unknown app '{name}'. Known apps: {sorted(self.apps)}")
        return self.apps[name]

    def _render(self, template: str, **ctx: Any) -> str:
        return _env.get_template(template).render(**ctx)

    def _common(self) -> Dict[str, Any]:
        return {
            "registry": self.spec.registry,
            "reg": self.reg,
            "cluster": self.spec.cluster,
            "cluster_cfg": TECH_CONFIG["clusters"][self.spec.cluster],
            "method": self.spec.deploy_method,
            "k8s_strategy": self.spec.k8s_strategy,
            "envs": self.spec.environments,
        }

    # ---- validation ------------------------------------------------------------------
    def _ctx(self, app_name: str) -> Dict[str, Any]:
        return {**resolve_app(self._app(app_name)), **self._common()}

    def ci_action(self, app_name: str) -> List[str]:
        ctx = resolve_app(self._app(app_name))
        path = f".github/actions/{ctx['ci_action']}/action.yml"
        self.bundle.add(path, self._render("actions/ci.yml.j2", **ctx))
        return [path]

    def validation_workflow(self, app_name: str) -> List[str]:
        ctx = resolve_app(self._app(app_name))
        path = f".github/workflows/{app_name}-validation.yml"
        self.bundle.add(path, self._render("workflows/validation.yml.j2", **ctx, **self._common()))
        return [path]

    def security_actions(self) -> List[str]:
        """Shared composite actions used by validation: CodeQL and Trivy."""
        written = []
        for name in ("codeql-scan", "trivy-scan"):
            path = f".github/actions/{name}/action.yml"
            self.bundle.add(path, self._render(f"actions/{name}.yml.j2"))
            written.append(path)
        return written

    # ---- build -----------------------------------------------------------------------
    def dockerfile(self, app_name: str) -> List[str]:
        ctx = resolve_app(self._app(app_name))
        norm = os.path.normpath(ctx["app"].path or ".")
        prefix = "" if norm == "." else norm.strip("/") + "/"
        files = [f"{prefix}Dockerfile", f"{prefix}.dockerignore"]
        self.bundle.add(files[0], self._render("docker/Dockerfile.j2", **ctx))
        self.bundle.add(files[1], self._render("docker/dockerignore.j2", **ctx))
        return files

    def registry_login_action(self) -> List[str]:
        path = f".github/actions/registry-login-{self.spec.registry}/action.yml"
        self.bundle.add(path, self._render("actions/registry-login.yml.j2", **self._common()))
        return [path]

    def build_actions(self) -> List[str]:
        """Shared composite actions used by build: docker build/push/scan (+ Trivy)."""
        written = []
        for name in ("docker-build-push", "trivy-scan"):
            path = f".github/actions/{name}/action.yml"
            self.bundle.add(path, self._render(f"actions/{name}.yml.j2"))
            written.append(path)
        return written

    def build_workflow(self, app_name: str) -> List[str]:
        ctx = resolve_app(self._app(app_name))
        path = f".github/workflows/{app_name}-build.yml"
        self.bundle.add(path, self._render("workflows/build.yml.j2", **ctx, **self._common()))
        return [path]

    # ---- release ---------------------------------------------------------------------
    def deploy_actions(self) -> List[str]:
        """Shared composite actions used by deployment: image resolve, cluster context, prepare, deploy."""
        c = self._common()
        plan = [
            ("resolve-image", "actions/resolve-image.yml.j2"),
            (f"k8s-context-{self.spec.cluster}", "actions/k8s-context.yml.j2"),
            ("k8s-prepare", "actions/k8s-prepare.yml.j2"),
            (f"deploy-{self.spec.deploy_method}", f"actions/deploy-{self.spec.deploy_method}.yml.j2"),
        ]
        written = []
        for name, template in plan:
            path = f".github/actions/{name}/action.yml"
            self.bundle.add(path, self._render(template, **c))
            written.append(path)
        return written

    def deploy_workflow(self) -> List[str]:
        path = ".github/workflows/_deploy.yml"
        self.bundle.add(path, self._render("workflows/deploy-reusable.yml.j2", **self._common()))
        return [path]

    def release_workflow(self, app_name: str) -> List[str]:
        ctx = resolve_app(self._app(app_name))
        path = f".github/workflows/{app_name}-release.yml"
        self.bundle.add(path, self._render("workflows/release.yml.j2", **ctx, **self._common()))
        return [path]

    def deploy_assets(self, app_name: str) -> List[str]:
        ctx = resolve_app(self._app(app_name))
        written: List[str] = []
        if self.spec.deploy_method == "helm":
            root = f"deploy/helm/{app_name}"
            self.bundle.add(f"{root}/Chart.yaml", self._render("helm/Chart.yaml.j2", **ctx))
            self.bundle.add(f"{root}/values.yaml", self._render("helm/values.yaml.j2", **ctx))
            written += [f"{root}/Chart.yaml", f"{root}/values.yaml"]
            for env in self.spec.environments:
                p = f"{root}/values-{env}.yaml"
                replicas = TECH_CONFIG["environments"][env]["replicas"]
                self.bundle.add(p, self._render("helm/values-env.yaml.j2", **ctx, env=env, replicas=replicas))
                written.append(p)
            helm_dir = os.path.join(TEMPLATE_DIR, "helm", "templates")
            for name in sorted(os.listdir(helm_dir)):
                with open(os.path.join(helm_dir, name)) as f:
                    self.bundle.add(f"{root}/templates/{name}", f.read())
                written.append(f"{root}/templates/{name}")
        else:
            for env in self.spec.environments:
                replicas = TECH_CONFIG["environments"][env]["replicas"]
                for kind in ("deployment", "service"):
                    p = f"deploy/k8s/{app_name}/{env}/{kind}.yaml"
                    self.bundle.add(
                        p,
                        self._render(
                            f"k8s/{kind}.yaml.j2", **ctx, env=env, replicas=replicas,
                            placeholder_image="IMAGE_PLACEHOLDER",
                        ),
                    )
                    written.append(p)
        return written

    # ---- repo-level ------------------------------------------------------------------
    def support_files(self) -> List[str]:
        path = ".github/dependabot.yml"
        content = (
            "version: 2\nupdates:\n"
            "  - package-ecosystem: github-actions\n    directory: /\n    schedule:\n      interval: weekly\n"
        )
        self.bundle.add(path, content)
        return [path]
