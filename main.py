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


def _default_assignee_email(project_key: str, area: str) -> str | None:
    if not project_key or not area:
        return None
    key = project_key.upper().replace("-", "_")
    return os.getenv(f"PROJECT_ASSIGNEE_{area.upper()}_{key}") or None


def _assign_if_configured(jira: JiraClient, issue_key: str, project_key: str, area: str) -> None:
    email = _default_assignee_email(project_key, area)
    if not email:
        return
    account_id = jira.find_account_id(email)
    if not account_id:
        click.echo(f"⚠️  Відповідального {email} не знайдено — задачу не призначено")
        return
    jira.assign_issue(issue_key, account_id)
    click.echo(f"   Призначено на: {email}")


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
    comments = jira.get_comments(issue_key)
    click.echo(f"KEY: {issue['key']}")
    click.echo(f"TYPE: {issue['issue_type']}")
    click.echo(f"STATUS: {issue['status']}")
    click.echo(f"TITLE: {issue['title']}")
    click.echo(f"REPORTER: {issue['reporter_name']}")
    click.echo(f"REPORTER_ID: {issue['reporter_account_id'] or ''}")
    click.echo(f"\nDESCRIPTION:\n{issue['description'] or 'Відсутній'}")
    click.echo(f"\nACCEPTANCE_CRITERIA:\n{issue['acceptance_criteria'] or 'Не вказано'}")
    if comments:
        click.echo(f"\nCOMMENTS ({len(comments)}):")
        for i, c in enumerate(comments, 1):
            click.echo(f"\n[{i}] {c['author']} ({c['date']}):")
            click.echo(c['text'])
    else:
        click.echo("\nCOMMENTS: Немає")

    dest_dir = f"/tmp/jira-{issue_key}"
    attachments = jira.download_attachments(issue_key, dest_dir)
    if attachments:
        click.echo(f"\nATTACHMENTS ({len(attachments)}):")
        for att in attachments:
            click.echo(f"  [{att['mime_type']}] {att['path']}")
    else:
        click.echo("\nATTACHMENTS: Немає")


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
@click.option("--area", default=None, type=click.Choice(["front", "back"]), help="Область бага: front або back — для автопризначення відповідального з .env")
def new_bug(title, lang, project, related, area):
    """Прочитати структурований опис зі stdin і створити баг в Jira.

    \b
    Приклади:
      python main.py new-bug --title "Попап не відкривається" --lang UA --related GN-1652 --area front << 'EOF'
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
    _assign_if_configured(jira, bug_key, bug_key.split("-")[0], area)


@cli.command("new-task")
@click.option("--title", required=True, help="Заголовок задачі")
@click.option("--lang", default=None, help="Мова: UA, RU, EN. Якщо не вказано — запитається інтерактивно")
@click.option("--project", required=True, help="Ключ проекту (наприклад GN)")
@click.option("--type", "issue_type", default=None, help="Тип: Story, Task. Якщо не вказано — запитається інтерактивно")
@click.option("--related", default=None, help="Ключ пов'язаної задачі (наприклад GN-1652)")
@click.option("--area", default=None, type=click.Choice(["front", "back"]), help="Область задачі: front або back — для автопризначення відповідального з .env")
def new_task(title, lang, project, issue_type, related, area):
    """Прочитати опис задачі зі stdin і створити в Jira.

    \b
    Приклад:
      python main.py new-task --title "Назва" --lang UA --type Story --project GN --area back << 'EOF'
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
        related_issue_key=related,
    )
    click.echo(f"\n✅ {issue_type} створено: {task_key}")
    click.echo(f"   Посилання: {jira.base_url}/browse/{task_key}")
    _assign_if_configured(jira, task_key, project, area)


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


def _human_readable_to_table(text):
    """Convert human-readable test case format to markdown table."""
    rows = []
    current_suite = ""
    current_case = None
    current_desc = ""
    current_priority = ""
    current_steps = []
    in_steps = False

    for line in text.split("\n"):
        if line.startswith("### "):
            if current_case is not None:
                rows.append((current_suite, current_case, current_desc, "\\n".join(current_steps), current_priority))
            current_case = line[4:].strip()
            current_desc = ""
            current_priority = ""
            current_steps = []
            in_steps = False
        elif line.startswith("## ") and not line.startswith("### "):
            suite_name = line[3:].strip()
            if any(w in suite_name for w in ("Рекомендац", "Recommend")):
                break
            current_suite = suite_name
            in_steps = False
        elif line.startswith(("Опис:", "Описание:", "Description:")):
            val = line.split(":", 1)[1].strip()
            current_desc = "" if val in ("(якщо є)", "(если есть)", "(if any)") else val
        elif line.startswith(("Пріоритет:", "Приоритет:", "Priority:")):
            current_priority = line.split(":", 1)[1].strip()
        elif line.startswith(("Кроки:", "Шаги:", "Steps:")):
            in_steps = True
        elif in_steps and line.strip():
            current_steps.append(line.strip())
        elif in_steps and not line.strip():
            in_steps = False

    if current_case is not None:
        rows.append((current_suite, current_case, current_desc, "\\n".join(current_steps), current_priority))

    if not rows:
        return None

    has_suites = any(r[0] for r in rows)
    if has_suites:
        lines = ["| Suite | Case | Description | Steps | Priority |", "|---|---|---|---|---|"]
        for suite, case, desc, steps, priority in rows:
            lines.append(f"| {suite} | {case} | {desc} | {steps} | {priority} |")
    else:
        lines = ["| Case | Description | Steps | Priority |", "|---|---|---|---|"]
        for _, case, desc, steps, priority in rows:
            lines.append(f"| {case} | {desc} | {steps} | {priority} |")

    return "\n".join(lines)


@cli.command("publish-tests")
@click.argument("issue_key", required=False)
@click.option("--convert", is_flag=True, default=False, help="Конвертувати людиночитаємий формат у таблицю перед публікацією")
def publish_tests(issue_key, convert):
    """Опублікувати тест-кейси з test-cases/ як коментар до задачі.

    \b
    Приклади:
      python main.py publish-tests MES-477
      python main.py publish-tests MES-477 --convert
    """
    import glob
    test_cases_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test-cases")

    if issue_key:
        file_path = os.path.join(test_cases_dir, f"{issue_key.upper()}.md")
        if not os.path.exists(file_path):
            click.echo(f"❌ Файл не знайдено: {file_path}")
            sys.exit(1)
    else:
        files = sorted(glob.glob(os.path.join(test_cases_dir, "*.md")))
        if not files:
            click.echo("❌ В папці test-cases немає збережених тест-кейсів")
            sys.exit(1)
        choices = [os.path.basename(f).replace(".md", "") for f in files]
        choice = questionary.select("Тест-кейси якої задачі опублікувати?", choices=choices).ask()
        if not choice:
            sys.exit(0)
        issue_key = choice
        file_path = os.path.join(test_cases_dir, f"{issue_key}.md")

    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if not text:
        click.echo(f"❌ Файл {file_path} порожній")
        sys.exit(1)

    if convert:
        converted = _human_readable_to_table(text)
        if not converted:
            click.echo("❌ Не вдалося розпізнати формат файлу для конвертації")
            sys.exit(1)
        text = converted
        click.echo("✓ Конвертовано у табличний формат")

    click.echo(f"\n⏳ Публікую тест-кейси для {issue_key}...")
    jira = JiraClient()
    jira.add_comment(issue_key, text)
    click.echo(f"✅ Тест-кейси опубліковано до {issue_key}")
    click.echo(f"   Посилання: {jira.base_url}/browse/{issue_key}")


@cli.command()
@click.argument("issue_key")
@click.argument("file_path", type=click.Path(exists=True))
def attach(issue_key, file_path):
    """Прикріпити файл до задачі.

    \b
    Приклад:
      python main.py attach GN-1808 /path/to/screenshot.png
    """
    click.echo(f"\n⏳ Прикріплюю файл до {issue_key}...")
    jira = JiraClient()
    filename = jira.attach_file(issue_key, file_path)
    click.echo(f"✅ Файл прикріплено: {filename}")
    click.echo(f"   Посилання: {jira.base_url}/browse/{issue_key}")


@cli.command()
@click.argument("issue_key")
@click.option("--email", required=True, help="Email користувача-виконавця")
def assign(issue_key, email):
    """Призначити виконавця задачі за email.

    \b
    Приклад:
      python main.py assign GN-1808 --email user@example.com
    """
    jira = JiraClient()
    click.echo(f"\n⏳ Шукаю користувача {email}...")
    account_id = jira.find_account_id(email)
    if not account_id:
        click.echo(f"❌ Користувача з email {email} не знайдено")
        sys.exit(1)
    jira.assign_issue(issue_key, account_id)
    click.echo(f"✅ Задачу {issue_key} призначено на {email}")
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
