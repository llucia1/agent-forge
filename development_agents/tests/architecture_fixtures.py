from typing import Any


def usable_architecture_sections(
    persistence_technology: str | None = None,
) -> dict[str, Any]:
    persistence = {}
    if persistence_technology is not None:
        persistence = {
            "stores": [
                {
                    "name": "primary",
                    "technology": persistence_technology,
                    "purpose": "Store project records",
                    "owned_by": "backend",
                    "data_models": [
                        {
                            "name": "ProjectRecord",
                            "description": "Persistent project data",
                        }
                    ],
                }
            ]
        }

    return {
        "modules": [
            {
                "name": "backend",
                "responsibility": "Provide application operations",
                "layer": "application",
                "dependencies": [],
            },
            {
                "name": "frontend",
                "responsibility": "Present application state",
                "layer": "presentation",
                "dependencies": ["backend"],
            },
        ],
        "interfaces": [
            {
                "name": "ProjectReader",
                "provider": "backend",
                "consumers": ["frontend"],
                "operations": [
                    {
                        "name": "read_project",
                        "input": {"project_id": "string"},
                        "output": {"status": "string"},
                    }
                ],
            }
        ],
        "apis": [
            {
                "name": "project-api",
                "protocol": "HTTP/JSON",
                "provider": "backend",
                "consumers": ["frontend"],
                "operations": [
                    {
                        "name": "get_project",
                        "method": "GET",
                        "path": "/projects/{project_id}",
                        "request": {"path": {"project_id": "string"}},
                        "responses": {"200": {"status": "string"}},
                    }
                ],
            }
        ],
        "persistence": persistence,
        "execution_plan": [
            {
                "order": 1,
                "name": "Implement backend",
                "description": "Build application operations",
                "modules": ["backend"],
            },
            {
                "order": 2,
                "name": "Implement frontend",
                "description": "Integrate the application API",
                "modules": ["frontend"],
            },
        ],
    }
