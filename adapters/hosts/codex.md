### Codex (`--adapter codex`)
- **Launch:** ask Codex to spawn the `rivet_worker` custom agent with the assignment, and wait for it. If that agent is not installed (`rivet bootstrap --host codex` installs it), spawn the built-in `worker` agent instead and put the worker brief (`references/worker.md` in the rivet-work skill) at the top of the assignment.
- **Model:** declare the model you ask the subagent to use, or the session's model name if you don't choose one.
- **Do not use** cloud tasks or several subagents at once for this item.
