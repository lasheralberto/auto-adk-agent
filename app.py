import asyncio
import os
import tempfile
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS
from werkzeug.utils import secure_filename
from openai import OpenAI

from agent.app import build_orchestrator
from agent.runner import run_agent, run_agent_streaming
from agent.tools.vectors import vector_store
from agent.service.stream_utils import _rag_stream_generator

app = Flask(__name__)
CORS(app)


@app.get("/health")
def health() -> tuple[dict, int]:
    return {"status": "ok"}, 200

# ================================
# VECTOR STORES MANAGEMENT
# ================================

@app.get("/vector_stores")
def list_vector_stores():
    """List available vector stores in the OpenAI account."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY is not configured in environment"}), 500

    client = OpenAI(api_key=api_key)

    def _as_plain_dict(value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "to_dict"):
            return value.to_dict()
        try:
            return dict(value)
        except Exception:
            return {
                "in_progress": getattr(value, "in_progress", None),
                "completed": getattr(value, "completed", None),
                "failed": getattr(value, "failed", None),
                "cancelled": getattr(value, "cancelled", None),
                "total": getattr(value, "total", None),
            }

    try:
        vector_stores = client.vector_stores.list()
        data_list = []
        for vs in vector_stores.data:
            data_list.append({
                "id": vs.id,
                "name": getattr(vs, "name", "Unnamed Vector Store"),
                "status": getattr(vs, "status", "unknown"),
                "created_at": getattr(vs, "created_at", None),
                "file_counts": _as_plain_dict(getattr(vs, "file_counts", {}))
            })
        return jsonify({"data": data_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/vector_stores")
def create_vector_store():
    """Create a new vector store."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "New Vector Store")
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    try:
        vs = client.vector_stores.create(name=name)
        return jsonify({
            "id": vs.id,
            "name": vs.name,
            "status": vs.status,
            "created_at": vs.created_at
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.delete("/vector_stores")
def delete_vector_store():
    """Delete a vector store."""
    vs_id = request.args.get("vector_store_id")
    if not vs_id:
        return jsonify({"error": "Missing vector_store_id parameter"}), 400

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    try:
        deleted_vs = client.vector_stores.delete(vector_store_id=vs_id)
        return jsonify({
            "id": deleted_vs.id,
            "deleted": deleted_vs.deleted
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================================
# END VECTOR STORES MANAGEMENT
# ================================

@app.post("/add_to_vs")
def add_to_vs():
    """Accepts multipart/form-data with a file field named 'file'. Optional form field
    'vector_store_id' can override the env VAR VECTOR_STORE_ID. Returns JSON status.
    """
    if "file" not in request.files:
        return {"error": "No file part in request (expected field 'file')."}, 400

    f = request.files.get("file")
    if f.filename == "":
        return {"error": "Empty filename."}, 400

    vs_id = request.form.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")

    # Save to a temporary file
    tmp = None
    try:
        filename = secure_filename(f.filename)
        suffix = os.path.splitext(filename)[1]
        print(f"Saving uploaded file to temp file with suffix {suffix}")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        f.save(tmp.name)
        tmp.close()

        result = vector_store.attach(tmp.name, vector_store_id=vs_id)
        return jsonify({"status": "ok", "file": filename, "vector_store_id": vs_id, "result": str(result)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            if tmp is not None:
                os.unlink(tmp.name)
        except Exception:
            pass


def _parse_chat_request(data: dict) -> tuple[str | None, str | None, str | None, bool, str | None]:
    question = data.get("message") or data.get("question")
    model = data.get("model")
    llm_param = (data.get("llm_provider") or data.get("llm") or os.environ.get("LLM_PROVIDER") or os.environ.get("LLM") or "")
    stream_param = data.get("stream", False)

    if isinstance(stream_param, str):
        stream = stream_param.lower() in ("true", "1", "yes")
    else:
        stream = bool(stream_param)

    if isinstance(llm_param, str) and "/" in llm_param:
        parsed_provider, model_from_llm = llm_param.split("/", 1)
        llm_provider = parsed_provider
        if model_from_llm and not model:
            model = model_from_llm.strip()
    else:
        llm_provider = llm_param

    model_name = model.strip() if isinstance(model, str) and model.strip() else None
    vector_store_id = data.get("vector_store_id")
    return question, llm_provider, model_name, stream, vector_store_id


@app.post("/chat")
@app.post("/ask")
def chat_agent() -> Response | tuple[dict, int]:
    data = request.get_json(silent=True) or {}
    question, llm_provider, model_name_to_use, stream, vs_id = _parse_chat_request(data)

    print(
        "Received chat request with question: "
        f"{question}, llm_provider: {llm_provider}, model: {model_name_to_use}, stream: {stream}"
    )

    if not isinstance(question, str) or not question.strip():
        return {"error": "Field 'message' or 'question' must be a non-empty string."}, 400

    if not isinstance(llm_provider, str) or not llm_provider.strip():
        return {"error": "Field 'llm_provider' must be a non-empty string (e.g. 'openai/gpt-4o')."}, 400

    try:
        rag_info = vector_store.search_vs(question.strip(), vector_store_id=vs_id)
        rag_context = rag_info.get("context", "")
        rag_filenames = rag_info.get("filenames", [])
        
        augmented_question = question.strip()
        
        if rag_context:
            augmented_question = (
                f"Contexto del Vector Store con información relevante para responder:\n"
                f"### COMIENZO DEL CONTEXTO ###\n"
                f"{rag_context}\n"
                f"### FIN DEL CONTEXTO ###\n\n"
                f"Pregunta del usuario: {question.strip()}\n"
                f"Responde basándote en el contexto anterior. Si no está la información en el contexto o en tu base de conocimientos general sobre Strava y entrenamiento, indícalo educadamente."
            )

        orchestrator = build_orchestrator(
            llm_provider=llm_provider.strip().lower(),
            model_name=model_name_to_use,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if stream:
        return Response(
            stream_with_context(_rag_stream_generator(rag_filenames, augmented_question, orchestrator, run_agent_streaming)),
            content_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
            },
        )

    result = asyncio.run(run_agent(augmented_question, orchestrator))
    if not isinstance(result, dict):
        result = {"response": str(result)}

    if rag_filenames and isinstance(result, dict):
        result["rag_files"] = rag_filenames

    return jsonify(result)


@app.get("/search_vs")
def search_vs_endpoint(query: str = "all files"):
    """
    Endpoint para listar y buscar archivos en el Vector Store.
    Retorna la estructura de datos compatible con el popover 'Files Context'.
    """
   
    # Realiza la búsqueda para obtener los resultados reales (score, file_id, etc)
    # Por defecto, la función search_vs retorna context y filenames.
    # Necesitamos modificarla o llamar directamente a la lógica de OpenAI para más detalle si se requiere.
    # De momento retornamos los metadatos de búsqueda.
    
    # En un caso real, esto llamaría a vector_stores.search y retornaría el objeto 'data' crudo
    # pero siguiendo el flujo actual del backend:
 
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    vs_id = request.args.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")
    
    try:
        # 1. Listamos todos los archivos asociados al Vector Store
        vs_files = client.vector_stores.files.list(
            vector_store_id=vs_id
        )
        
        data_list = []
        # 2. Para cada archivo listado, recuperamos sus detalles específicos (como el filename que no viene en list)
        for vs_file in vs_files.data:
            file_details = client.files.retrieve(vs_file.id)
            data_list.append({
                "file_id": vs_file.id,
                "filename": getattr(file_details, "filename", getattr(file_details, "name", None)),
                "status": vs_file.status,
                "created_at": vs_file.created_at,
                "usage_bytes": vs_file.usage_bytes
            })
        return jsonify({"data": data_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/vectorize")
async def vectorize_text():
    """
    Endpoint asíncrono para vectorizar un texto usando LangExtract y un provider de vectores.
    Cuerpo esperado (JSON):
    {
      "text": "Texto a procesar",
      "provider": "pinecone" (opcional, default pinecone),
      "model_id": "gpt-4o-mini" (opcional),
      "api_key": "sk-..." (opcional)
    }
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    provider = data.get("provider", "pinecone")
    model_id = data.get("model_id", "gpt-4o-mini")
    api_key = data.get("api_key")

    if not text or not isinstance(text, str):
        return jsonify({"error": "Field 'text' is required and must be a string."}), 400

    try:
        # Ejecutamos la extracción y vectorización en un hilo aparte para no bloquear el loop asíncrono
        # si la librería langextract o el provider no son nativamente asíncronos.
        loop = asyncio.get_event_loop()
        extraction = await loop.run_in_executor(
            None, 
            vector_store.extract_and_vectorize, 
            text, 
            provider, 
            model_id, 
            api_key
        )
        
        return jsonify({
            "status": "ok",
            "provider": provider,
            "extraction": extraction
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/get_vs_file_details")
def get_vs_file_details():
    """
    Endpoint para obtener los detalles de un archivo específico del Vector Store
    utilizando client.vector_stores.files.retrieve.
    """
    file_id = request.args.get("file_id")
    if not file_id:
        return {"error": "Missing file_id parameter"}, 400

    from openai import OpenAI
    import os
    
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    vs_id = request.args.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")
    
    try:
        # Recupera los detalles específicos del archivo
        file_details = client.vector_stores.files.retrieve(
            vector_store_id=vs_id,
            file_id=file_id
        )
        
        # Retornamos los detalles serializables
        return {
            "id": getattr(file_details, "id", file_id),
            "object": getattr(file_details, "object", "vector_store.file"),
            "created_at": getattr(file_details, "created_at", None),
            "vector_store_id": getattr(file_details, "vector_store_id", vs_id),
            "status": getattr(file_details, "status", "unknown"),
            "last_error": getattr(file_details, "last_error", None),
            # Algunos campos pueden estar anidados o ser objetos de la SDK
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/get_vs_file_content")
def get_vs_file_content():
    """
    Endpoint para descargar el contenido de un archivo del Vector Store.
    Usa client.vector_stores.files.content para obtener el texto.
    """
    file_id = request.args.get("file_id")
    if not file_id:
        return {"error": "Missing file_id parameter"}, 400

    from openai import OpenAI
    import os
    
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    vs_id = request.args.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")
    
    try:
        # Recupera el contenido del archivo procesado en el Vector Store
        # Note: .content returns a generator/iterator of pages
        content_response = client.vector_stores.files.content(
            vector_store_id=vs_id,
            file_id=file_id
        )
        
        full_text = ""
        # Iteramos sobre las páginas de contenido (vector_store.file.content)
        for page in content_response:
            if hasattr(page, "text") and page.text:
                full_text += page.text
            elif isinstance(page, dict) and "text" in page:
                 full_text += page["text"]
        
        return {
            "file_id": file_id,
            "content": full_text
        }
    except Exception as e:
        return {"error": str(e)}, 500

@app.delete("/delete_vs_file")
def delete_vs_file():
    """
    Endpoint para eliminar un archivo del Vector Store.
    """
    file_id = request.args.get("file_id")
    if not file_id:
        return {"error": "Missing file_id parameter"}, 400

    from openai import OpenAI
    import os
    
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    vs_id = request.args.get("vector_store_id") or os.environ.get("VECTOR_STORE_ID")
    
    try:
        deleted_file = client.vector_stores.files.delete(
            vector_store_id=vs_id,
            file_id=file_id
        )
        return jsonify({
            "status": "deleted",
            "file_id": file_id,
            "details": str(deleted_file)
        })
    except Exception as e:
        return {"error": str(e)}, 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "").strip() in {"1", "true", "True"}
    app.run(host="0.0.0.0", port=port, debug=debug)