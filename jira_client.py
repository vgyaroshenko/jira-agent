import re
import requests
from requests.auth import HTTPBasicAuth
from config import JIRA_URL, JIRA_EMAIL, JIRA_TOKEN, JIRA_DEFAULT_PROJECT_KEY


class JiraClient:
    def __init__(self):
        self.base_url = JIRA_URL.rstrip("/")
        self.default_project_key = JIRA_DEFAULT_PROJECT_KEY
        self.auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_TOKEN)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}

    def get_issue(self, issue_key: str) -> dict:
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
        response = requests.get(url, auth=self.auth, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def get_issue_text(self, issue_key: str) -> dict:
        issue = self.get_issue(issue_key)
        fields = issue["fields"]

        description = self._extract_text_from_adf(fields.get("description"))

        # Поле AC может отличаться в вашей Jira — уточните ключ поля
        acceptance_criteria = self._extract_text_from_adf(
            fields.get("customfield_10016")
        )

        reporter = fields.get("reporter") or {}

        return {
            "key": issue_key,
            "title": fields.get("summary", ""),
            "description": description,
            "acceptance_criteria": acceptance_criteria,
            "status": fields["status"]["name"],
            "issue_type": fields["issuetype"]["name"],
            "reporter_account_id": reporter.get("accountId"),
            "reporter_name": reporter.get("displayName", ""),
        }

    def get_comments(self, issue_key: str) -> list:
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"
        response = requests.get(url, auth=self.auth, headers=self.headers,
                                params={"maxResults": 100, "orderBy": "created"})
        response.raise_for_status()
        result = []
        for c in response.json().get("comments", []):
            author = (c.get("author") or {}).get("displayName", "Unknown")
            date = (c.get("created") or "")[:10]
            text = self._extract_text_from_adf(c.get("body"))
            result.append({"author": author, "date": date, "text": text})
        return result

    def add_comment(self, issue_key: str, comment_text: str, mention_account_id: str = None) -> None:
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"

        content = []

        if mention_account_id:
            content.append({
                "type": "paragraph",
                "content": [{"type": "mention", "attrs": {"id": mention_account_id}}],
            })

        content.extend(self._text_to_adf_content(comment_text))

        payload = {"body": {"type": "doc", "version": 1, "content": content}}
        response = requests.post(url, json=payload, auth=self.auth, headers=self.headers)
        response.raise_for_status()
        print(f"  ✓ Коментар додано до {issue_key}")

    def _text_to_adf_content(self, text: str) -> list:
        """Convert text (with optional markdown table) to ADF content nodes."""
        nodes = []
        lines = text.split("\n")
        i = 0
        buffer = []

        while i < len(lines):
            if lines[i].strip().startswith("|"):
                if buffer:
                    para = "\n".join(buffer).strip()
                    if para:
                        nodes.append({"type": "paragraph", "content": [{"type": "text", "text": para}]})
                    buffer = []
                table_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i])
                    i += 1
                table_node = self._markdown_table_to_adf(table_lines)
                if table_node:
                    nodes.append(table_node)
            else:
                buffer.append(lines[i])
                i += 1

        if buffer:
            para = "\n".join(buffer).strip()
            if para:
                nodes.append({"type": "paragraph", "content": [{"type": "text", "text": para}]})

        return nodes or [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]

    def _markdown_table_to_adf(self, lines: list) -> dict | None:
        """Parse markdown table lines into an ADF table node.

        Cells in the Steps column may use literal \\n as step separator.
        """
        data_lines = [l for l in lines if not re.match(r"^\|[-| :]+\|?$", l.strip())]
        if not data_lines:
            return None

        rows = []
        for idx, line in enumerate(data_lines):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cell_type = "tableHeader" if idx == 0 else "tableCell"

            row_cells = []
            for cell in cells:
                parts = cell.replace("\\n", "\n").split("\n")
                cell_content = [
                    {"type": "paragraph", "content": [{"type": "text", "text": p.strip()}]}
                    for p in parts if p.strip()
                ] or [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]
                row_cells.append({"type": cell_type, "content": cell_content})

            rows.append({"type": "tableRow", "content": row_cells})

        return {"type": "table", "content": rows}

    def create_task(self, title: str, description: str, project_key: str, language: str = "Ukrainian", issue_type: str = "Story", related_issue_key: str = None) -> str:
        url = f"{self.base_url}/rest/api/3/issue"

        sprint_id = self.get_active_sprint_id(project_key)
        if sprint_id:
            print(f"  Активний спринт знайдено (ID: {sprint_id})")
        else:
            print("  Активного спринту немає — задача потрапить в беклог")

        fields = {
            "project": {"key": project_key},
            "summary": title,
            "description": self._task_to_adf(description, language),
            "issuetype": {"name": issue_type},
        }
        if sprint_id:
            fields["customfield_10020"] = sprint_id

        response = requests.post(url, json={"fields": fields}, auth=self.auth, headers=self.headers)
        response.raise_for_status()
        task_key = response.json()["key"]

        if related_issue_key:
            self._link_issues(task_key, related_issue_key)

        return task_key

    def _task_to_adf(self, body: str, language: str = "Ukrainian") -> dict:
        ac_labels = {
            "Ukrainian": "Критерії приймання (Acceptance Criteria)",
            "Russian":   "Критерии приёмки (Acceptance Criteria)",
            "English":   "Acceptance Criteria",
        }

        content = []
        pattern = re.compile(r"##(\w+)##([^\n]*)\n(.*?)(?=##\w+##|\Z)", re.DOTALL)

        for match in pattern.finditer(body):
            marker  = match.group(1)
            heading = match.group(2).strip()
            text    = match.group(3).strip()

            if marker == "DESC":
                for line in text.replace("\\n", "\n").split("\n"):
                    line = line.strip()
                    if line:
                        content.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})

            elif marker == "SECTION":
                if heading:
                    content.append({
                        "type": "heading", "attrs": {"level": 3},
                        "content": [{"type": "text", "text": heading}],
                    })
                items = []
                for line in text.split("\n"):
                    line = line.strip().lstrip("-•").strip()
                    if not line:
                        continue
                    parts = line.replace("\\n", "\n").split("\n")
                    cell_content = [
                        {"type": "paragraph", "content": [{"type": "text", "text": p.strip()}]}
                        for p in parts if p.strip()
                    ]
                    if cell_content:
                        items.append({"type": "listItem", "content": cell_content})
                if items:
                    content.append({"type": "bulletList", "content": items})

            elif marker == "AC":
                content.append({
                    "type": "heading", "attrs": {"level": 3},
                    "content": [{"type": "text", "text": ac_labels.get(language, ac_labels["Ukrainian"])}],
                })
                items = []
                for line in text.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.replace("\\n", "\n").split("\n")
                    cell_content = [
                        {"type": "paragraph", "content": [{"type": "text", "text": p.strip()}]}
                        for p in parts if p.strip()
                    ]
                    if cell_content:
                        items.append({"type": "listItem", "content": cell_content})
                if items:
                    content.append({"type": "bulletList", "content": items})

        if not content:
            content = [{"type": "paragraph", "content": [{"type": "text", "text": body.strip()}]}]

        return {"type": "doc", "version": 1, "content": content}

    def get_active_sprint_id(self, project_key: str) -> int | None:
        boards_url = f"{self.base_url}/rest/agile/1.0/board"
        response = requests.get(boards_url, auth=self.auth, headers=self.headers,
                                params={"projectKeyOrId": project_key, "type": "scrum"})
        if not response.ok:
            return None
        boards = response.json().get("values", [])
        if not boards:
            return None

        board_id = boards[0]["id"]
        sprints_url = f"{self.base_url}/rest/agile/1.0/board/{board_id}/sprint"
        response = requests.get(sprints_url, auth=self.auth, headers=self.headers,
                                params={"state": "active"})
        if not response.ok:
            return None
        sprints = response.json().get("values", [])
        return sprints[0]["id"] if sprints else None

    def create_bug(self, title: str, description: str, project_key: str = None, related_issue_key: str = None, language: str = "Ukrainian") -> str:
        url = f"{self.base_url}/rest/api/3/issue"
        resolved_project = project_key or self.default_project_key

        sprint_id = self.get_active_sprint_id(resolved_project)
        if sprint_id:
            print(f"  Активний спринт знайдено (ID: {sprint_id})")
        else:
            print("  Активного спринту немає — баг потрапить в бэклог")

        fields = {
            "project": {"key": resolved_project},
            "summary": title,
            "description": self._structured_to_adf(description, language),
            "issuetype": {"name": "Bug"},
        }

        if sprint_id:
            fields["customfield_10020"] = sprint_id

        payload = {"fields": fields}
        response = requests.post(url, json=payload, auth=self.auth, headers=self.headers)
        response.raise_for_status()
        bug_key = response.json()["key"]

        if related_issue_key:
            self._link_issues(bug_key, related_issue_key)

        return bug_key

    def _structured_to_adf(self, body: str, language: str = "Ukrainian") -> dict:
        section_labels = {
            "Ukrainian": {
                "ENV": "Оточення",
                "DESC": "Опис проблеми",
                "STEPS": "Кроки для відтворення",
                "EXPECTED": "Очікуваний результат",
                "ACTUAL": "Фактичний результат",
                "ADDITIONAL": "Додаткова інформація",
            },
            "Russian": {
                "ENV": "Окружение",
                "DESC": "Описание проблемы",
                "STEPS": "Шаги воспроизведения",
                "EXPECTED": "Ожидаемый результат",
                "ACTUAL": "Фактический результат",
                "ADDITIONAL": "Дополнительная информация",
            },
            "English": {
                "ENV": "Environment",
                "DESC": "Problem Description",
                "STEPS": "Steps to Reproduce",
                "EXPECTED": "Expected Result",
                "ACTUAL": "Actual Result",
                "ADDITIONAL": "Additional Information",
            },
        }
        labels = section_labels.get(language, section_labels["Ukrainian"])

        sections = {}
        pattern = re.compile(r"##(\w+)##\n(.*?)(?=##\w+##|\Z)", re.DOTALL)
        for match in pattern.finditer(body):
            sections[match.group(1)] = match.group(2).strip()

        # Fall back to plain paragraph if no section markers found (e.g. old format)
        if not sections:
            return {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": body.strip()}]}],
            }

        content = []
        skip_values = {"не вказано", "не указано", "not specified", "немає", "нет", "none", "n/a"}

        for key in ("ENV", "DESC", "STEPS", "EXPECTED", "ACTUAL", "ADDITIONAL"):
            text = sections.get(key, "")
            if not text or text.lower() in skip_values:
                continue

            content.append({
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": labels[key]}],
            })

            if key == "STEPS":
                items = []
                for line in text.splitlines():
                    line = re.sub(r"^\d+\.\s*", "", line.strip())
                    if line:
                        items.append({"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]}]})
                if items:
                    content.append({"type": "orderedList", "content": items})

            elif key in ("EXPECTED", "ACTUAL"):
                items = []
                for line in text.splitlines():
                    line = line.lstrip("-• ").strip()
                    if line:
                        items.append({"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]}]})
                if items:
                    content.append({"type": "bulletList", "content": items})

            else:
                content.append({"type": "paragraph", "content": [{"type": "text", "text": text}]})

        return {"type": "doc", "version": 1, "content": content}

    def update_issue(self, issue_key: str, title: str = None, description: str = None, language: str = "Ukrainian") -> None:
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
        fields = {}
        if title:
            fields["summary"] = title
        if description:
            bug_markers = {"##ENV##", "##STEPS##", "##EXPECTED##", "##ACTUAL##"}
            if any(m in description for m in bug_markers):
                fields["description"] = self._structured_to_adf(description, language)
            else:
                fields["description"] = self._task_to_adf(description, language)
        if not fields:
            raise ValueError("Нічого оновлювати")
        response = requests.put(url, json={"fields": fields}, auth=self.auth, headers=self.headers)
        response.raise_for_status()

    def search_issues(self, jql: str, max_results: int = 50) -> list:
        url = f"{self.base_url}/rest/api/3/search/jql"
        params = {"jql": jql, "maxResults": max_results, "fields": "summary,status,issuetype,comment"}
        response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
        response.raise_for_status()
        return response.json().get("issues", [])

    def has_bot_comment(self, issue: dict) -> bool:
        return self._comment_contains(issue, "🤖")

    def has_test_cases_comment(self, issue: dict) -> bool:
        return self._comment_contains(issue, "Автоматически сгенерированные тест-кейсы")

    def has_quality_comment(self, issue_key: str) -> bool:
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"
        response = requests.get(url, auth=self.auth, headers=self.headers)
        response.raise_for_status()
        comments = response.json().get("comments", [])
        for c in comments:
            body = c.get("body") or ""
            content = body if isinstance(body, str) else self._extract_text_from_adf(body)
            if "🤖" in content and "Автоматически сгенерированные тест-кейсы" not in content:
                return True
        return False

    def _comment_contains(self, issue: dict, text: str) -> bool:
        comments = issue.get("fields", {}).get("comment", {}).get("comments", [])
        for c in comments:
            body = c.get("body") or ""
            content = body if isinstance(body, str) else self._extract_text_from_adf(body)
            if text in content:
                return True
        return False

    def _link_issues(self, bug_key: str, related_key: str) -> None:
        url = f"{self.base_url}/rest/api/3/issueLink"
        payload = {
            "type": {"name": "Blocks"},
            "inwardIssue": {"key": bug_key},
            "outwardIssue": {"key": related_key},
        }
        try:
            requests.post(url, json=payload, auth=self.auth, headers=self.headers)
            print(f"  ✓ {bug_key} связан с {related_key}")
        except Exception as e:
            print(f"  ! Не удалось связать задачи: {e}")

    def _extract_text_from_adf(self, adf_content, _list_counter=None) -> str:
        if not adf_content:
            return ""
        if isinstance(adf_content, str):
            return adf_content

        if isinstance(adf_content, list):
            return "\n".join(filter(None, [self._extract_text_from_adf(i) for i in adf_content]))

        if not isinstance(adf_content, dict):
            return ""

        node_type = adf_content.get("type")
        children = adf_content.get("content", [])

        if node_type == "text":
            return adf_content.get("text", "")

        if node_type == "orderedList":
            lines = []
            for idx, item in enumerate(children, start=1):
                item_text = self._extract_text_from_adf(item).strip()
                lines.append(f"{idx}. {item_text}")
            return "\n".join(lines)

        if node_type == "bulletList":
            lines = []
            for item in children:
                item_text = self._extract_text_from_adf(item).strip()
                lines.append(f"- {item_text}")
            return "\n".join(lines)

        if node_type == "listItem":
            return " ".join(filter(None, [self._extract_text_from_adf(c) for c in children]))

        if node_type == "paragraph":
            return " ".join(filter(None, [self._extract_text_from_adf(c) for c in children]))

        return "\n".join(filter(None, [self._extract_text_from_adf(c) for c in children]))
