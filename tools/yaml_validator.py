# tools/yaml_validator.py
"""Static checks over the generated bundle. Cheap, deterministic, run before the LLM reviewer."""

import re
from typing import Any, Dict, List, Set

import yaml

from core.bundle import FileBundle

_USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.M)
SECRET_REF = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)")
VAR_REF = re.compile(r"\bvars\.([A-Za-z_][A-Za-z0-9_]*)")


def _is_workflow(path: str) -> bool:
    return path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))


def _is_parsable_yaml(path: str) -> bool:
    return path.endswith((".yml", ".yaml")) and "/templates/" not in path


def validate_bundle(bundle: FileBundle) -> List[str]:
    issues: List[str] = []
    for path, content in bundle.files.items():
        if not _is_parsable_yaml(path):
            continue
        try:
            doc = yaml.safe_load(content)
        except yaml.YAMLError as e:
            issues.append(f"{path}: invalid YAML ({str(e).splitlines()[0]})")
            continue

        for ref in _USES.findall(content):
            if ref.startswith("./"):
                if not _local_ref_exists(ref, bundle):
                    issues.append(f"{path}: local reference {ref} not generated")
                continue
            if "@" not in ref:
                issues.append(f"{path}: action {ref} has no version pin")
            elif ref.split("@", 1)[1] in ("main", "master", "latest"):
                issues.append(f"{path}: action {ref} pinned to a moving branch")

        if _is_workflow(path) and isinstance(doc, dict):
            issues += _check_workflow(path, doc)
        issues += _check_local_action_inputs(path, doc, bundle)
    return issues


def _steps(doc: Any) -> List[Dict[str, Any]]:
    """Steps of a workflow (all jobs) or of a composite action."""
    if not isinstance(doc, dict):
        return []
    if isinstance(doc.get("runs"), dict):
        return doc["runs"].get("steps") or []
    return [st for job in (doc.get("jobs") or {}).values() for st in (job.get("steps") or [])]


def _check_local_action_inputs(path: str, doc: Any, bundle: FileBundle) -> List[str]:
    """Inputs passed to a local composite action must exist, and required ones must be passed."""
    issues: List[str] = []
    for step in _steps(doc):
        ref = step.get("uses", "")
        if not ref.startswith("./.github/actions/"):
            continue
        action_path = ref[2:].rstrip("/") + "/action.yml"
        if action_path not in bundle.files:
            continue  # reported elsewhere
        declared = (yaml.safe_load(bundle.files[action_path]) or {}).get("inputs") or {}
        passed = set((step.get("with") or {}).keys())
        for unknown in sorted(passed - set(declared)):
            issues.append(f"{path}: {ref} has no input '{unknown}'")
        for name, spec in declared.items():
            if spec.get("required") and name not in passed:
                issues.append(f"{path}: {ref} missing required input '{name}'")
    return issues


def _local_ref_exists(ref: str, bundle: FileBundle) -> bool:
    target = ref[2:].rstrip("/")
    if target.endswith((".yml", ".yaml")):
        return target in bundle.files
    return f"{target}/action.yml" in bundle.files


def _check_workflow(path: str, doc: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    jobs: Dict[str, Any] = doc.get("jobs") or {}
    # PyYAML (YAML 1.1) parses the key `on` as boolean True
    if "on" not in doc and True not in doc:
        issues.append(f"{path}: missing 'on' trigger")
    if "permissions" not in doc and not all("permissions" in j for j in jobs.values()):
        issues.append(f"{path}: no least-privilege 'permissions' block")
    for name, job in jobs.items():
        needs = job.get("needs", [])
        for n in [needs] if isinstance(needs, str) else needs:
            if n not in jobs:
                issues.append(f"{path}: job '{name}' needs unknown job '{n}'")
    return issues


def collect_references(bundle: FileBundle) -> Dict[str, Set[str]]:
    """All secrets.* and vars.* referenced anywhere in workflows and composite actions."""
    secrets: Set[str] = set()
    variables: Set[str] = set()
    for path, content in bundle.files.items():
        if path.startswith(".github/"):
            secrets |= set(SECRET_REF.findall(content))
            variables |= set(VAR_REF.findall(content))
    secrets.discard("GITHUB_TOKEN")  # provided automatically
    return {"secrets": secrets, "vars": variables}
