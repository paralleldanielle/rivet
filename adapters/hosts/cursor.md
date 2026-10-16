### Cursor (`--adapter cursor`)
- **Launch:** the Task tool with `subagent_type: "rivet-worker"`, in the foreground, one at a time. Wait for its result.
- **Model:** declare the model you request. Cursor may run the subagent on another model; the engine records the declaration, not what actually ran.
- **Do not use** Background Agents, cloud agents, or parallel subagents for this item.
