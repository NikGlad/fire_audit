"""Read-only CAD inventory and durable, itemized decisions for barrier sources."""
from __future__ import annotations

import copy
from pathlib import Path

from .barrier_remarks import new_source_remarks, source_remarks_errors, inventory_digest
from .barrier_remarks_capture import capture_source_layer
from .io_utils import read_json, write_json, utc_now, sha256_file
from .dwg_selection import resolved_source_path
from . import barrier_remarks_moves as moves
from .norm_lock import normative_update_lock

INVENTORY_FILE = "ИСТОЧНИКИ_ЗАМЕЧАНИЙ_ПП.json"
BASELINE_FILE = "ОРИГИНАЛ_ЗАМЕЧАНИЙ_ПП.json"


def applies(run):
    return run.get("mode") == "barrier_assessment" and run.get("task_variant") != "object_norms"


def _file_fingerprint(path):
    path = Path(path)
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Исходный файл DWG изменился во время чтения; перечитайте исходник")
    return {"size": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest}


def _check_source_version(baseline, full=False):
    recorded = baseline.get("source_file_fingerprint")
    if (not isinstance(recorded, dict) or type(recorded.get("size")) is not int
            or type(recorded.get("mtime_ns")) is not int
            or not isinstance(recorded.get("sha256"), str) or len(recorded["sha256"]) != 64):
        raise ValueError("Не закреплена версия исходного файла DWG; перечитайте исходник")
    path = Path(baseline["source_path"])
    stat = path.stat()
    changed = (recorded["size"], recorded["mtime_ns"]) != (stat.st_size, stat.st_mtime_ns)
    if changed or (full and _file_fingerprint(path) != recorded):
        raise ValueError("Исходный файл DWG изменился после снимка; перечитайте исходник и проверьте решения")


def stored_errors(run, registry, folder):
    """Also bind the editable register to the independently captured inventory."""
    if not applies(run):
        return []
    errors = source_remarks_errors(registry, run.get("task_variant", "full_execution"))
    data = registry.get("barrier_assessment") if isinstance(registry, dict) else None
    record = data.get("source_remarks", {}) if isinstance(data, dict) else None
    if not isinstance(record, dict):
        return errors
    inventory = record.get("inventory", {})
    if not isinstance(inventory, dict):
        return errors
    if record.get("refresh_error"):
        errors.append("Не подтверждено актуальное чтение слоя пояснений: " + str(record["refresh_error"]))
    if inventory.get("run_id") != run.get("run_id"):
        errors.append("Снимок замечаний слоя относится к другому запуску или не закреплён")
    expected = resolved_source_path(run, registry)
    actual = inventory.get("source_path")
    if not expected or not isinstance(actual, str) or Path(actual).resolve() != Path(expected).resolve():
        errors.append("Снимок замечаний слоя не связан с исходным DWG этого запуска")
    try:
        captured = read_json(Path(folder) / INVENTORY_FILE)
        if not isinstance(captured, dict) or captured != inventory:
            errors.append("Реестр не соответствует сохранённому снимку чтения слоя через AUTOM8LABS")
        baseline = read_json(Path(folder) / BASELINE_FILE)
        if (not isinstance(baseline, dict) or baseline.get("run_id") != run.get("run_id")
                or baseline.get("digest") != inventory_digest(baseline)):
            errors.append("Не закреплён исходный снимок замечаний именно из исходного DWG")
        else:
            latest, journal_digest, _ = moves.journal(folder, baseline)
            _check_baseline(baseline, inventory, latest, journal_digest)
            _check_source_version(baseline)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append("Сохранённый снимок чтения слоя пояснений недоступен или повреждён: " + str(exc))
    return errors


def _check_baseline(baseline, inventory, layer_changes=None, journal_digest=""):
    if (baseline.get("source_path") != inventory.get("source_path")
            or baseline.get("run_id") != inventory.get("run_id")):
        raise ValueError("Исходный снимок замечаний относится к другому DWG или запуску")
    baseline_path = baseline.get("captured_document_path")
    if not baseline_path or Path(baseline_path).resolve() != Path(baseline["source_path"]).resolve():
        raise ValueError("Исходный снимок замечаний получен не из исходного DWG")
    original = {item["handle"].upper(): item for item in baseline["entities"]}
    layer_changes = layer_changes or {}
    current = {item["handle"].upper(): item for item in inventory["entities"] + inventory.get("tracked_dimensions", [])}
    if layer_changes:
        if inventory.get("dimension_layer_journal_digest") != journal_digest:
            raise ValueError("Снимок не соответствует независимому журналу изменений слоёв")
        evidence = inventory.get("dimension_layer_evidence", {})
        moves.validate_move_evidence(baseline, layer_changes, evidence)
        if any(current.get(handle) != evidence["dimensions"][handle] for handle in layer_changes):
            raise ValueError("Перенесённые размеры не совпадают с полным чтением DWG")
    elif inventory.get("tracked_dimensions") or inventory.get("dimension_layer_journal_digest"):
        raise ValueError("Нет независимого журнала разрешённых переносов размеров")
    missing = sorted(set(original) - set(current))
    changed = sorted(handle for handle in original.keys() & current.keys()
                     if original[handle] != current[handle] and not (
                         handle in layer_changes and moves.layer_only(original[handle], current[handle])))
    if missing or changed:
        raise ValueError("В копии отсутствуют или изменены исходные замечания/объекты слоя: "
                         + ", ".join(missing + changed))


def _invalidate(loaded, reason):
    registry, run = loaded["registry"], loaded["run"]
    registry.setdefault("dwg_actions", {})["work_completed"] = False
    for key in ("quality_control", "word_report"):
        if registry.get(key, {}).get("status") == "complete":
            registry[key]["status"] = "needs_attention"
    registry.setdefault("barrier_assessment", {})["status"] = "source_remarks_pending"
    run["status"] = "source_remarks_pending"
    registry["barrier_assessment"]["source_remarks_pending_reason"] = reason


def _save(store, loaded):
    folder = Path(loaded["folder"])
    write_json(folder / "РЕЕСТР_ПРОВЕРКИ.json", loaded["registry"])
    write_json(folder / "run.json", loaded["run"])
    store.refresh_project_history(loaded["run"]["project_slug"])


def capture(store, slug, run_id, required_document_path=None):
    loaded = store.load(slug, run_id)
    run = loaded["run"]
    if not applies(run):
        raise ValueError("Реестр замечаний слоя предназначен только для оценки преград")
    source_text = resolved_source_path(run, loaded["registry"])
    data = loaded["registry"].setdefault("barrier_assessment", {})
    previous = data.setdefault("source_remarks", new_source_remarks())
    try:
        with store.source_remarks_bridge_factory() as client:
            info = client.call("get_drawing_info")
            actual_text = info.get("path", "")
            if not isinstance(actual_text, str) or not Path(actual_text).is_absolute():
                raise ValueError("AUTOM8LABS не подтвердил полный путь активного DWG")
            actual = Path(actual_text).resolve()
            source = Path(source_text).resolve() if source_text else actual
            if not source_text and f"__{run['mode']}__{run_id}" in source.stem:
                raise ValueError("Первый снимок замечаний необходимо получить из открытого исходного DWG, а не из копии результата")
            file_before = _file_fingerprint(source)
            if required_document_path and actual != Path(required_document_path).resolve():
                raise ValueError("Для регистрации результата откройте именно сохраняемый DWG результата")
            allowed = [str(source)]
            if run.get("task_variant", "full_execution") == "full_execution":
                actions = loaded["registry"].get("dwg_actions", {})
                pinned = str(actions.get("result_copy_path", "")).strip()
                prefix = f"{source.stem}__{run['mode']}__{run_id}"
                if (actual.parent == source.parent and actual.suffix.lower() == ".dwg"
                        and (actual.stem.startswith(prefix) or (pinned and actual == Path(pinned).resolve()))):
                    allowed.append(str(actual))
            baseline = read_json(Path(loaded["folder"]) / BASELINE_FILE)
            latest, journal_digest, _ = moves.journal(loaded["folder"], baseline) if isinstance(baseline, dict) else ({}, "", 0)
            if baseline is None and actual != source:
                raise ValueError("Первый снимок замечаний необходимо получить именно из исходного DWG")
            if actual != source:
                if not isinstance(baseline, dict):
                    raise ValueError("Исходный снимок замечаний повреждён")
                _check_source_version(baseline, full=True)
            elif latest:
                raise ValueError("После зарегистрированного переноса размеров перечитывайте копию результата; исходный baseline не переписывается")
            if actual not in {Path(path).resolve() for path in allowed}:
                raise ValueError("Открыт другой DWG; чтение источников этого запуска запрещено")
            # Pin a single document for every read; switching to even another
            # otherwise permitted document must invalidate this capture.
            def extra_reader(bridge):
                return {**moves.read_move_evidence(bridge, baseline, latest),
                        "dimension_layer_journal_digest": journal_digest}
            inventory = capture_source_layer(client, str(source), [str(actual)],
                                             extra_reader=extra_reader if latest else None)
            if Path(inventory["captured_document_path"]).resolve() != actual:
                raise ValueError("Активный DWG сменился во время закрепления источников")
            inventory["run_id"] = run_id
            inventory["source_file_fingerprint"] = file_before
            if actual != source:
                if (not isinstance(baseline, dict) or baseline.get("digest") != inventory_digest(baseline)):
                    raise ValueError("Исходный снимок замечаний повреждён")
                _check_baseline(baseline, inventory, latest, journal_digest)
        if _file_fingerprint(source) != file_before:
            raise ValueError("Исходный файл DWG изменился во время чтения; перечитайте исходник")
        loaded["registry"].setdefault("dwg_actions", {})["source_path"] = str(source)
    except Exception as exc:
        previous["refresh_error"] = str(exc)
        _invalidate(loaded, "Не удалось полностью перечитать исходный слой пояснений")
        _save(store, loaded)
        raise
    old_inventory = previous.get("inventory", {})
    changed = old_inventory.get("digest") != inventory["digest"] or old_inventory.get("run_id") != run_id
    if changed:
        data.setdefault("source_remarks_history", []).append({
            "archived_at": utc_now(), "reason": "Обновление исходного снимка", "record": copy.deepcopy(previous),
            "previous_completion": {key: copy.deepcopy(loaded["registry"].get(key, {}))
                                    for key in ("dwg_actions", "quality_control", "word_report")},
        })
        previous = new_source_remarks()
        data["source_remarks"] = previous
        _invalidate(loaded, "Состав или содержимое слоя изменилось: решения требуется проверить заново")
    previous["inventory"] = inventory
    previous.pop("refresh_error", None)
    if actual == source:
        if baseline and baseline.get("digest") != inventory.get("digest"):
            data.setdefault("source_baseline_history", []).append(copy.deepcopy(baseline))
        write_json(Path(loaded["folder"]) / BASELINE_FILE, inventory)
    write_json(Path(loaded["folder"]) / INVENTORY_FILE, inventory)
    _save(store, loaded)
    errors = stored_errors(run, loaded["registry"], loaded["folder"])
    return {"run_id": run_id, "changed": changed, "inventory": copy.deepcopy(inventory),
            "errors": errors, "complete": not errors}


def record_decisions(store, slug, run_id, decisions, none_found_reason=None, delete_decision_ids=None):
    loaded = store.load(slug, run_id)
    if not applies(loaded["run"]):
        raise ValueError("Решения по замечаниям слоя записываются только для оценки преград")
    data = loaded["registry"].setdefault("barrier_assessment", {})
    record = data.setdefault("source_remarks", new_source_remarks())
    inventory = record.get("inventory", {})
    captured = read_json(Path(loaded["folder"]) / INVENTORY_FILE)
    if not captured or captured != inventory or inventory.get("status") != "complete":
        raise ValueError("Сначала выполните полное чтение слоя через capture_barrier_source_remarks")
    if not isinstance(decisions, list) or not all(isinstance(item, dict) for item in decisions):
        raise ValueError("decisions должен быть списком решений по замечаниям")
    ids = [item.get("id") for item in decisions]
    if (any(not isinstance(value, str) or not value.strip() for value in ids)
            or len({value.strip().casefold() for value in ids}) != len(ids)):
        raise ValueError("Каждому решению нужен уникальный непустой id")
    for item in decisions:
        if item.get("inventory_digest") != inventory.get("digest"):
            raise ValueError("Решение относится к другой версии исходного слоя")
    if none_found_reason is not None and not isinstance(none_found_reason, str):
        raise ValueError("none_found_reason должен быть текстовым обоснованием")
    deletes = [] if delete_decision_ids is None else delete_decision_ids
    if not isinstance(deletes, list) or any(not isinstance(value, str) or not value.strip() for value in deletes):
        raise ValueError("delete_decision_ids должен быть списком идентификаторов записей реестра")
    data.setdefault("source_remarks_history", []).append({
        "archived_at": utc_now(), "reason": "Обновление решений", "record": copy.deepcopy(record),
    })
    by_id = {item["id"].strip().casefold(): item for item in record.get("decisions", [])}
    for key in deletes:
        by_id.pop(key.strip().casefold(), None)
    for item in decisions:
        by_id[item["id"].strip().casefold()] = {**copy.deepcopy(item), "id": item["id"].strip()}
    record["decisions"] = list(by_id.values())
    if none_found_reason is not None:
        record["none_found_reason"] = none_found_reason.strip()
    _invalidate(loaded, "Решения изменены: требуется свежая итоговая проверка")
    _save(store, loaded)
    errors = stored_errors(loaded["run"], loaded["registry"], loaded["folder"])
    return {"run_id": run_id, "decision_count": len(by_id), "complete": not errors, "errors": errors}


def record_dimension_layer_changes(store, slug, run_id, changes):
    """Register an already performed layer-only delta after fresh native reads.

    No CAD write, document switch or save is performed. An already journaled
    handle may append fresh relationship evidence without another layer move.
    Unknown relationships block the exception; the original guard stays intact.
    """
    loaded = store.load(slug, run_id)
    run, registry, folder = loaded["run"], loaded["registry"], Path(loaded["folder"])
    if not applies(run) or run.get("task_variant", "full_execution") != "full_execution":
        raise ValueError("Перенос исходных обмеров доступен только для полной оценки преград в копии DWG")
    with normative_update_lock(folder / "dimension_layer_changes.lock"):
        baseline = read_json(folder / BASELINE_FILE)
        record = registry.get("barrier_assessment", {}).get("source_remarks", {})
        inventory = record.get("inventory", {})
        if (not isinstance(baseline, dict) or baseline.get("digest") != inventory_digest(baseline)
                or baseline.get("run_id") != run_id or read_json(folder / INVENTORY_FILE) != inventory
                or inventory.get("digest") != inventory_digest(inventory)):
            raise ValueError("Нужны неизменённый первичный снимок и независимая текущая инвентаризация")
        latest, previous_digest, count = moves.journal(folder, baseline)
        prepared = moves.prepare_changes(changes, baseline, inventory, record.get("decisions", []), latest)
        proposed = {**latest, **{item["handle"]: item for item in prepared}}
        _check_source_version(baseline, full=True)
        source = Path(baseline["source_path"]).resolve()
        actions = registry.get("dwg_actions", {})
        pinned = str(actions.get("result_copy_path", "")).strip()
        with store.source_remarks_bridge_factory() as client:
            actual = Path(client.call("get_drawing_info").get("path", "")).resolve()
            prefix = f"{source.stem}__{run['mode']}__{run_id}"
            if (actual == source or actual.parent != source.parent or actual.suffix.lower() != ".dwg"
                    or not (actual == Path(pinned).resolve() if pinned else actual.stem.startswith(prefix))):
                raise ValueError("Для журнала откройте единственную копию результата этого запуска")
            def extra_reader(bridge):
                return {**moves.read_move_evidence(bridge, baseline, proposed),
                        "dimension_layer_journal_digest": "pending"}
            verified = capture_source_layer(client, str(source), [str(actual)], extra_reader=extra_reader)
            verified["run_id"] = run_id
            _check_baseline(baseline, verified, proposed, "pending")
        _check_source_version(baseline, full=True)
        if (read_json(folder / BASELINE_FILE) != baseline or read_json(folder / INVENTORY_FILE) != inventory
                or read_json(folder / "РЕЕСТР_ПРОВЕРКИ.json") != registry
                or read_json(folder / "run.json") != run):
            raise ValueError("Исходный снимок или решения изменились во время проверки; повторите чтение")
        entry_digest = moves.append_journal(folder, {
            "run_id": run_id, "source_path": baseline["source_path"], "baseline_digest": baseline["digest"],
            "previous_digest": previous_digest, "created_at": utc_now(), "changes": prepared,
            "captured_document_path": str(actual), "evidence": verified["dimension_layer_evidence"],
        })
        registry.setdefault("barrier_assessment", {})["dimension_layer_change_journal"] = {
            "path": str(folder / moves.JOURNAL_FILE), "entry_count": count + 1, "digest": entry_digest}
        _invalidate(loaded, "Записан разрешённый перенос исходного размера: требуется свежий снимок и проверка решений")
        _save(store, loaded)
        return {"run_id": run_id, "journal_path": str(folder / moves.JOURNAL_FILE),
                "entry_digest": entry_digest, "change_count": len(prepared), "complete": False,
                "next_action": "capture_barrier_source_remarks; review decisions against the new inventory digest"}


def require_complete(store, slug, run_id, required_document_path=None):
    loaded = store.load(slug, run_id)
    if not applies(loaded["run"]):
        return
    # Fail quickly without touching CAD when the recorded work is already incomplete.
    errors = stored_errors(loaded["run"], loaded["registry"], loaded["folder"])
    if errors:
        raise ValueError("Не завершён обязательный контроль замечаний слоя пояснений:\n" + "\n".join(errors))
    if required_document_path is None and loaded["run"].get("task_variant", "full_execution") == "full_execution":
        actions = loaded["registry"].get("dwg_actions", {})
        if actions.get("result_copy_created") is True:
            required_document_path = str(actions.get("result_copy_path", "")).strip()
            if not required_document_path:
                raise ValueError("Не закреплён путь DWG результата для итогового чтения слоя")
    capture(store, slug, run_id, required_document_path)
    loaded = store.load(slug, run_id)
    errors = stored_errors(loaded["run"], loaded["registry"], loaded["folder"])
    if errors:
        raise ValueError("Слой пояснений изменился или не все замечания разрешены:\n" + "\n".join(errors))
