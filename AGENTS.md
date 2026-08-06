# Project Agent Instructions

Before making changes, read these files completely:

1. `docs/project-handoff.md`
2. `README.md`
3. `docs/integration.md`

## Current objective

Continue building the tender and bidding intelligent review system described in
`docs/project-handoff.md`. The immediate next milestone is to deploy Dify on the
development computer, create the first visual review workflow, and connect the
existing FastAPI service to the published Dify Workflow API.

## Architecture decisions

- Keep FastAPI as the integration layer for the existing business system.
- Use Dify as the visual orchestration, prompt, model, and RAG layer.
- Keep deterministic calculations and validation in Python or a rule engine.
- Treat the current Python agents as a runnable MVP and reusable tools; do not
  discard them when introducing Dify.
- The five specialist capabilities are document parsing, compliance review,
  data validation, anomaly analysis, and report generation.
- A supervisor workflow should coordinate the specialist capabilities and
  return one normalized issue list.

## Security

- Never commit, print, summarize, or transmit `.env` contents or real API keys.
- `.env` must remain ignored by Git. Use `.env.example` for configuration docs.
- Do not send local tender documents to an external model unless the user has
  explicitly approved transmitting those documents.

## Working agreement

- Inspect the current code and Git status before editing.
- Preserve existing user changes and keep integration contracts compatible.
- Verify changes with the smoke tests documented in the handoff.
- Update `docs/project-handoff.md` after material architecture or deployment
  milestones so work can continue on another computer.
