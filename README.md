# AI-Powered CI/CD Generator

Multi-agent generator (Anthropic SDK + Langfuse) that produces GitHub Actions pipelines for polyglot repos
(Java, Node.js, TypeScript, Python, .NET, Go).

**Claude plans, chooses tools and reviews. Jinja2 templates write the YAML**, so output is valid and consistent.

## What it generates (per application)

| Workflow | Contents |
|---|---|
| `<app>-validation.yml` | Composite CI action (lint/test/build), CodeQL, dependency review, gitleaks, Dockerfile lint, Trivy, single `Validation gate` job |
| `<app>-build.yml` | Buildx + metadata, registry login composite (ACR / ECR / JFrog / Nexus), SBOM + provenance, Trivy image scan, immutable `sha-<commit>` tag |
| `<app>-release.yml` + `_deploy.yml` | dev -> test -> uat -> prod via GitHub Environments (approval gates), kubectl (`azure/k8s-deploy`, basic/canary/bluegreen) or Helm (`--atomic`), rollback, image pull secrets for JFrog/Nexus |

Also: Dockerfile + `.dockerignore`, Helm chart with per-env values (or per-env kubectl manifests), Dependabot config for actions,
`docs/CICD_SETUP.md` and `secrets-manifest.yaml` listing every `vars.*` / `secrets.*` the workflows reference.

Registry auth uses actions (`aws-actions/*`, `docker/login-action`); AWS uses OIDC, ACR uses service-principal placeholders.
Cluster auth: kubeconfig secret (any cluster), AKS (`azure/login` + `aks-set-context`) or EKS (OIDC role).
The only CLI calls are the deploy tools themselves (`kubectl`, `helm`, and `aws eks update-kubeconfig` for EKS).

## Agents and models

| Agent | Default model | Job |
|---|---|---|
| Planner | `claude-haiku-5-5` (`claude-sonnet-5-5` when a diagram image is given) | Request + repo scan + diagram -> `PipelineSpec` (structured output) |
| Validation / Build / Release | `claude-sonnet-5-5` | Tool-calling loops over narrow render tools; skipped calls are completed deterministically |
| Reviewer | `claude-opus-5-5` | Reviews the bundle against a security/ops checklist |

Override with `MODEL_PLANNER`, `MODEL_VISION`, `MODEL_GENERATOR`, `MODEL_REVIEWER`. Set Langfuse keys to trace each run
(planner -> agents -> tool calls -> reviewer); without keys tracing is skipped.

## Usage

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # anthropic 1.x needs Python 3.10+
pip install -r requirements.txt
cp .env.example .env                                    # add ANTHROPIC_API_KEY

# Natural language + repo scan
python main.py -r "ACR, AKS, Helm" --repo ../my-monorepo -o out

# Architecture diagram (multi-modal)
python main.py -r "Generate CI/CD for this architecture" --diagram arch.png -o out

# No LLM: deterministic from a spec file
python main.py --no-llm --spec examples/spec.json -o out
```

`examples/spec.json` (copy and edit; `--spec` takes a path):
```json
{"apps": [{"name": "orders", "path": "services/orders", "tech": "java", "variant": "maven"}],
 "registry": "acr", "cluster": "aks", "deploy_method": "helm"}
```

Then follow `out/docs/CICD_SETUP.md` (environments, variables, secrets).

## Layout

```
agents/     planner, validation, build, release, reviewer, orchestrator
core/       llm (Anthropic + Langfuse), schemas, file bundle
tools/      renderer, agent tools, repo scanner, validator, docs generator
templates/  Jinja2 (<< >> / <% %> delimiters so ${{ }} and Helm {{ }} pass through)
config/     settings, technology_config.yaml (commands, versions, pinned actions, registries)
tests/      pytest (no network; LLM faked)
```

## Tests

```bash
python -m pytest -q
```

## Known limits
- Start command defaults per technology (`npm start`, `python main.py`, `dotnet <app>.dll`, ...); set `start_command` in the spec if yours differs.
- Actions are pinned to major versions, not SHAs; let the generated Dependabot config or your policy tooling pin them.
- Not yet verified against a live GitHub run or a live Anthropic call; static checks and mocked-LLM tests only.
