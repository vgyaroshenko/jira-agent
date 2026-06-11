import os
import sys
import click
import questionary
from dotenv import load_dotenv
from jira_client import JiraClient

load_dotenv()

_LANG_CHOICES = ["UA — українська", "RU — русский", "EN — English"]
_TYPE_CHOICES = ["Story", "Task"]
_LANG_MAP = {"RU": "Russian", "UA": "Ukrainian", "EN": "English"}


def _detect_language(text: str) -> str:
    if any(c in set("іїєґІЇЄҐ") for c in text):
        return "Ukrainian"
    if any(c in set("ёыъэЁЫЪЭ") for c in text):
        return "Russian"
    if any("Ѐ" <= c <= "ӿ" for c in text):
        return "Russian"
    return "English"


def _ask_language(prompt: str) -> str:
    choice = questionary.select(prompt, choices=_LANG_CHOICES).ask()
    if not choice:
        sys.exit(0)
    return choice.split(" — ")[0]


def _ask_type() -> str:
    choice = questionary.select("Що створити?", choices=_TYPE_CHOICES).ask()
    if not choice:
        sys.exit(0)
    return choice


@click.group()
def cli():
    """🤖 Jira QA Agent — інструменти для Claude Code"""
    pass


@cli.command()
@click.argument("issue_key")
def fetch(issue_key):
    """Отримати дані задачі з Jira і вивести в stdout.

    \b
    Приклад:
      python main.py fetch GN-1808
    """
    jira = JiraClient()
    issue = jira.get_issue_text(issue_key)
    click.echo(f"KEY: {issue['key']}")
    click.echo(f"TYPE: {issue['issue_type']}")
    click.echo(f"STATUS: {issue['status']}")
    click.echo(f"TITLE: {issue['title']}")
    click.echo(f"REPORTER: {issue['reporter_name']}")
    click.echo(f"REPORTER_ID: {issue['reporter_account_id'] or ''}")
    click.echo(f"\nDESCRIPTION:\n{issue['description'] or 'Відсутній'}")
    click.echo(f"\nACCEPTANCE_CRITERIA:\n{issue['acceptance_criteria'] or 'Не вказано'}")


@cli.command()
@click.argument("issue_key")
@click.option("--mention", default=None, help="Account ID для згадки в коментарі")
def comment(issue_key, mention):
    """Прочитати текст зі stdin і додати коментарем до задачі.

    \b
    Приклади:
      echo "Тест-кейси..." | python main.py comment GN-1808
      python main.py comment GN-1808 --mention 557058:xxx < comment.txt
    """
    if sys.stdin.isatty():
        click.echo("📝 Введіть текст коментаря. Коли закінчите — натисніть Ctrl+D:")
        click.echo("-" * 40)
    text = sys.stdin.read().strip()
    if not text:
        click.echo("❌ Текст коментаря порожній")
        sys.exit(1)
    click.echo(f"\n⏳ Додаю коментар до {issue_key}...")
    jira = JiraClient()
    jira.add_comment(issue_key, text, mention_account_id=mention)
    click.echo(f"✅ Коментар додано до {issue_key}")


@cli.command("new-bug")
@click.option("--title", required=True, help="Заголовок бага (до 80 символів)")
@click.option("--lang", default=None, help="Мова: UA, RU, EN. Якщо не вказано — запитається інтерактивно")
@click.option("--project", default=None, help="Ключ проекту (наприклад GN)")
@click.option("--related", default=None, help="Ключ пов'язаної задачі (наприклад GN-1652)")
def new_bug(title, lang, project, related):
    """Прочитати структурований опис зі stdin і створити баг в Jira.

    \b
    Приклади:
      python main.py new-bug --title "Попап не відкривається" --lang UA --related GN-1652 << 'EOF'
      ##ENV##
      ...
      EOF
    """
    if not lang:
        lang = _ask_language("На якій мові створити баг-репорт?")

    description = sys.stdin.read().strip()
    if not description:
        click.echo("❌ Опис порожній")
        sys.exit(1)

    language = _LANG_MAP.get(lang.upper(), _detect_language(title))
    project_key = project or (related.split("-")[0] if related else None)

    click.echo(f"\n⏳ Створюю баг...")
    jira = JiraClient()
    bug_key = jira.create_bug(
        title=title[:80],
        description=description,
        project_key=project_key,
        related_issue_key=related,
        language=language,
    )
    click.echo(f"\n✅ Баг створено: {bug_key}")
    click.echo(f"   Посилання: {jira.base_url}/browse/{bug_key}")


@cli.command("new-task")
@click.option("--title", required=True, help="Заголовок задачі")
@click.option("--lang", default=None, help="Мова: UA, RU, EN. Якщо не вказано — запитається інтерактивно")
@click.option("--project", required=True, help="Ключ проекту (наприклад GN)")
@click.option("--type", "issue_type", default=None, help="Тип: Story, Task. Якщо не вказано — запитається інтерактивно")
def new_task(title, lang, project, issue_type):
    """Прочитати опис задачі зі stdin і створити в Jira.

    \b
    Приклад:
      python main.py new-task --title "Назва" --lang UA --type Story --project GN << 'EOF'
      ##DESC##
      ...
      EOF
    """
    if not issue_type:
        issue_type = _ask_type()

    if not lang:
        lang = _ask_language("На якій мові створити задачу?")

    description = sys.stdin.read().strip()
    if not description:
        click.echo("❌ Опис порожній")
        sys.exit(1)

    language = _LANG_MAP.get(lang.upper(), _detect_language(title))

    click.echo(f"\n⏳ Створюю {issue_type} в проекті {project}...")
    jira = JiraClient()
    task_key = jira.create_task(
        title=title,
        description=description,
        project_key=project,
        language=language,
        issue_type=issue_type,
    )
    click.echo(f"\n✅ {issue_type} створено: {task_key}")
    click.echo(f"   Посилання: {jira.base_url}/browse/{task_key}")


@cli.command()
@click.argument("issue_key")
@click.option("--title", default=None, help="Новий заголовок задачі")
@click.option("--lang", default=None, help="Мова: UA, RU, EN. Якщо не вказано — запитається інтерактивно")
def update(issue_key, title, lang):
    """Оновити задачу в Jira: заголовок та/або опис зі stdin.

    \b
    Приклади:
      python main.py update GN-1808 --title "Новий заголовок"
      python main.py update GN-1808 --lang UA << 'EOF'
      ##DESC##
      ...
      EOF
      python main.py update GN-1808 --title "Новий заголовок" --lang UA << 'EOF'
      ##DESC##
      ...
      EOF
    """
    description = None
    if not sys.stdin.isatty():
        description = sys.stdin.read().strip() or None

    if not title and not description:
        click.echo("❌ Вкажіть --title або передайте новий опис через stdin")
        sys.exit(1)

    if description and not lang:
        lang = _ask_language("На якій мові оновити задачу?")

    language = _LANG_MAP.get((lang or "").upper(), _detect_language(description or title or ""))

    click.echo(f"\n⏳ Оновлюю {issue_key}...")
    jira = JiraClient()
    jira.update_issue(issue_key, title=title, description=description, language=language)
    click.echo(f"✅ Задачу {issue_key} оновлено")
    click.echo(f"   Посилання: {jira.base_url}/browse/{issue_key}")


@cli.command()
@click.argument("project_key")
def lang(project_key):
    """Повернути мову проекту з .env (PROJECT_LANG_KEY). Виводить UA, RU або EN.

    \b
    Приклад:
      python main.py lang GN
    """
    key = project_key.upper().replace("-", "_")
    language = os.getenv(f"PROJECT_LANG_{key}", "")
    click.echo(language)


@cli.command()
@click.argument("project_key")
def sprint(project_key):
    """Вивести задачі активного спринту (для перевірки якості через Claude Code).

    \b
    Приклад:
      python main.py sprint GN
    """
    jira = JiraClient()
    jql = (
        f"project = {project_key} "
        f"AND sprint in openSprints() "
        f"AND statusCategory != Done "
        f'AND status not in ("QA", "Test", "Ready for Deploy", "QA Prod")'
    )
    issues = jira.search_issues(jql)
    if not issues:
        click.echo("Задач в активному спринті немає")
        return

    click.echo(f"Знайдено задач: {len(issues)}\n")
    for issue in issues:
        key = issue["key"]
        title = issue["fields"]["summary"]
        status = issue["fields"]["status"]["name"]
        has_tc = jira.has_test_cases_comment(issue)
        has_quality = jira.has_bot_comment(issue)
        tc_mark = "✅TC" if has_tc else "❌TC"
        qc_mark = "✅QC" if has_quality else "❌QC"
        click.echo(f"{key}  [{status}]  {tc_mark}  {qc_mark}  —  {title}")


if __name__ == "__main__":
    cli()
