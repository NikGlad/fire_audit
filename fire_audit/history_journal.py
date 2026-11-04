"""Local, append-only evidence journal; completeness is checked, never inferred from chat."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

JOURNAL_NAME = "ЖУРНАЛ_СОБЫТИЙ.sqlite3"
EVENT_KINDS = ("user_answer", "document", "plan_change", "decision", "question", "scope_change", "correction")
SOURCE_KINDS = ("conversation", "documents", "plans", "previous_checks")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@contextmanager
def database(project_dir):
    with closing(sqlite3.connect(Path(project_dir) / JOURNAL_NAME, timeout=30)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                   "event_id TEXT UNIQUE NOT NULL, run_id TEXT NOT NULL, kind TEXT NOT NULL, "
                   "recorded_at TEXT NOT NULL, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS heads (document TEXT PRIMARY KEY, state TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        yield db


def append(db, run_id, kind, payload, event_id=None):
    event_id = event_id or uuid4().hex
    previous = db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
    if previous:
        if previous['run_id'] != run_id or previous['kind'] != kind or previous['payload'] != encoded(payload):
            raise ValueError("Событие уже записано с другим содержанием; добавьте correction со ссылкой supersedes")
        return event_id
    db.execute("INSERT INTO events(event_id,run_id,kind,recorded_at,payload) VALUES(?,?,?,?,?)",
               (event_id, run_id, kind, now(), encoded(payload)))
    return event_id


def document_location(path):
    path = Path(path)
    if path.name == 'project.json' and path.parent.parent.name == 'projects':
        return path.parent, '', 'project.json'
    if path.name in {'run.json', 'РЕЕСТР_ПРОВЕРКИ.json'} and path.parent.parent.name == 'runs':
        project = path.parent.parent.parent
        return project, path.parent.name, str(path.relative_to(project))
    return None


def normalized(value):
    value = copy.deepcopy(value)
    if isinstance(value, dict):
        # Validation time is not a new finding or a change of project evidence.
        if isinstance(value.get('graphics_layer_check'), dict):
            value['graphics_layer_check'].pop('checked_at', None)
        if isinstance(value.get('metadata'), dict):
            value['metadata'].pop('updated_at', None)
    return value


def parts(value):
    """Store changed findings individually, without duplicating an entire large registry."""
    result = {}
    for key, item in (value or {}).items():
        if isinstance(item, list) and item and all(isinstance(x, dict) and isinstance(x.get('id'), str) for x in item):
            ids = [x['id'] for x in item]
            if len(ids) == len(set(ids)):
                result[key + '/@order'] = ids
                result.update({key + '/' + x['id']: x for x in item})
                continue
        result[key] = item
    return result


def snapshot(db, run_id, document, value, origin):
    value = normalized(value)
    head = db.execute("SELECT state FROM heads WHERE document=?", (document,)).fetchone()
    old = json.loads(head['state']) if head else {}
    if head and old == value:
        return
    before, after = parts(old), parts(value)
    changes = [{"path": key, "before_present": key in before, "before": before.get(key),
                "after_present": key in after, "after": after.get(key)}
               for key in sorted(before.keys() | after.keys())
               if (key in before) != (key in after) or before.get(key) != after.get(key)]
    append(db, run_id, 'state_snapshot', {"document": document, "origin": origin,
           "before_digest": digest(old) if head else None, "after_digest": digest(value), "changes": changes})
    db.execute("INSERT OR REPLACE INTO heads(document,state) VALUES(?,?)", (document, encoded(value)))


def observe_write(path, before, after):
    location = document_location(path)
    if not location:
        return
    project, run_id, document = location
    with database(project) as db:
        if before is not None:
            snapshot(db, run_id, document, before, 'before_application_write')
        snapshot(db, run_id, document, after, 'application_write')


def synchronize(project, run_id):
    """Also detect direct file edits at checkpoints; unseen intermediate edits cannot be recovered."""
    project = Path(project)
    with database(project) as db:
        for path in (project / 'project.json', project / 'runs' / run_id / 'run.json',
                     project / 'runs' / run_id / 'РЕЕСТР_ПРОВЕРКИ.json'):
            if not path.is_file():
                raise ValueError('Нет файла истории: ' + str(path))
            _, owner, document = document_location(path)
            snapshot(db, owner, document, json.loads(path.read_text(encoding='utf-8-sig')), 'checkpoint')


def rows(db, run_id):
    start = db.execute("SELECT MIN(seq) FROM events WHERE run_id=?", (run_id,)).fetchone()[0] or 0
    return [{**dict(row), 'payload': json.loads(row['payload'])} for row in db.execute(
        "SELECT * FROM events WHERE run_id=? OR (run_id='' AND seq>=?) ORDER BY seq", (run_id, start))]


def history(project, run_id):
    synchronize(project, run_id)
    with database(project) as db:
        events = rows(db, run_id)
    latest = next((x for x in reversed(events) if x['kind'] == 'reconciliation'), None)
    return {'journal_file': str(Path(project) / JOURNAL_NAME), 'run_id': run_id,
            'events': events, 'latest_reconciliation': latest,
            'coverage_limit': 'Журнал содержит сохранённые события. Сверьте весь доступный диалог и документы; невидимые приложению сообщения автоматически не извлекаются.'}


def record_event(project, run_id, event):
    if not isinstance(event, dict):
        raise ValueError('Событие должно быть объектом')
    for key in ('id', 'kind', 'event_date', 'source', 'text'):
        if not isinstance(event.get(key), str) or not event[key].strip():
            raise ValueError('Для события нужны id, kind, event_date, source и исходный text')
    if event['kind'] not in EVENT_KINDS:
        raise ValueError('Неизвестный вид события')
    date.fromisoformat(event['event_date'])
    event = copy.deepcopy(event)
    if event.get('source_path'):
        path = Path(event['source_path'])
        if not path.is_file():
            raise ValueError('Файл события не найден: ' + str(path))
        event['source_file'] = file_identity(path)
    synchronize(project, run_id)
    with database(project) as db:
        supersedes = event.get('supersedes')
        if supersedes and not db.execute("SELECT 1 FROM events WHERE event_id=?", (supersedes,)).fetchone():
            raise ValueError('Уточняемое событие supersedes не найдено в этом объекте')
        append(db, run_id, event['kind'], event, event['id'])
    return {'event_id': event['id'], 'journal_file': str(Path(project) / JOURNAL_NAME)}


def inventory_checked(inventory):
    if not isinstance(inventory, list) or not inventory:
        raise ValueError('Нужен перечень сверенных источников source_inventory')
    kinds = set()
    checked = []
    for item in inventory:
        if not isinstance(item, dict) or item.get('kind') not in SOURCE_KINDS:
            raise ValueError('Неизвестный вид источника истории')
        if not str(item.get('reference', '')).strip():
            raise ValueError('Для каждого источника нужна точная ссылка или пояснение отсутствия')
        kinds.add(item['kind'])
        row = copy.deepcopy(item)
        if item.get('path'):
            path = Path(item['path'])
            if not path.is_file():
                raise ValueError('Источник не найден: ' + str(path))
            row['sha256'] = file_identity(path)['sha256']
        checked.append(row)
    if kinds != set(SOURCE_KINDS):
        raise ValueError('Сверьте диалог, документы, планы и предыдущие проверки; отсутствие источников укажите явно')
    return checked


def file_identity(path):
    before = Path(path).stat()
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    after = Path(path).stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Файл изменился во время чтения: ' + str(path))
    return {'path': str(Path(path).resolve()), 'sha256': h.hexdigest()}


def reconcile(project, run_id, review):
    synchronize(project, run_id)
    if not isinstance(review, dict) or review.get('unrecorded_events') != []:
        raise ValueError('Сначала внесите все пропущенные события; unrecorded_events должен быть пустым списком')
    if not str(review.get('conversation_through', '')).strip():
        raise ValueError('Укажите последнее сверенное сообщение conversation_through')
    inventory = inventory_checked(review.get('source_inventory'))
    dispositions = review.get('dispositions', [])
    if not isinstance(dispositions, list):
        raise ValueError('dispositions должен быть списком')
    with database(project) as db:
        events = [e for e in rows(db, run_id) if e['kind'] != 'reconciliation']
        expected = [e['event_id'] for e in events]
        received = review.get('reviewed_event_ids', [])
        if not isinstance(received, list) or len(received) != len(set(received)) or set(received) != set(expected):
            raise ValueError('Не сверены все события текущего запуска; перечитайте get_run_history')
        manual = {e['event_id'] for e in events if e['kind'] in EVENT_KINDS}
        covered = set()
        for row in dispositions:
            if not isinstance(row, dict) or row.get('event_id') not in manual or row['event_id'] in covered:
                raise ValueError('Неизвестное или повторное событие в dispositions')
            if row.get('status') not in {'applied', 'no_impact', 'unresolved', 'superseded'}:
                raise ValueError('Укажите applied, no_impact, unresolved или superseded')
            if not str(row.get('reason', '')).strip() or not str(row.get('evidence', '')).strip():
                raise ValueError('Для каждого ответа/решения нужны причина и ссылка evidence на результат или открытый вопрос')
            covered.add(row['event_id'])
        if covered != manual:
            raise ValueError('Нет решения или зарегистрированного открытого вопроса по каждому ручному событию')
        payload = {**copy.deepcopy(review), 'source_inventory': inventory}
        event_id = append(db, run_id, 'reconciliation', payload)
    return {'event_id': event_id, 'status': 'reconciled', 'reviewed_events': len(expected)}


def completion_errors(project, run_id):
    data = history(project, run_id)
    review = data['latest_reconciliation']
    if not review:
        return ['История не сверена: выполните get_run_history и reconcile_run_history перед завершением']
    expected = [e['event_id'] for e in data['events'] if e['kind'] != 'reconciliation']
    if review['payload'].get('reviewed_event_ids') != expected:
        # Input order is not significant, but new or removed events are.
        if set(review['payload'].get('reviewed_event_ids', [])) != set(expected):
            return ['После сверки истории появились изменения; дополните журнал и повторите reconcile_run_history']
    try:
        inventory = inventory_checked(review['payload']['source_inventory'])
    except (ValueError, OSError) as exc:
        return ['Источник сверки истории недоступен: ' + str(exc)]
    if inventory != review['payload']['source_inventory']:
        return ['Файлы источников изменились после сверки истории; зарегистрируйте изменение и повторите сверку']
    return []


def reference(project):
    path = Path(project) / JOURNAL_NAME
    if not path.exists():
        return {'path': str(path), 'available': False, 'legacy_history_requires_review': True}
    # Read without creating tables or modifying an archived object.
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        count, latest = db.execute('SELECT COUNT(*), MAX(seq) FROM events').fetchone()
    return {'path': str(path), 'available': True, 'event_count': count, 'latest_sequence': latest,
            'instruction': 'Прочитать через get_run_history; старые записи не подтверждают полноту переписки автоматически'}


def enabled(project, run_id):
    path = Path(project) / JOURNAL_NAME
    if not path.exists():
        return False
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return bool(db.execute("SELECT 1 FROM events WHERE run_id=? AND kind<>'state_snapshot' LIMIT 1",
                               (run_id,)).fetchone())


HISTORY_INSTRUCTION = """Обязательная фиксация истории текущего объекта: сразу после каждого относящегося к нему содержательного ответа пользователя,
нового файла/плана, уточнения области работ, вопроса или решения вызови record_history_event.
Сохрани исходный текст отдельно от своей интерпретации, дату события и точный источник;
свяжи событие с помещением, планом, устойчивым id замечания, нормой и затронутыми видами работ.
Для уточнения используй новое событие с supersedes; прежнюю запись не переписывай.
До зависимых расчётов и изменения DWG обнови факты и решения в текущем реестре и record_continuity.
После каждого законченного участка и перед сменой чата/этапа перечитай get_run_history (все страницы),
сверь диалог, вложения, планы, ответы и решения. При продолжении сначала восстанови эти записи и открытые вопросы.
После регистрации результата и контроля качества выполни validate_run, затем reconcile_run_history:
перечисли все источники, последнее сверенное сообщение, все reviewed_event_ids и решение по каждому ручному событию.
Пропущенные события сначала внеси; unresolved допускается только как явно зарегистрированный открытый вопрос,
а не как подтверждение соответствия. Итоговый validate_run(for_application=true) должен подтвердить актуальность сверки.
Если данные изменились после сверки, повтори её. Не ожидай команды «продолжай» после записи истории.
Приложение автоматически сохраняет версии паспорта и реестра при записи через API; это не заменяет фиксацию сообщений.
Не заявляй полноту недоступной переписки или старых промежуточных изменений: укажи границу восстановленной истории.
"""
