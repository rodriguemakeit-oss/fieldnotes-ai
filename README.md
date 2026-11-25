# Fieldnotes AI

Azure Functions examples for searching and summarizing a knowledge base using language-model services.

## What it demonstrates

- Independently deployable function applications
- Retrieval-based synthesis over a configured knowledge source
- Environment-based configuration for service endpoints and credentials

## Repository map

The two function applications live under `src/func-002/` and `src/func-003/`. Each has its own dependencies and startup instructions.

## Run locally

See [GUIDE.md](GUIDE.md). You will need Azure Functions Core Tools and your own search index, model endpoint, and credentials. No knowledge corpus or hosted deployment is included.

## Scope

This portfolio adaptation uses example settings and requires validation against your own services and data. See [VALIDATION.md](VALIDATION.md) for the checks performed and their limits.