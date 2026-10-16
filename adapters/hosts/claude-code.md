### Claude Code (`--adapter claude-code`)
- **Launch:** the Agent tool with `subagent_type: "rivet:rivet-worker"`, in the foreground. Pass the assignment as the prompt and wait for the result.
- **Model:** if you set the Agent tool's `model`, declare that exact model ID to `dispatch`; otherwise declare the session's own model ID.
- **Do not use** background agents, forks, remote or cloud sessions, or any other agent type for this item.
- **Engine:** `rivet` is on the Bash tool's PATH while the plugin is enabled.
