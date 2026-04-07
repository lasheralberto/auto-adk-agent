---
name: script-generator
description: Generates Python scripts on demand only when no existing project script can satisfy the user request.
---

Use this skill to solve tasks by generating scripts on the fly only as a fallback.

Rules:

1. Always call `list_project_scripts` first.
2. If an existing script can satisfy the request, do not generate new code and ask `script_executor` to run that script.
3. Only when no relevant script exists, call `generate_script_with_genai` from the tools layer with the user question.
4. Use the output of `generate_script_with_genai` as the generated Python source code string.
5. Execute that generated source on the fly with `execute_inline_script`.
6. If `generate_script_with_genai` returns a validation error or indicates missing credentials/resources, do not fake execution. Return a clear request to the user for the missing resource.
7. Return: generated code and execution output (or missing-resource request).
8. Never write generated scripts to project files.
9. Never claim execution happened without calling `execute_inline_script` or `script_executor`.
10. Never ask the user for permission to generate/execute scripts when tools are available.
11. Ask questions only when an external resource/credential is required and missing.
