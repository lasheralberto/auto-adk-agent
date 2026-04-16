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
│   ├── agents/          # Specialized agent implementations (Wiki, Chat, etc.)
│   ├── config/          # Environment and application configuration
│   ├── models/          # Domain models (e.g., Strava RL models)
│   ├── service/         # Streaming and API utilities
│   ├── skills/          # Atomic capabilities (Intent routing, planning, querying)
│   ├── tools/           # Pipeline tools (Wiki indexers, Strava connectors)
│   └── runner.py        # Core execution engine
├── strava_agent_sdk/    # Dedicated SDK for Strava interactions
├── app.py               # Entry point for the application/API
├── Dockerfile           # Containerization configuration
└── cloudbuild.yaml      # CI/CD pipeline for Google Cloud
```

---

## 🛠️ Getting Started

### Prerequisites
*   Python 3.13+
*   Docker (optional, for containerized deployment)
*   Strava API Credentials (for fitness features)
*   OpenAI or Anthropic API Key (configured via `.env`)

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-repo/auto-adk-agent.git
    cd auto-adk-agent
    ```

2.  **Set up a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure environment variables:**
    Create a `.env` file in the root directory:
    ```env
    STRAVA_CLIENT_ID=your_id
    STRAVA_CLIENT_SECRET=your_secret
    OPENAI_API_KEY=your_key
    STORAGE_BACKEND=local # or gcp
    ```

---

## 📖 Usage Examples

### Running the Agent
You can trigger the agent via the central `runner.py` or the `app.py` interface.

```python
from agent.runner import AgentRunner

# Initialize the runner
runner = AgentRunner()

# Execute a complex query involving research and planning
response = runner.run("Analyze my Strava runs from last week and compare them to the training habits of elite marathoners.")

print(response)
```

### Researching via Wiki Agent
The repository includes a specific pipeline for Wikipedia-based RAG:

```python
from agent.tools.pipeline.research_wiki_agent import WikiResearchAgent

wiki_agent = WikiResearchAgent()
results = wiki_agent.search_and_summarize("Advanced Reinforcement Learning in Fitness Apps")
```

---

## 🧠 Skills System

The core of Auto-ADK is its **Skills** directory. Each skill is a self-contained module with its own logic:

| Skill | Description |
| :--- | :--- |
| **intent-router** | Determines if a user wants to check fitness data, search the web, or chat. |
| **plan-react-planner** | Breaks down a prompt like "How do I improve my pace?" into sub-tasks. |
| **strava-ingestion** | Handles OAuth and data fetching from Strava. |
| **query-agent** | Optimized for precise retrieval from the internal vector index. |

---

## 🐳 Deployment

### Docker
Build and run the agent in a containerized environment:

```bash
docker build -t auto-adk-agent .
docker run --env-file .env -p 8080:8080 auto-adk-agent
```

### Google Cloud Build
The included `cloudbuild.yaml` allows for automated deployment to GCP:

```bash
gcloud builds submit --config cloudbuild.yaml .
```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🤝 Contributing

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---
**Maintained by the ADK Development Team.** _Built for the next generation of autonomous data assistants._