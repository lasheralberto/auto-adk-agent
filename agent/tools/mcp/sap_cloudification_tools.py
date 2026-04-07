from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

MCP_URL = "https://sap-released-objects-mcp-server-production.up.railway.app/mcp"

def build_cloudification_agent(model, skill) -> LlmAgent:
    """Construye el agente de cloudification cargando sus tools desde el MCP remoto (Streamable HTTP)."""
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=MCP_URL)
    )
    return LlmAgent(
        name="cloudification_agent",
        model=model,
        instruction=skill.instructions,
        tools=[toolset],
    )

