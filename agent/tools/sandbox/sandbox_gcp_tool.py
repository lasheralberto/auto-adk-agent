import os
import json

import vertexai
from dotenv import load_dotenv
from vertexai._genai import types

load_dotenv()

# Use getenv to avoid raising KeyError at import time when env vars are missing.
SANDBOX_DISPLAY_NAME = os.environ.get("SANDBOX_DISPLAY_NAME", "")
AGENT_ENGINE_DISPLAY_NAME = os.environ.get("AGENT_ENGINE_DISPLAY_NAME", "")
PROJECT_ID = os.environ.get("PROJECT_ID", "")
LOCATION = os.environ.get("LOCATION", "")
CLIENT = None
_CACHED_AGENT_ENGINE_NAME = None
_CACHED_SANDBOX_NAME = None
_SANDBOX_MAX_OUTPUT_CHARS = 20000
_SANDBOX_MAX_EXEC_SECONDS = 45


def _build_safe_wrapper_code(user_code: str) -> str:
    """Wrap user code to cap stdout/stderr and guarantee a compact JSON result."""
    return f"""
import json
import signal
import sys
import time
import traceback

MAX_CHARS = {_SANDBOX_MAX_OUTPUT_CHARS}
MAX_SECONDS = {_SANDBOX_MAX_EXEC_SECONDS}
USER_CODE = {user_code!r}


class _LimitedBuffer:
    def __init__(self, max_chars):
        self.max_chars = max_chars
        self.chunks = []
        self.current = 0
        self.truncated = False

    def write(self, s):
        if s is None:
            return 0
        s = str(s)
        if self.current >= self.max_chars:
            self.truncated = True
            return len(s)
        remaining = self.max_chars - self.current
        if len(s) > remaining:
            self.chunks.append(s[:remaining])
            self.current += remaining
            self.truncated = True
        else:
            self.chunks.append(s)
            self.current += len(s)
        return len(s)

    def flush(self):
        return None

    def getvalue(self):
        return ''.join(self.chunks)


timed_out = False
status = 'ok'
trace = ''
start = time.time()


def _timeout_handler(signum, frame):
    raise TimeoutError(f'Execution timed out after {{MAX_SECONDS}} seconds')


buf_out = _LimitedBuffer(MAX_CHARS)
buf_err = _LimitedBuffer(MAX_CHARS)
prev_out, prev_err = sys.stdout, sys.stderr

try:
    if hasattr(signal, 'SIGALRM'):
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(MAX_SECONDS)

    # Avoid pathological int-to-string conversions from blocking responses.
    if hasattr(sys, 'set_int_max_str_digits'):
        try:
            sys.set_int_max_str_digits(20000)
        except Exception:
            pass

    sys.stdout = buf_out
    sys.stderr = buf_err
    namespace = {{'__name__': '__main__', '__file__': 'sandbox_exec.py'}}
    exec(compile(USER_CODE, 'sandbox_exec.py', 'exec'), namespace)
except TimeoutError:
    status = 'timeout'
    timed_out = True
    trace = traceback.format_exc()
except Exception:
    status = 'error'
    trace = traceback.format_exc()
finally:
    sys.stdout = prev_out
    sys.stderr = prev_err
    if hasattr(signal, 'SIGALRM'):
        try:
            signal.alarm(0)
        except Exception:
            pass

payload = {{
    'status': status,
    'stdout': buf_out.getvalue(),
    'stderr': buf_err.getvalue(),
    'truncated': bool(buf_out.truncated or buf_err.truncated),
    'timed_out': timed_out,
    'elapsed_seconds': round(time.time() - start, 3),
}}
if trace:
    payload['traceback'] = trace[-8000:]

print(json.dumps(payload, ensure_ascii=False))
"""


def _extract_json_payload(raw_text: str) -> dict | None:
    """Extract a JSON object line from sandbox output, if present."""
    if not raw_text:
        return None

    text = raw_text.strip()
    if not text:
        return None

    def _normalize_payload(parsed: dict) -> dict | None:
        # Wrapped payload already contains status/stdout/stderr.
        if any(k in parsed for k in ("status", "stdout", "stderr", "traceback", "timed_out", "truncated")):
            return parsed

        # Vertex sandbox envelopes often place wrapped payload as JSON string in msg_out.
        msg_out = parsed.get("msg_out")
        if isinstance(msg_out, str) and msg_out.strip():
            try:
                nested = json.loads(msg_out.strip())
                if isinstance(nested, dict):
                    return nested
            except Exception:
                pass

        # Fallback: if msg_out is plain text, expose it as stdout.
        if isinstance(msg_out, str) and msg_out.strip():
            return {
                "status": "ok" if int(parsed.get("exit_status_int", 0) or 0) == 0 else "error",
                "stdout": msg_out,
                "stderr": str(parsed.get("msg_err") or ""),
            }

        return parsed

    # Fast path: full response is JSON.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            normalized = _normalize_payload(parsed)
            if isinstance(normalized, dict):
                return normalized
    except Exception:
        pass

    # Fallback: search last JSON-looking line.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                normalized = _normalize_payload(parsed)
                if isinstance(normalized, dict):
                    return normalized
        except Exception:
            continue

    return None


def _format_wrapped_payload(payload: dict) -> str:
    """Format wrapped execution payload into user-facing text."""
    status = str(payload.get("status") or "ok")
    stdout = str(payload.get("stdout") or "").strip()
    stderr = str(payload.get("stderr") or "").strip()
    trace = str(payload.get("traceback") or "").strip()
    truncated = bool(payload.get("truncated"))
    elapsed = payload.get("elapsed_seconds")

    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append("[stderr]\n" + stderr)
    if status in ("error", "timeout") and trace:
        parts.append("[traceback]\n" + trace)
    if truncated:
        parts.append(f"[sandbox-note] Output truncated to {_SANDBOX_MAX_OUTPUT_CHARS} chars")
    if elapsed is not None:
        parts.append(f"[sandbox-note] elapsed={elapsed}s status={status}")

    if parts:
        return "\n\n".join(parts)
    return "[sandbox] Execution finished with no output."


def init_client():
    """Initializes the Vertex AI client."""
    global CLIENT
    if CLIENT is None:
        CLIENT = vertexai.Client(project=PROJECT_ID, location=LOCATION)


def list_or_create_agent_engine() -> str:
    """Returns the agent engine resource name, creating one if it doesn't exist."""
    global _CACHED_AGENT_ENGINE_NAME

    if _CACHED_AGENT_ENGINE_NAME:
        return _CACHED_AGENT_ENGINE_NAME

    agent_engines = CLIENT.agent_engines.list()

    for engine in agent_engines:
        engine_display_name = getattr(getattr(engine, "api_resource", None), "display_name", None)
        engine_name = getattr(getattr(engine, "api_resource", None), "name", None)
        print(f"Checking agent engine: {engine_display_name or engine_name}")
        if engine_display_name == AGENT_ENGINE_DISPLAY_NAME and engine_name:
            print(f"Agent engine found: {engine_name}")
            _CACHED_AGENT_ENGINE_NAME = engine_name
            return engine_name

    print("Agent engine not found. Creating a new agent engine...")
    agent_engine = CLIENT.agent_engines.create(config={"display_name": AGENT_ENGINE_DISPLAY_NAME})
    agent_engine_name = agent_engine.api_resource.name
    print("Created agent engine:", agent_engine_name)
    _CACHED_AGENT_ENGINE_NAME = agent_engine_name
    return agent_engine_name


def create_sandbox(agent_engine_name: str) -> str:
    """Creates a new sandbox under the given agent engine and returns its name."""
    operation = CLIENT.agent_engines.sandboxes.create(
        name=agent_engine_name,
        spec={
            "code_execution_environment": {
                "code_language": "LANGUAGE_PYTHON",
                "machine_config": "MACHINE_CONFIG_VCPU4_RAM4GIB",
            }
        },
        config=types.CreateAgentEngineSandboxConfig(
            display_name=SANDBOX_DISPLAY_NAME,
            ttl="3600s",
        ),
    )

    operation_name = getattr(operation, "name", None)
    if operation_name:
        print("Sandbox operation:", operation_name)

    if operation.response and getattr(operation.response, "name", None):
        return operation.response.name

    raise ValueError("Sandbox creation completed without a response name.")


def list_or_create_sandbox(agent_engine_name: str) -> str:
    """Returns the sandbox name, creating one if it doesn't exist."""
    global _CACHED_SANDBOX_NAME

    if _CACHED_SANDBOX_NAME:
        return _CACHED_SANDBOX_NAME

    sandboxes = CLIENT.agent_engines.sandboxes.list(name=agent_engine_name)

    for sandbox in sandboxes:
        print(f"Checking sandbox: {sandbox.display_name}")
        if sandbox.display_name == SANDBOX_DISPLAY_NAME:
            print(f"Sandbox found: {sandbox.name}")
            _CACHED_SANDBOX_NAME = sandbox.name
            return sandbox.name

    print("Sandbox not found. Creating a new sandbox...")
    _CACHED_SANDBOX_NAME = create_sandbox(agent_engine_name)
    return _CACHED_SANDBOX_NAME


def run_in_sandbox_gcp(code: str) -> str:
    """Execute Python code in a GCP Vertex AI sandbox and return the stdout output."""
    global _CACHED_SANDBOX_NAME

    init_client()
    agent_engine_name = list_or_create_agent_engine()
    sandbox_name = list_or_create_sandbox(agent_engine_name)

    wrapped_code = _build_safe_wrapper_code(code)

    try:
        print(f"Executing code in sandbox: {code}")
        response = CLIENT.agent_engines.sandboxes.execute_code(
            name=sandbox_name,
            input_data={"code": wrapped_code},
        )
    except Exception as exc:
        # Sandbox resources can expire; clear only sandbox cache and retry once.
        error_text = str(exc).lower()
        if any(token in error_text for token in ("not found", "404", "expired")):
            _CACHED_SANDBOX_NAME = None
            sandbox_name = list_or_create_sandbox(agent_engine_name)
            response = CLIENT.agent_engines.sandboxes.execute_code(
                name=sandbox_name,
                input_data={"code": wrapped_code},
            )
        else:
            raise

    output_parts = []

    # Primary stream output (stdout-like chunks)
    for chunk in (getattr(response, "outputs", None) or []):
        print(f"Received code execution chunk: {chunk}")
        data = getattr(chunk, "data", None)
        if data:
            output_parts.append(data.decode("utf-8", errors="replace"))

    # Some SDK versions may expose additional result channels.
    for attr_name in ("result", "output", "stdout", "stderr", "message", "text"):
        value = getattr(response, attr_name, None)
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value:
            output_parts.append(text_value)

    # Keep unique chunks while preserving order.
    seen = set()
    unique_parts = []
    for item in output_parts:
        if item in seen:
            continue
        seen.add(item)
        unique_parts.append(item)

    if unique_parts:
        combined = "\n".join(unique_parts)
        payload = _extract_json_payload(combined)
        if payload is not None:
            return _format_wrapped_payload(payload)
        return combined

    return "[sandbox] Execution finished with no output."


# if __name__ == "__main__":
#     print(run_in_sandbox("print('Hello from the sandbox!')"))