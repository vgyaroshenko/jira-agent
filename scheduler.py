"""
DEPRECATED: Планировщик требует прямого доступа к Anthropic API (ANTHROPIC_API_KEY).
При использовании через Claude Code этот файл не нужен — проверку спринта делай вручную:
  python main.py sprint GN

---
Планировщик: проверяет все задачи и баги в активном спринте.
Запускается дважды в день — в 11:00 и 17:00 по Киеву (08:00 и 14:00 UTC).

Логика для каждой задачи:
  - Статус QA или Done → пропускаем
  - Описание недостаточное → комментарий с тем что нужно добавить (один раз)
  - Описание достаточное + нет тест-кейсов → генерируем тест-кейсы
  - Тест-кейсы уже есть → пропускаем

Настройка crontab:
  0 8  * * * cd /Users/volod/dev/claude/jira-agent && .venv/bin/python scheduler.py
  0 14 * * * cd /Users/volod/dev/claude/jira-agent && .venv/bin/python scheduler.py

Проекты задаются через JIRA_PROJECT_KEY в .env (через запятую: GN,CCW,DEV)
"""
from datetime import datetime
from pathlib import Path
from jira_client import JiraClient
from agent import JiraQAAgent
from config import JIRA_DEFAULT_PROJECT_KEY

LOG_FILE = Path(__file__).parent / "scheduler.log"

SKIP_STATUSES = {"QA", "Done", "Closed", "Resolved"}


def check_sprint_issues():
    jira = JiraClient()
    agent = JiraQAAgent()

    project_keys = [k.strip() for k in JIRA_DEFAULT_PROJECT_KEY.split(",") if k.strip()]
    projects_jql = ", ".join(project_keys)

    jql = (
        f'project in ({projects_jql}) '
        f'AND sprint in openSprints() '
        f'AND statusCategory != Done '
        f'AND status not in ("QA", "Test", "Ready for Deploy", "QA Prod")'
    )

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Проверяю задачи в спринте...")
    print(f"  Проекты: {projects_jql}")

    issues = jira.search_issues(jql)

    if not issues:
        print("  Задач в спринте нет")
        return

    print(f"  Найдено задач: {len(issues)}")

    ok_count = 0
    commented_issues = []
    skipped_count = 0

    for issue in issues:
        key = issue["key"]
        title = issue["fields"]["summary"]

        already_commented = jira.has_bot_comment(issue)
        already_has_tc = jira.has_test_cases_comment(issue)

        if already_commented and already_has_tc:
            skipped_count += 1
            continue

        is_ok = agent.check_task_quality(key)

        if is_ok:
            ok_count += 1
            if not already_has_tc:
                agent.generate_test_cases(key, post_to_jira=True)
        else:
            if not already_commented:
                commented_issues.append({"key": key, "title": title})

    print(f"\n  Итого: {ok_count} получили тест-кейсы, {len(commented_issues)} получили комментарий, {skipped_count} пропущено")
    _write_log(commented_issues, ok_count, skipped_count)


def _write_log(commented: list[dict], ok_count: int, skipped_count: int) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n[{timestamp}]"]
    lines.append(f"  Тест-кейсы: {ok_count}, комментарий: {len(commented)}, пропущено: {skipped_count}")
    for issue in commented:
        lines.append(f"  • {issue['key']} — {issue['title']}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  [log] Записано в {LOG_FILE}")


if __name__ == "__main__":
    check_sprint_issues()
