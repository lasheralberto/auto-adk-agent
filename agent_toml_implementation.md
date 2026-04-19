Para montar un panel de agentes que sea editable y visualmente intuitivo, debemos mapear los primitivos de ADK (BaseAgent, WorkflowAgents, etc.) a una interfaz de nodos y conexiones.1. Relación Técnica: El Panel vs. El CódigoLa clave es tratar a cada agente como un Objeto de Configuración que el panel puede serializar (generalmente en JSON).Agentes LLM (Nodos de Acción): Se representan como nodos que contienen instructions, model y output_key.Sequential Agents (Contenedores de Flujo): En el panel, no son solo un nodo, sino un área o un grupo. Visualmente, dibujan una línea forzada entre sus sub-agentes. Su relación con los LLM es de padre-hijo: el SequentialAgent posee una lista sub_agents donde el orden en el panel define el orden de ejecución.Parallel/Loop Agents (Nodos Lógicos): Actúan como "splitters" (divisores) o "gates" (puertas). El ParallelAgent dispara múltiples hilos desde un mismo origen de datos.2. Esquema de Funcionamiento del PanelPara que sea editable, el panel debe manejar tres capas:CapaResponsabilidadRelación con ADKLienzo (Canvas)Arrastrar y soltar agentes.Define la jerarquía sub_agents.Inspector (Sidebar)Editar instructions o tools.Modifica los atributos de la instancia LlmAgent.Mapping de EstadoConectar la salida de un agente a la entrada de otro.Define el output_key y las variables {variable} en el prompt.3. Simulación del Panel de Control (Esquema Visual)Este esquema representa cómo se vería la interfaz de usuario para gestionar estos agentes dinámicamente.Plaintext__________________________________________________________________________________
| [ + Añadir Agente ] [ 💾 Guardar Workflow ] [ ▶️ Ejecutar ]                      |
|________________________________________________________________________________|
|                                                                                |
|  ZONA DE FLUJO (WORKFLOW CANVAS)                                               |
|                                                                                |
|  +--------------------------------------------------------------------------+  |
|  |  SEQUENTIAL_WORKFLOW: "Procesador de Leads"                              |  |
|  |  (Agrupador Principal)                                                   |  |
|  |                                                                          |  |
|  |  [ Agente LLM: Clasificador ] ----> [ Agente LLM: Redactor ]             |  |
|  |  (output_key: "tipo_lead")          (lee: {tipo_lead})                   |  |
|  |                                                                          |  |
|  |            |                                                             |  |
|  |            v                                                             |  |
|  |  +--------------------------------------------------------------------+  |  |
|  |  |  PARALLEL_AGENT: "Notificaciones Multicanal"                       |  |  |
|  |  |  (Se ejecuta en paralelo tras el redactor)                         |  |  |
|  |  |                                                                    |  |  |
|  |  |  [ Agente: EmailBot ]           [ Agente: SlackBot ]               |  |  |
|  |  +--------------------------------------------------------------------+  |  |
|  +--------------------------------------------------------------------------+  |
|                                                                                |
|________________________________________________________________________________|
|  INSPECTOR DE AGENTE (Seleccionado: Clasificador)                              |
|--------------------------------------------------------------------------------|
|  Nombre: Clasificador | Modelo: gemini-1.5-flash | Output Key: tipo_lead       |
|  Instrucciones: "Analiza el texto y decide si es 'Ventas' o 'Soporte'..."      |
|  Herramientas: [ Search_Tool ] [ Transfer_to_Agent (Redactor) ]                |
|________________________________________________________________________________|
4. Cómo se relacionan entre sí en el WorkflowPara que el panel sea funcional, la relación entre agentes debe configurarse de dos formas:Relación por Jerarquía (Estructural):Al mover un agente dentro de un recuadro de "Sequential" o "Parallel" en el panel, el sistema añade ese agente al array sub_agents del padre.Ejemplo: pipeline = SequentialAgent(sub_agents=[agente_clasificador, agente_redactor]).Relación por Contexto (Datos):En el panel, creas una "flecha" de datos. Técnicamente, esto solo significa que el Agente B usa una llave en su instruction (ej. {resultado_A}) que el Agente A guardó previamente usando su output_key.Relación por Delegación (Dinámica):Si en el panel habilitas una conexión de "Transferencia", el LlmAgent padre recibirá automáticamente la herramienta transfer_to_agent. El panel debe asegurar que el agente destino tenga una description clara para que el LLM sepa cuándo saltar a él.5. Implementación de "Quitar/Poner"Para que sea editable en tiempo real:Añadir: Creas una nueva instancia de LlmAgent y la registras en el diccionario global de agentes del panel.Conectar: Actualizas la lista sub_agents del agente que actúa como orquestador (Sequential/Parallel).Quitar: Eliminas el agente de la lista sub_agents de su padre y destruyes la instancia. ADK lanzará un ValueError si intentas dejar a un hijo huérfano en una estructura mal formada, por lo que el panel debe validar la jerarquía antes de guardar.

1. Estructura del Archivo agents_workflow.tomlDividiremos el archivo en Definiciones de Agentes (quiénes son) y Estructura del Workflow (cómo se relacionan).Ini, TOML# ---------------------------------------------------------
# DEFINICIÓN DE AGENTES LLM (Nodos de Acción)
# ---------------------------------------------------------

[agents.clasificador]
type = "LlmAgent"
model = "gemini-1.5-flash"
output_key = "categoria_ticket"
instructions = """
Analiza el mensaje del usuario y clasifícalo en: 'ventas', 'soporte' o 'tecnico'.
"""

[agents.redactor_ventas]
type = "LlmAgent"
model = "gemini-1.5-pro"
output_key = "respuesta_final"
instructions = "Genera una propuesta comercial basada en el interés: {categoria_ticket}."

[agents.notificador_slack]
type = "CustomAgent"
name = "SlackNotifier"
# Este agente podría ser una clase Python personalizada referenciada

# ---------------------------------------------------------
# ORQUESTACIÓN (Relación Sequential y Parallel)
# ---------------------------------------------------------

[workflow.proceso_principal]
type = "SequentialAgent"
sub_agents = ["clasificador", "flujo_paralelo"]

[workflow.flujo_paralelo]
type = "ParallelAgent"
sub_agents = ["redactor_ventas", "notificador_slack"]
2. Cómo se relacionan en el Workflow (Lógica Detrás)Para que el panel sea editable y funcional, la relación se establece mediante tres reglas de mapeo en el TOML:Relación Secuencial (Paso a Paso):En el TOML, la lista sub_agents = ["agente_a", "agente_b"] dentro de un SequentialAgent le dice al ADK que el contexto de salida del Agente A (su output_key) estará disponible automáticamente para el Agente B. El panel visual simplemente reordena los elementos de esta lista.Relación por Referencia (Variables):Si te fijas en redactor_ventas, sus instrucciones incluyen {categoria_ticket}. El panel debe detectar que categoria_ticket es un output_key de otro agente y dibujar una "línea de datos" punteada entre ellos.Relación Jerárquica (Anidación):Puedes meter un ParallelAgent dentro de un SequentialAgent. En el TOML, esto se hace tratando al workflow como un agente más. Esto permite que el panel tenga "grupos" que puedes colapsar o expandir.3. Simulación del Panel de Edición TOMLSi el usuario añade o quita agentes en la interfaz, el archivo se actualizaría dinámicamente:Acción en el PanelCambio en el TOMLAñadir AgenteSe crea una nueva entrada [agents.nombre_nuevo].Arrastrar a un flujoEl nombre se añade al array sub_agents del workflow destino.Cambiar ordenSe reordena la posición del string en el array sub_agents.Borrar conexiónSe elimina el nombre del array sub_agents, pero el agente puede seguir existiendo en la sección [agents].