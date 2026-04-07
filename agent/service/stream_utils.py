import json
import asyncio
import queue
import threading
from typing import Generator

HEARTBEAT_INTERVAL_SECONDS = 5

def _sse(data: dict[str, object], event: str = "chunk") -> str:
    """Helper to format SSE events."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

def _stream_generator(question: str, agent: object, run_agent_streaming_fn) -> Generator[str, None, None]:
    """Bridges the async run_agent_streaming generator into a sync Flask generator via queue."""
    q: queue.Queue = queue.Queue()
    _DONE = object()
    _ERROR = object()

    async def _consume() -> None:
        try:
            async for chunk in run_agent_streaming_fn(question, agent):
                q.put(chunk)
        except Exception as exc:  # noqa: BLE001
            q.put((_ERROR, str(exc)))
        finally:
            q.put(_DONE)

    threading.Thread(target=lambda: asyncio.run(_consume()), daemon=True).start()

    # Emit periodic heartbeat events so proxies/clients keep the stream open on long-running tasks.
    while True:
        try:
            item = q.get(timeout=HEARTBEAT_INTERVAL_SECONDS)
        except queue.Empty:
            yield _sse({"response": "", "tool_calls": []}, event="heartbeat")
            continue
        if item is _DONE:
            yield _sse({"response": "", "tool_calls": []}, event="done")
            break
        if isinstance(item, tuple) and item[0] is _ERROR:
            yield _sse({"response": str(item[1]), "tool_calls": []}, event="error")
            break

        if isinstance(item, dict):
            event_name = str(item.get("_event", "chunk")).strip() or "chunk"
            payload = {key: value for key, value in item.items() if key != "_event"}
            yield _sse(payload, event=event_name)
            continue

        yield _sse(item)

def _rag_stream_generator(rag_filenames, augmented_question, orchestrator, run_agent_streaming_fn):
    """
    Generador que incluye el evento de RAG y luego el streaming original del orquestador.
    """
    if rag_filenames:
        # Evento inicial para el componente UI
        yield _sse({"response": "", "tool_calls": [{"tool": "File Context", "agent": ", ".join(rag_filenames)}]}, event="chunk")

    # Streaming original del orquestador
    for chunk in _stream_generator(augmented_question, orchestrator, run_agent_streaming_fn):
        yield chunk
