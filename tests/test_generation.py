import itertools

import pytest
import yaml

from core.bundle import FileBundle
from core.schemas import AppSpec, PipelineSpec
from tools.docs import add_docs
from tools.renderer import Renderer
from tools.template_tools import ToolKit
from tools.yaml_validator import collect_references, validate_bundle

TECHS = ["java", "node", "typescript", "python", "dotnet", "go"]
REGISTRIES = ["acr", "ecr", "jfrog", "nexus"]
CLUSTERS = ["generic", "aks", "eks"]
METHODS = ["helm", "kubectl"]
ALL_TOOLS = [
    "render_security_actions", "render_build_actions", "render_deploy_actions",
    "render_ci_action", "render_validation_workflow", "render_support_files", "render_dockerfile",
    "render_registry_login_action", "render_build_workflow", "render_deploy_workflow",
    "render_deploy_assets", "render_release_workflow",
]


def generate(apps, registry="ecr", cluster="generic", method="helm", strategy="basic") -> FileBundle:
    spec = PipelineSpec(apps=apps, registry=registry, cluster=cluster, deploy_method=method, k8s_strategy=strategy)
    bundle = FileBundle()
    ToolKit(Renderer(spec, bundle)).complete(ALL_TOOLS)
    add_docs(bundle, spec)
    return bundle


@pytest.mark.parametrize("tech,registry,cluster,method", list(itertools.product(TECHS, REGISTRIES, CLUSTERS, METHODS)))
def test_matrix_is_valid(tech, registry, cluster, method):
    strategy = "canary" if method == "kubectl" else "basic"
    bundle = generate([AppSpec(name="svc", path="svc", tech=tech)], registry, cluster, method, strategy)
    assert validate_bundle(bundle) == []
    for path, content in bundle.files.items():
        if path.endswith((".yml", ".yaml")) and "/templates/" not in path:
            yaml.safe_load(content)


def test_every_reference_is_documented():
    bundle = generate([AppSpec(name="api", path="api", tech="java")], "jfrog", "aks", "helm")
    refs = collect_references(bundle)
    manifest = yaml.safe_load(bundle.files["secrets-manifest.yaml"])
    documented = {r["name"] for r in manifest["variables"]} | {r["name"] for r in manifest["secrets"]}
    assert (refs["secrets"] | refs["vars"]) <= documented
    assert "REGISTRY_TOKEN" in refs["secrets"]
    assert "REGISTRY_HOST" in bundle.files["docs/CICD_SETUP.md"]


def test_multi_app_monorepo_has_per_app_workflows_and_shared_files():
    apps = [AppSpec(name="orders", path="services/orders", tech="java", variant="gradle"),
            AppSpec(name="web", path="web", tech="typescript", variant="yarn")]
    bundle = generate(apps)
    for app in ("orders", "web"):
        for kind in ("validation", "build", "release"):
            assert f".github/workflows/{app}-{kind}.yml" in bundle.files
    assert ".github/actions/ci-java-gradle/action.yml" in bundle.files
    assert ".github/actions/ci-typescript-yarn/action.yml" in bundle.files
    assert "services/orders/Dockerfile" in bundle.files
    assert validate_bundle(bundle) == []


def test_unknown_variant_is_rejected():
    spec = PipelineSpec(apps=[AppSpec(name="x", tech="go", variant="maven")])
    with pytest.raises(ValueError):
        Renderer(spec, FileBundle()).ci_action("x")


def test_toolkit_completes_skipped_calls():
    spec = PipelineSpec(apps=[AppSpec(name="a", tech="go"), AppSpec(name="b", tech="python")])
    kit = ToolKit(Renderer(spec, FileBundle()))
    kit.all()["render_ci_action"].fn(app_name="a")
    gaps = kit.complete(["render_ci_action"])
    assert gaps == [("render_ci_action", "b")]


def test_release_environments_follow_spec_and_gate_order():
    spec = PipelineSpec(apps=[AppSpec(name="a", tech="go")], environments=["dev", "prod"])
    bundle = FileBundle()
    Renderer(spec, bundle).release_workflow("a")
    wf = yaml.safe_load(bundle.files[".github/workflows/a-release.yml"])
    assert [j for j in wf["jobs"] if j.startswith("deploy-")] == ["deploy-dev", "deploy-prod"]
    assert "deploy-dev" in wf["jobs"]["deploy-prod"]["needs"]


COMPOSITES = {"codeql-scan", "trivy-scan", "docker-build-push", "resolve-image", "k8s-prepare"}


@pytest.mark.parametrize("method", METHODS)
def test_workflows_delegate_to_composite_actions(method):
    bundle = generate([AppSpec(name="svc", path="svc", tech="go")], "ecr", "aks", method)
    for name in COMPOSITES | {"k8s-context-aks", f"deploy-{method}", "registry-login-ecr", "ci-go-go"}:
        assert f".github/actions/{name}/action.yml" in bundle.files, name
    deploy = bundle.files[".github/workflows/_deploy.yml"]
    assert f"./.github/actions/deploy-{method}" in deploy
    assert "helm upgrade" not in deploy and "azure/k8s-deploy" not in deploy  # logic lives in the composite
    build = bundle.files[".github/workflows/svc-build.yml"]
    assert "./.github/actions/docker-build-push" in build and "docker/build-push-action" not in build
    assert "./.github/actions/codeql-scan" in bundle.files[".github/workflows/svc-validation.yml"]
    assert validate_bundle(bundle) == []  # includes composite input/required-input checks


def test_recommended_kubernetes_labels():
    for method in METHODS:
        bundle = generate([AppSpec(name="svc", path="svc", tech="go")], method=method)
        text = "\n".join(c for p, c in bundle.files.items() if p.startswith("deploy/"))
        for label in ("name", "instance", "component", "part-of", "managed-by"):
            assert f"app.kubernetes.io/{label}:" in text, (method, label)
        assert "\n    app: " not in text  # legacy label removed
