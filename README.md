![Banner](.github/images/banner.png)

This is a comprehensive `README.md` designed for the **Auto-ADK Agent** repository, reflecting its modular architecture and advanced capabilities.

---

# 🤖 Auto-ADK Agent: Memory + Code-Generation

![Banner](.github/images/banner.png)

[![Python Version](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GCP Enabled](https://img.shields.io/badge/GCP-CloudBuild-google.svg)](https://cloud.google.com/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-blue.svg)](https://www.docker.com/)

**Auto-ADK Agent** is a state-of-the-art multi-agent framework designed to bridge the gap between Large Language Models (LLMs) and real-world data silos. By integrating a modular **Skill System**, **Recursive Planning (ReAct)**, and **Domain-Specific SDKs**, it enables autonomous research and data analysis—specifically optimized for fitness (Strava) and knowledge retrieval (Wikipedia).

---

## 🚀 Key Features

*   **Modular Skill Architecture**: Capabilities are isolated into "Skills" (e.g., `intent-router`, `strava-ingestion`) allowing for easy extension.
*   **Intelligent Planning**: Implements a `plan-react-planner` to decompose complex user prompts into executable steps.
*   **Fitness Intelligence**: Native integration with the Strava API via a custom `strava_agent_sdk` for activity analysis and ingestion.
*   **Autonomous Research**: A dedicated Wiki-Research agent using vector indexing and retrieval-augmented generation (RAG).
*   **Memory-Augmented Generation**: Maintains context across multi-turn conversations with a dedicated storage backend.
*   **Cloud Native**: Built-in support for Google Cloud Secret Manager, Cloud Build, and Docker-ready environments.

---

## 📂 Repository Structure

```text
├── agent/
│   ├── agents/          # Specialized agents (Wiki Research Chat, etc.)
│   ├── skills/          # Discrete capabilities (Intent Routing, Planning, Ingestion)
│   ├── tools/           # Pipeline tools (Wiki Vector Index, Storage, LLM connectors)
│   ├── models/          # Domain models (Strava Reinforcement Learning models)
│   ├── config/          # Environment and Envar management
│   └── service/         # Streaming and utility services
├── strava_agent_sdk/    # Specialized SDK for Strava API interaction
├── app.py               # Main API Entrypoint (FastAPI/Flask)
├── runner.py            # CLI Execution Entrypoint
├── Dockerfile           # Container definition
└── cloudbuild.yaml      # CI/CD Pipeline
```

---

## 🛠️ Installation

### Prerequisites
- Python 3.13+
- Docker (optional, for containerized deployment)
- Strava API Credentials (for fitness features)

### Setup
1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-repo/auto-adk-agent.git
   cd auto-adk-agent
   ```

2. **Set up a virtual environment:**
   ```bash
   python3.13 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment Variables:**
   Create a `.env` file in the root directory:
   ```env
   OPENAI_API_KEY=your_key_here
   STRAVA_CLIENT_ID=your_id
   STRAVA_CLIENT_SECRET=your_secret
   STORAGE_BACKEND=local # or gcp
   ```

---

## 💡 Usage

### Running the Agent (CLI)
You can interact with the agent directly through the runner script:

```bash
python agent/runner.py --task "Analyze my last 3 runs on Strava and compare them to the world record for a marathon."
```

### Running the API Service
To start the agent as a web service:

```bash
python app.py
```

### Example: Custom Agent Implementation
The framework allows you to leverage the `strava_agent_sdk` to build custom logic:

```python
from agent.tools.pipeline.connectors.strava import StravaConnector
from strava_agent_sdk import StravaClient

# Initialize the connector
connector = StravaConnector(client_id="...", client_secret="...")

# Fetch activities through the agent pipeline
activities = connector.fetch_recent_activities(limit=5)

for activity in activities:
    print(f"Activity: {activity.name} | Distance: {activity.distance}m")
```

---

## 🧠 Core Skills

| Skill | Description |
| :--- | :--- |
| **Intent Router** | Analyzes user input to determine which agent or tool should handle the request. |
| **Plan-ReAct Planner** | Implements the Reason + Act loop to handle multi-step reasoning tasks. |
| **Wiki Research** | Performs vector-based searches on Wikipedia to provide factual, grounded answers. |
| **Strava Ingestion** | Handles OAuth2 flows and ingests fitness telemetry data for analysis. |

---

## 🚢 Deployment

### Docker
Build and run the container locally:
```bash
docker build -t auto-adk-agent .
docker run --env-file .env -p 8080:8080 auto-adk-agent
```

### Google Cloud Platform
The repository includes a `cloudbuild.yaml` for automated deployment to Cloud Run or GKE:
```bash
gcloud builds submit --config cloudbuild.yaml
```

---

## 📝 Documentation
- [AGENTS.md](AGENTS.md): Detailed documentation on specialized agent personas.
- [CLAUDE.md](CLAUDE.md): Development guidelines and formatting rules.
- [SKILL.md](agent/skills/intent-router/SKILL.md): Individual documentation for each skill module.

---

## 🤝 Contributing
1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📄 License
Distributed under the MIT License. See `LICENSE` for more information.

---
*Built with ❤️ by the Auto-ADK Team.*