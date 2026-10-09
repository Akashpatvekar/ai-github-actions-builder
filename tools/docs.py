# tools/docs.py
"""Setup guide + secrets manifest, derived from what the workflows actually reference."""

from typing import Dict, List

import yaml

from config import TECH_CONFIG
from core.bundle import FileBundle
from core.schemas import PipelineSpec
from tools.yaml_validator import collect_references

EXTRA = {
    "K8S_NAMESPACE": ("var", "Namespace override per environment (default: <app>-<env>)", "environment"),
    "GITLEAKS_LICENSE": ("secret", "Gitleaks license key (only for organization-owned repositories)", "repository"),
}


def _describe(spec: PipelineSpec) -> Dict[str, Dict[str, str]]:
    """name -> {description, scope} for every known variable/secret."""
    reg = TECH_CONFIG["registries"][spec.registry]
    cluster = TECH_CONFIG["clusters"][spec.cluster]
    info: Dict[str, Dict[str, str]] = {}
    for kind in ("vars", "secrets"):
        for name, desc in reg[kind].items():
            info[name] = {"description": desc, "scope": "repository"}
        for name, desc in cluster[kind].items():
            if name in info:
                info[name]["scope"] = "repository (build) + environment (deploy)"
            else:
                info[name] = {"description": desc, "scope": "environment"}
    for name, (_, desc, scope) in EXTRA.items():
        info[name] = {"description": desc, "scope": scope}
    return info


def secrets_manifest(bundle: FileBundle, spec: PipelineSpec) -> str:
    refs = collect_references(bundle)
    info = _describe(spec)
    unknown = {"description": "Referenced by a workflow; set as needed", "scope": "repository"}

    def rows(names: List[str]) -> List[Dict[str, str]]:
        return [{"name": n, **info.get(n, unknown), "value": "<placeholder>"} for n in sorted(names)]

    return yaml.safe_dump(
        {"variables": rows(list(refs["vars"])), "secrets": rows(list(refs["secrets"]))}, sort_keys=False
    )


def setup_guide(bundle: FileBundle, spec: PipelineSpec) -> str:
    refs = collect_references(bundle)
    info = _describe(spec)
    reg = TECH_CONFIG["registries"][spec.registry]
    cluster = TECH_CONFIG["clusters"][spec.cluster]

    def table(names: List[str]) -> str:
        lines = ["| Name | Scope | Purpose |", "|---|---|---|"]
        for n in sorted(names):
            d = info.get(n, {"description": "Referenced by a workflow", "scope": "repository"})
            lines.append(f"| `{n}` | {d['scope']} | {d['description']} |")
        return "\n".join(lines)

    envs = ", ".join(spec.environments)
    apps = "\n".join(f"- `{a.name}` ({a.tech}) at `{a.path}`" for a in spec.apps)
    protected = ", ".join(e for e in spec.environments if TECH_CONFIG["environments"][e]["approvals"]) or "none"
    return f"""# CI/CD setup

Generated pipeline: **{reg['display']}** registry, **{cluster['display']}**, deployed with **{spec.deploy_method}**.

## Applications
{apps}

Each application gets `<app>-validation.yml`, `<app>-build.yml` and `<app>-release.yml`; all releases share `_deploy.yml`.

## 1. GitHub Environments
Create these environments (Settings -> Environments): {envs}.
Add required reviewers on: {protected}. Set cluster variables/secrets per environment.

## 2. Variables (Settings -> Secrets and variables -> Actions -> Variables)
{table(sorted(refs['vars']))}

## 3. Secrets
{table(sorted(refs['secrets']))}

`secrets-manifest.yaml` lists the same items with `<placeholder>` values for review or automation.
Never commit real values.

## 4. One-time registry/cluster prerequisites
- Registry: create the repository/namespace for each application image{' (ECR repositories must exist before the first push)' if spec.registry == 'ecr' else ''}.
- Credentials: {'federate the IAM role with GitHub OIDC (sub: repo:<org>/<repo>:*)' if spec.registry == 'ecr' else 'create a service account/token with push rights (build) and pull rights (cluster)'}.
- Cluster: grant the deploy identity rights to create namespaces, Deployments, Services and Secrets.

## 5. Hardening notes
- Actions are pinned to major versions; enable the generated Dependabot config and consider pinning to commit SHAs.
- Require the `Validation gate` check on protected branches.
- Deploys use the immutable `sha-<commit>` tag, or an image digest when supplied via workflow_dispatch.
"""


def add_docs(bundle: FileBundle, spec: PipelineSpec) -> None:
    bundle.add("docs/CICD_SETUP.md", setup_guide(bundle, spec))
    bundle.add("secrets-manifest.yaml", secrets_manifest(bundle, spec))
