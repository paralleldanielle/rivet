### GitHub Copilot (`--adapter copilot`)
- **Launch:** run the `rivet-worker` custom agent as a subagent with the assignment, and wait for its result. When Rivet is installed as a plugin, Copilot CLI lists it as `rivet:rivet-worker`; VS Code's subagent tool takes `rivet-worker`.
- **Model:** declare the exact model name the session or subagent uses, as shown by the host.
- **Do not use** the Copilot coding agent (delegating to the cloud or assigning issues to Copilot) or any other background agent for this item.
