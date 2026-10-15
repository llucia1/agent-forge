def project_markdown() -> str:
    return """# Task Tracker

## Objective
Allow users to track tasks.

## Scope
Task creation and listing.

## Global Rules
- Keep contracts explicit.
"""


def feature_markdown(*check_ids: str) -> str:
    criteria = "\n".join(
        f"- AC-{index:03d} [check: {check_id}]: Criterion {index} passes."
        for index, check_id in enumerate(check_ids or ("spec-check",), 1)
    )
    return f"""# Create a task

## Objective
Create a task with a title.

## Scope
Backend endpoint and frontend form.

## Business Rules
- Titles are required.

## Flows
1. Submit a title. 2. Store and display the task.

## APIs and Interfaces
- POST /tasks accepts a title and returns the task.

## Data
- Task: id, title.

## Errors
- Missing titles return a validation error.

## Edge Cases
- Duplicate titles are allowed.

## Security
- Treat titles as untrusted input.

## Acceptance Criteria
{criteria}
"""
