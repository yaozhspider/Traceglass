# Existing integrations implementation plan

**Goal:** Make the existing reverse tools, Skills, model and sandbox usable from Traceglass, with real-call verification.

**Architecture:** AgentScope remains the only agent runtime. Load the explicitly selected legacy project's model configuration without copying credentials. Use its full analysis image as the base for an AgentScope Docker workspace, and expose existing domain functions through a small MCP server running inside that workspace. Load all legacy Skill packages through the native loader, with the legacy resource reader for sibling references.

**Constraints:** Never modify `reverse` or restart its services. Never load its agent factory, runtime or orchestration. Do not expose model credentials to the analysis container. Keep old sessions intact. Configured, reachable and successfully exercised are different states.

- [ ] Add configuration tests for legacy model inheritance, explicit overrides, Skill discovery and secret redaction. Implement the configuration bridge.
- [ ] Register the 33 domain functions and existing evidence/Skill helpers in a stdio MCP adapter; verify schemas and a real call.
- [ ] Build an analysis image from the existing full tool image. Clear its old entrypoint/configuration, copy only tool support code/resources and the MCP adapter, and run native DockerWorkspaceManager.
- [ ] Verify a native sandbox shell, legacy hash/metadata tools, Skill resource access, IDA connectivity and an external model reply. Use a harmless generated fixture, never execute an unknown sample.
- [ ] Show configured model, Skill, tool and workspace details in the product configuration; update setup instructions with actual verification limits.
- [ ] Run backend tests and frontend build. Retain a visible live smoke-test session and record results without credentials.
