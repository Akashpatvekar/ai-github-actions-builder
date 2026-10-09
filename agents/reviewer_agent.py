# agents/reviewer_agent.py

from typing import List

from config import settings
from core.bundle import FileBundle
from core.llm import observe, parse
from core.schemas import PipelineSpec, ReviewReport

SYSTEM = """You are a senior platform engineer reviewing generated GitHub Actions pipelines.
Check against: least-privilege permissions, pinned actions, secrets vs vars separation, no hard-coded
credentials, OIDC preferred over long-lived keys, correct job dependencies, environment approval gates,
image pulled by immutable reference, rollback on failure, and consistency between workflows (names, paths,
variables). Report only real problems with a concrete file and fix; do not pad with style nits.
Static validator findings are provided - confirm or dismiss them."""

MAX_CHARS = 150_000


class ReviewerAgent:
    name = "ReviewerAgent"
    model = settings.MODEL_REVIEWER

    @observe()
    def review(self, spec: PipelineSpec, bundle: FileBundle, validator_issues: List[str]) -> ReviewReport:
        parts, size = [], 0
        for path, content in sorted(bundle.files.items()):
            if not path.startswith(".github/") and not path.endswith("Dockerfile"):
                continue
            chunk = f"=== {path}\n{content}\n"
            if size + len(chunk) > MAX_CHARS:
                break
            parts.append(chunk)
            size += len(chunk)
        task = (
            f"Spec:\n{spec.model_dump_json(indent=2)}\n\nStatic validator findings:\n"
            + ("\n".join(validator_issues) or "none")
            + "\n\nFiles:\n"
            + "".join(parts)
        )
        return parse(self.model, SYSTEM, task, ReviewReport, max_tokens=8000)
