---
name: script-execution
description: Lists and executes project scripts through script_execution_tool.
---

Use this skill to run existing project scripts via the tools exposed by script_execution_tool.

Follow these rules:

1. If the user provides script source code directly, call `execute_inline_script` with that string and required arguments.
2. If the user provides a specific script path, call `execute_project_script` directly with that path and required arguments.
3. If no script path is provided, call `list_project_scripts` first to discover available scripts.
4. If there is a relevant script, call `execute_project_script` with the best matching script path and required arguments.
5. Return the concrete script output clearly.
6. Do not invent script paths or outputs.
7. If no matching script exists, state that no relevant script was found.
