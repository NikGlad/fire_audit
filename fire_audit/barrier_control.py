"""Read-only presentation of the existing PP workflow for the Control tab."""
from pathlib import Path

from .barriers import MODE, preparation_errors
from .barrier_workflow import validate_plan
from .io_utils import read_json


STATUS_LABELS = {
    "not_prepared":"Решения ещё не подготовлены", "prepared":"План готов к просмотру",
    "building":"Построение выполняется", "built_needs_verification":"Копия построена; нужна сверка",
    "verified":"Последняя сверка графики пройдена", "verification_failed":"Есть ошибки сверки",
    "interrupted_requires_review":"Построение прервано; нужна проверка фактического результата",
}
KINDS = {"barrier":"Преграда", "category":"Категория", "callout":"Выноска", "dimension":"Размер", "legend":"Условное обозначение"}


def snapshot(app, slug, run_id):
    loaded=app.runs.load(slug,run_id)
    run=loaded["run"]; folder=Path(loaded["folder"])
    if run["mode"]!=MODE:
        raise ValueError("Выберите запуск «Оценка Преград»")
    state=read_json(folder / "ПЛАН_ПП.json", {})
    plan=state.get("plan", {}); project=run["project_snapshot"]
    status=app.barrier_workflow.status(slug,run_id)
    stale=list(status["errors"])
    blocking=[e for e in stale if not e.startswith("Файл результата изменён после построения")]
    inputs=preparation_errors(project,read_json(app.paths.knowledge / "barrier_drawing_profile.json", {}))
    issues=validate_plan(plan,project) if plan else ["Помощник ещё не подготовил решения и контуры помещений"]
    verification=state.get("verification") or {}
    errors=list(dict.fromkeys([*stale,*inputs,*issues,*verification.get("errors",[])]))
    if state.get("status")=="interrupted_requires_review" and state.get("error"):
        errors.append("Причина остановки построения: "+state["error"])
    prepared=bool(plan) and not issues
    built=state.get("status") in {"built_needs_verification","verified","verification_failed"}
    result_exists=bool(state.get("result_dwg")) and Path(state["result_dwg"]).is_file()
    if built and not result_exists:
        errors.append("Файл построенной копии не найден. Уточните его путь с помощником")
    complete=built and result_exists and state.get("status")=="verified" and verification.get("ok") is True and not errors
    steps=[]
    def step(key,title,done,detail,pending="Ожидается"):
        label="Требует обновления" if stale and done else "Выполнено" if done else pending
        steps.append({"id":key,"title":title,"status":label,"done":done and not stale,"detail":detail})
    step("inputs","Исходные данные ПП",not inputs,"\n".join(inputs) or "Категории, СТУ, документы и профиль оформления подготовлены.","Нужны вводные")
    step("decisions","Нормативные решения",prepared,"\n".join(issues) if issues else "Основания и специальные условия записаны в плане помощника. Применимость норм проверяет его автор.","Нужны решения")
    step("rooms","Помещения и границы",prepared,f"Помещений в плане: {len(plan.get('rooms',[]))}.\n"+("\n".join(issues) if issues else "Для каждой стороны записано решение; категории и проёмы проверены по плану."),"Нужны контуры")
    step("plan","План схемы и оформление",prepared,"План готов к просмотру перед построением." if prepared else "Подготовленный план появится после работы помощника.")
    step("build","Построение отдельной копии",built and result_exists,
         "\n".join(x for x in (state.get("result_dwg"),state.get("error")) if x) or "Копия ещё не построена.",
         "Копия не найдена" if built and not result_exists else STATUS_LABELS.get(state.get("status"),"Ожидается"))
    step("verify","Итоговая сверка схемы",complete,"\n".join(verification.get("errors",[])) or
         ("Последняя сверка: "+verification.get("checked_at","") if verification else "Сверка через AutoCAD ещё не выполнена."),"Нужна сверка")
    next_step=("Обновите задание по текущему паспорту и правилам." if blocking else
               "Заполните и сохраните вводные во вкладке «Дополнительные данные»." if inputs else
               "Передайте задание помощнику для подготовки решений и контуров." if not prepared else
               "Просмотрите «План схемы ПП» и постройте отдельную копию." if state.get("status")=="prepared" else
               "Построение выполняется. Дождитесь результата." if state.get("status")=="building" else
               "Проверьте фактический результат прерванного построения с помощником. Автоматическое повторение запрещено." if state.get("status")=="interrupted_requires_review" else
               "Последняя сверка пройдена. После изменений используйте «Проверить схему ПП»." if complete else
               "Откройте копию результата в AutoCAD и нажмите «Проверить схему ПП». Исправления согласуйте с помощником.")
    rows=plan.get("rooms") or project.get(MODE,{}).get("rooms",[])
    return {"scope":(slug,run_id), "project":project, "state":state, "plan":plan, "steps":steps,
            "rows":rows,"errors":errors,"stale":stale,"can_build":state.get("status")=="prepared" and not errors,
            "can_verify":built and not blocking and result_exists,"complete":complete,
            "summary":f"Оценка преград: этапов выполнено {sum(s['done'] for s in steps)} из {len(steps)}. " +
                      ("Задание или данные требуют обновления." if blocking else "Нужна повторная сверка изменённой копии." if stale else STATUS_LABELS.get(state.get("status","not_prepared"),"Нужна проверка состояния")),
            "next_step":next_step,"folder":folder}


def overview(data):
    plan=data["plan"]; state=data["state"]
    lines=[data["next_step"],"",f"Помещений: {len(data['rows'])}. Элементов схемы: {len(plan.get('items',[]))}."]
    if state.get("result_dwg"): lines.append("Копия DWG: "+state["result_dwg"])
    if data["errors"]: lines += ["", "Требует внимания:", *["• "+e for e in data["errors"]]]
    lines += ["", "Обновление вкладки читает сохранённый протокол. Для проверки текущего открытого DWG нажмите «Проверить схему ПП».",
              "Нормативные основания и полноту области проверки подтверждает помощник; графическое исполнение проверяет программа."]
    return "\n".join(lines)


def rooms_text(data):
    approved={str(r['floor'])+':'+str(r['number']):r for r in data["project"].get(MODE,{}).get("rooms",[])}
    lines=[data["plan"].get("scope",{}).get("description","Подготовленных контуров ещё нет; ниже помещения из паспорта."),""]
    for r in data["rows"]:
        key=r.get("key") or str(r.get("floor",""))+':'+str(r.get("number",""))
        info=approved.get(key,r)
        lines.append(f"{key} — {info.get('name','Помещение из модели помощника')}")
        if info.get("category"):lines.append("Категория: "+info["category"])
        if info.get("host_part_functional_class"):lines.append("Класс части: "+info["host_part_functional_class"])
        edges=r.get("edges",[])
        lines.append(f"Сторон с решениями: {len(edges)}. Проёмы: "+("проверены" if r.get("openings_reviewed") else "не подтверждены"))
        for index,edge in enumerate(edges,1):
            lines.append(f"  Сторона {index}: "+("преграда" if edge.get("decision")=="barrier" else "самостоятельная преграда не требуется")+". "+edge.get("basis","Основание не заполнено"))
        lines.append("")
    return "\n".join(lines) if data["rows"] else "Помещения ещё не заполнены. Откройте вводные ПП."


def plan_text(data):
    if not data["plan"]:return overview(data)
    plan=data["plan"]; legend={r["key"]:r for r in plan.get("legend",[])}
    lines=[plan["scope"]["description"],"Исходный DWG: "+data["state"]["source_path"],
           "Результат будет сохранён в отдельной копии.",""]
    for item in plan["items"]:
        entry=legend.get(item.get("legend_key"),{})
        lines.append(f"{item['id']}. {KINDS.get(item['kind'],item['kind'])}: "+(item.get("text") or entry.get("name") or item.get("room_name") or "по геометрии плана"))
        if item.get("room_key"):lines.append("Помещение: "+item["room_key"]+"; категория: "+item.get("category",""))
        if item.get("basis"):lines.append("Основание: "+item["basis"])
        if item.get("kind") in {"barrier","legend"}:lines.append(f"Цвет по легенде: {entry.get('color_index')}; вес линии: 50.")
        if item.get("existing_handle"):lines.append("Учитывается существующий объект без повторного нанесения.")
        lines.append("")
    lines += ["Во время построения не переключайте и не редактируйте активный DWG.","",overview(data)]
    return "\n".join(lines)
