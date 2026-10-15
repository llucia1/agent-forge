import hashlib
import re
from dataclasses import dataclass


class SpecificationValidationError(ValueError):
    """Raised when a functional Markdown contract is invalid."""


PROJECT_SECTIONS = ("Objective", "Scope", "Global Rules")
FEATURE_SECTIONS = (
    "Objective",
    "Scope",
    "Business Rules",
    "Flows",
    "APIs and Interfaces",
    "Data",
    "Errors",
    "Edge Cases",
    "Security",
    "Acceptance Criteria",
)
SPECIFICATION_PATH_PATTERN = re.compile(
    r"specs/[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?\.md"
)
ACCEPTANCE_CRITERION_PATTERN = re.compile(
    r"-\s+(AC-[A-Z0-9][A-Z0-9_-]*)\s+"
    r"\[check:\s*([a-zA-Z0-9][a-zA-Z0-9._-]*)\]\s*:\s*(.+)"
)


@dataclass(frozen=True)
class AcceptanceCriterion:
    id: str
    check_id: str
    description: str


@dataclass(frozen=True)
class ProjectSpecification:
    title: str
    sections: dict[str, str]
    markdown: str

    @classmethod
    def from_markdown(cls, markdown: str) -> "ProjectSpecification":
        title, sections, normalized = _parse_markdown(
            markdown,
            PROJECT_SECTIONS,
            "project.md",
        )
        return cls(title, sections, normalized)


@dataclass(frozen=True)
class FeatureSpecification:
    title: str
    sections: dict[str, str]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    markdown: str

    @classmethod
    def from_markdown(cls, markdown: str) -> "FeatureSpecification":
        title, sections, normalized = _parse_markdown(
            markdown,
            FEATURE_SECTIONS,
            "feature specification",
        )
        criteria = []
        for line in sections["Acceptance Criteria"].splitlines():
            match = ACCEPTANCE_CRITERION_PATTERN.fullmatch(line.strip())
            if match is None:
                raise SpecificationValidationError(
                    "Acceptance criteria must use '- AC-ID [check: check-id]: "
                    "description'"
                )
            criteria.append(AcceptanceCriterion(*match.groups()))
        criterion_ids = [criterion.id for criterion in criteria]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise SpecificationValidationError(
                "Acceptance criterion ids must be unique"
            )
        return cls(title, sections, tuple(criteria), normalized)

    @property
    def fingerprint(self) -> str:
        return "sha256:" + hashlib.sha256(
            self.markdown.encode("utf-8")
        ).hexdigest()


def validate_specification_path(spec_path: str) -> None:
    if (
        not isinstance(spec_path, str)
        or SPECIFICATION_PATH_PATTERN.fullmatch(spec_path) is None
    ):
        raise SpecificationValidationError(
            "Specification path must match specs/<slug>.md"
        )


def _parse_markdown(
    markdown: str,
    required_sections: tuple[str, ...],
    label: str,
) -> tuple[str, dict[str, str], str]:
    if not isinstance(markdown, str):
        raise SpecificationValidationError(f"{label} must be Markdown text")
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = normalized.splitlines()
    if not lines or not lines[0].startswith("# ") or not lines[0][2:].strip():
        raise SpecificationValidationError(
            f"{label} requires one non-empty level-one title"
        )
    title = lines[0][2:].strip()
    sections: dict[str, list[str]] = {}
    current = None
    for line in lines[1:]:
        if line.startswith("# "):
            raise SpecificationValidationError(
                f"{label} must contain exactly one level-one title"
            )
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading not in required_sections or heading in sections:
                raise SpecificationValidationError(
                    f"{label} contains an unknown or duplicate section: "
                    f"{heading}"
                )
            current = heading
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
        elif line.strip():
            raise SpecificationValidationError(
                f"{label} contains content before its first section"
            )
    if tuple(sections) != required_sections:
        raise SpecificationValidationError(
            f"{label} sections must appear exactly in this order: "
            + ", ".join(required_sections)
        )
    parsed = {
        heading: "\n".join(content).strip()
        for heading, content in sections.items()
    }
    empty = [heading for heading, content in parsed.items() if not content]
    if empty:
        raise SpecificationValidationError(
            f"{label} sections must not be empty: " + ", ".join(empty)
        )
    return title, parsed, normalized + "\n"
