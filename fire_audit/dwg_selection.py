"""Separate user DWG selection instructions from a confirmed filesystem path."""
from __future__ import annotations

import ntpath
import re
from pathlib import Path


def dwg_location(project: dict) -> str:
    # Preserve the existing passport key and all previously entered values.
    return str(project.get("dwg", {}).get("path", "") or "").strip()


def explicit_dwg_path(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    if (not text or any(char in text for char in '\n\r\x00')
            or not text.lower().endswith('.dwg')
            or not (ntpath.isabs(text) or Path(text).is_absolute())):
        return ""
    # A second drive or a quoted instruction is not one filesystem path.
    if '"' in text or len(re.findall(r'[A-Za-z]:[\\/]', text)) > 1:
        return ""
    return text


def resolved_source_path(run: dict, registry: dict) -> str:
    return (explicit_dwg_path(registry.get('dwg_actions', {}).get('source_path', ''))
            or explicit_dwg_path(dwg_location(run.get('project_snapshot', {}))))


def dwg_selection_markdown(project: dict) -> str:
    location = dwg_location(project)
    supplied = '\n'.join('> ' + line for line in location.splitlines()) if location else '> Не заполнено — использовать открытые DWG в AutoCAD.'
    return (
        '## Расположение файла DWG и выбор исходных чертежей\n\n'
        'По умолчанию работай с открытым файлом или файлами DWG в AutoCAD, даже если поле '
        '«Расположение файла DWG» не заполнено. Пустое поле не блокирует начало работы. '
        'Через AUTOM8LABS прочитай list_open_drawings и get_drawing_info; определи состав '
        'исходных документов по текущему заданию. Если открыто несколько относящихся к заданию '
        'DWG, учти их все, а не только активный документ.\n\n'
        'В поле можно указать путь, папку, несколько файлов или свободные пояснения. '
        'Учитывай отдельные указания из этого поля и непосредственно из текущего задания в чате GPT. '
        'При противоречии приоритет имеет более позднее явное указание пользователя в чате. '
        'Не считай пояснение подтверждённым путём и не переноси выбор из другого объекта.\n\n'
        'Вводные поля «Расположение файла DWG»:\n' + supplied + '\n\n'
        'Перед изменением чертежа установи и зафиксируй фактический полный путь каждого выбранного '
        'исходного DWG и его документа AutoCAD. При неясности состава файлов уточни адресата '
        'до операций записи. В многофайловом задании веди отдельную адресацию объектов и результатов '
        'по каждому DWG. Все действующие правила сохранности исходников и рабочих копий сохраняются. '
        'Для record_result_dwg передай фактический source_path, установленный через AUTOM8LABS, '
        'если поле пустое, содержит пояснения или выбор уточнён в чате.\n'
    )
