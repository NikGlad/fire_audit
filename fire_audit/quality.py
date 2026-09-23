"""Software safeguards and expert-labelled cases are reported separately."""
from __future__ import annotations

from .control_geometry import compare_measurement, finite, ray_origin, digest
from .io_utils import read_json, write_json, utc_now, slugify, sha256_file
from pathlib import Path


def compare_case(case, result):
    expected = case["expected"]
    errors = []
    if result.get("decision") != expected.get("decision"):
        errors.append("Ложное замечание" if result.get("decision") == "violation" else "Неверное решение/пропуск нарушения")
    if result.get("floor") != expected.get("floor"):
        errors.append("Неверный этаж")
    if set(result.get("target_ids", [])) != set(expected.get("target_ids", [])):
        errors.append("Неверная цель")
    if expected.get("measurement_mm") is not None:
        try:
            if abs(finite(result.get("measurement_mm")) - finite(expected["measurement_mm"])) > finite(case.get("tolerance_mm", 1)):
                errors.append("Неверный обмер")
        except ValueError:
            errors.append("Нет достоверного обмера")
    if result.get("unauthorized_changes") is not False:
        errors.append("Не подтверждено отсутствие несогласованных изменений")
    return {"case_id": case["id"], "ok": not errors, "errors": errors}


class QualityChecks:
    def __init__(self, paths):
        self.paths = paths

    def run_barriers(self):
        """Small PP software checks, separate from AR examples and CAD verification."""
        from .barriers import LAYER, category_label, covers_fire_resistance, validate_created_entity
        from .barrier_workflow import covers_edge
        from .barrier_dxf import equivalent
        legend=[{"key":"test","color_index":5}]
        good={"layer":LAYER,"colorIndex":5,"lineWeight":50}
        checks=[
            ("Подпись склада использует С",category_label("В2","Кладовая")=="В2С"),
            ("Подпись производства использует П",category_label("В2","Цех")=="В2П"),
            ("Явный вес 50 и цвет легенды проверяются",not validate_created_entity(good,"barrier",legend,"test")),
            ("Неверный цвет обнаруживается",bool(validate_created_entity({**good,"colorIndex":6},"barrier",legend,"test"))),
            ("Неверный слой обнаруживается",bool(validate_created_entity({**good,"layer":"0"},"barrier",legend,"test"))),
            ("Вес ByLayer не принимается вместо 50",bool(validate_created_entity({**good,"lineWeight":"ByLayer"},"barrier",legend,"test"))),
            ("Непокрытый участок границы обнаруживается",not covers_edge([{"points":[[0,0],[90,0]]}],[0,0],[100,0])),
            ("Большие минуты не заменяют недостающий признак R",not covers_fire_resistance({"E":60,"I":60},{"R":45,"E":45,"I":45})),
            ("Изменение координаты на 0,01 мм обнаруживается",not equivalent([1000.0],[1000.01])),
        ]
        return {"mode":"barrier_assessment", "software_checks":[{"name":name,"ok":ok} for name,ok in checks],
                "software_ok":all(ok for _,ok in checks),
                "notice":"Это программные проверки правил ПП. Они не являются нормативной оценкой здания и не заменяют кнопку «Проверить схему ПП», которая читает фактический результат AutoCAD."}

    def approve_case_from_ui(self, case):
        for key in ("id", "description", "source_dwg", "normative_basis", "expected", "expert_name"):
            if not case.get(key):
                raise ValueError(f"У эталонного примера отсутствует {key}")
        if slugify(case["id"]) != case["id"]:
            raise ValueError("Недопустимый id примера")
        source = Path(case["source_dwg"])
        if case["id"] == "replace_with_unique_case_id" or not source.is_file() or source.suffix.lower() != ".dwg":
            raise ValueError("Заполните шаблон реальными данными и укажите существующий исходный DWG")
        expected = case["expected"]
        if expected.get("decision") not in {"violation", "compliant", "not_applicable", "insufficient_data"} or not expected.get("floor"):
            raise ValueError("Нужны подтверждённые решение и этаж")
        if not isinstance(expected.get("target_ids"), list) or finite(case.get("tolerance_mm", 1)) < 0:
            raise ValueError("Неверные цели или допуск обмера")
        record = {**case, "approved_in_gui_at": utc_now(), "source_sha256": sha256_file(source)}
        folder = self.paths.data / "quality" / "cases"
        old = read_json(folder / (case["id"] + ".json"))
        if old:
            write_json(folder / "history" / (case["id"] + "_" + digest(old) + ".json"), old)
        write_json(folder / (case["id"] + ".json"), record)
        return record

    def record_result(self, case_id, result):
        if slugify(case_id) != case_id:
            raise ValueError("Недопустимый id")
        case = read_json(self.paths.data / "quality/cases" / (case_id + ".json"))
        if not case:
            raise ValueError("Нет утверждённого эталона")
        record = {"case_digest": digest(case), "result": result, "at": utc_now()}
        write_json(self.paths.data / "quality/results" / (case_id + ".json"), record)
        return compare_case(case, result)

    def run(self):
        software = []
        for name, value, limit, direction, expected in (
            ("Равенство пределу — не нарушение минимальной ширины", 1200, 1200, "below", False),
            ("Ширина больше требуемой — не нарушение", 1250, 1200, "below", False),
            ("Ширина меньше требуемой", 1190, 1200, "below", True),
            ("Превышение максимального расстояния", 7001, 7000, "above", True),
            ("Расстояние равно пределу", 7000, 7000, "above", False),
        ):
            software.append({"name": name, "ok": compare_measurement(value, limit, direction) == expected})
        software.append({"name": "Начало луча на внешней стороне полного габарита", "ok": ray_origin([0, 0, 10, 10], [[20, 5]]) == [10, 5, 0]})
        expert = []
        for path in sorted((self.paths.data / "quality/cases").glob("*.json")):
            case = read_json(path)
            record = read_json(self.paths.data / "quality/results" / path.name)
            source = Path(case["source_dwg"])
            if not source.is_file() or sha256_file(source) != case.get("source_sha256"):
                expert.append({"case_id": case["id"], "ok": False, "errors": ["Исходный файл эталона изменён или недоступен"]})
            elif not record or record["case_digest"] != digest(case):
                expert.append({"case_id": case["id"], "ok": False, "errors": ["Нет результата для актуальной версии эталона"]})
            else:
                expert.append(compare_case(case, record["result"]))
        return {"software_checks": software, "software_ok": all(c["ok"] for c in software),
                "expert_cases": expert, "expert_coverage_available": bool(expert),
                "expert_cases_ok": bool(expert) and all(c["ok"] for c in expert),
                "notice": "Числа здесь — синтетические проверки логики, не нормативные пределы для вашего здания. "
                          "Успешные программные тесты не подтверждают качество всей оценки. "
                          "Реальные примеры Храброво АБК автоматически эталонными не объявлялись."}
