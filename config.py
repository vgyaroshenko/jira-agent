import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
JIRA_URL = os.getenv("JIRA_URL", "")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_TOKEN = os.getenv("JIRA_API_TOKEN", "")
# Используется только как fallback при создании бага без --related
JIRA_DEFAULT_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "DEV")
MODEL = "claude-sonnet-4-6"
