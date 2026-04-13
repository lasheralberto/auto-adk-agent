import os
from pathlib import Path

from dotenv import load_dotenv
from strava_agent_sdk import StravaAgentClient
from strava_agent_sdk.flask import FlaskAdapter

_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=_BASE_DIR / ".env", override=False)

sdk_client = StravaAgentClient()
flask_adapter = FlaskAdapter(__name__, sdk_client=sdk_client)
app = flask_adapter.create_app()

flask_adapter.register_routes()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true"}
    app.run(host="0.0.0.0", port=port, debug=debug)
