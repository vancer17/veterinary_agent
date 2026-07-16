from __future__ import annotations

from ingress.app import app, create_app


def main() -> None:
    import uvicorn

    uvicorn.run("vet_agent.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
