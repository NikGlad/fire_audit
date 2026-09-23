from __future__ import annotations

import copy
import re
from datetime import date
from pathlib import Path
from typing import Any

from .io_utils import read_json, slugify, utc_now, write_json
from .paths import AppPaths


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


MANUAL_TEXT_FIELDS = (
    ("accessibility", "mgn_description", "mgn_present", "МГН"),
    ("accessibility", "m4_description", "m4_present", "МГН группы М4"),
    ("building_parts", "abk_description", "abk_present", "АБК"),
    ("fire_systems", "firefighter_lifts_description", "firefighter_lifts", "Лифты для пожарных"),
    ("evacuation", "stairs_evacuation_description", "all_stairs_are_evacuation", "Эвакуационные лестницы"),
    ("evacuation", "smoke_free_stairwells_description", "smoke_free_stairwells_present",
     "Наличие незадымляемых лестничных клеток"),
    ("provided_materials", "facades_description", "facades", "Фасады"),
    ("provided_materials", "sections_description", "sections", "Разрезы"),
    ("provided_materials", "roof_description", "roof", "Кровля"),
)


def manual_text_value(container: dict[str, Any], description_key: str, legacy_key: str) -> Any:
    if description_key in container:
        return container.get(description_key, "")
    legacy_value = container.get(legacy_key)
    if legacy_value is True:
        return "Да"
    if legacy_value is False:
        return "Нет"
    if legacy_value is None:
        return ""
    return legacy_value


class ProjectStore:
    def __init__(self, paths: AppPaths):
        self.paths = paths

    def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        for project_file in sorted(self.paths.projects.glob("*/project.json")):
            try:
                data = read_json(project_file, {})
            except (OSError, ValueError) as exc:
                projects.append(
                    {
                        "slug": project_file.parent.name,
                        "name": project_file.parent.name,
                        "error": str(exc),
                    }
                )
                continue
            projects.append(
                {
                    "slug": data.get("slug", project_file.parent.name),
                    "name": data.get("name", project_file.parent.name),
                    "gpzu": data.get("dates", {}).get("gpzu", ""),
                    "dwg": data.get("dwg", {}).get("path", ""),
                    "path": str(project_file),
                }
            )
        return projects

    def project_dir(self, slug: str) -> Path:
        safe = slugify(slug)
        path = (self.paths.projects / safe).resolve()
        if path.parent != self.paths.projects.resolve():
            raise ValueError("Недопустимое имя объекта")
        return path

    def load(self, slug: str) -> dict[str, Any]:
        path = self.project_dir(slug) / "project.json"
        data = read_json(path)
        if data is None:
            raise FileNotFoundError(f"Объект не найден: {slug}")
        return data

    def save(self, slug: str, data: dict[str, Any]) -> dict[str, Any]:
        safe = slugify(slug)
        data = copy.deepcopy(data)
        data["slug"] = safe
        geometry = data.setdefault("geometry", {})
        geometry.pop("fire_compartment_division_present", None)
        geometry.setdefault("fire_compartment_division_description", "")
        for section_name, description_key, legacy_key, _label in MANUAL_TEXT_FIELDS:
            section = data.setdefault(section_name, {})
            if description_key not in section:
                section[description_key] = manual_text_value(section, description_key, legacy_key)
            section.pop(legacy_key, None)
        data.setdefault("metadata", {})["updated_at"] = utc_now()
        errors, warnings = self.validate(data)
        if errors:
            raise ValueError("; ".join(errors))
        folder = self.project_dir(safe)
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "project.json", data)
        return {"project": data, "warnings": warnings}

    def create(self, name: str, slug: str | None = None) -> dict[str, Any]:
        template = read_json(self.paths.templates / "project_template.json", {})
        safe = slugify(slug or name)
        folder = self.project_dir(safe)
        if (folder / "project.json").exists():
            raise FileExistsError(f"Объект уже существует: {safe}")
        template["slug"] = safe
        template["name"] = name.strip() or safe
        template.setdefault("metadata", {})["created_at"] = utc_now()
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "project.json", template)
        (folder / "sources").mkdir(exist_ok=True)
        (folder / "runs").mkdir(exist_ok=True)
        return template

    def validate(self, data: dict[str, Any], require_ready: bool = False) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if not str(data.get("name", "")).strip():
            errors.append("Не заполнено название объекта")
        if not str(data.get("slug", "")).strip():
            errors.append("Не заполнен идентификатор объекта")

        dwg = data.get("dwg", {})
        if dwg.get("save_allowed"):
            errors.append("Постоянное разрешение на сохранение исходного DWG запрещено; результаты сохраняются только отдельными новыми файлами")
        if dwg.get("delete_allowed"):
            errors.append("Постоянное разрешение на удаление запрещено; оно задаётся только текущей командой")

        gpzu = data.get("dates", {}).get("gpzu", "")
        if gpzu:
            if not DATE_RE.match(gpzu):
                errors.append("Дата ГПЗУ должна иметь формат ГГГГ-ММ-ДД")
            else:
                try:
                    date.fromisoformat(gpzu)
                except ValueError:
                    errors.append("Дата ГПЗУ недействительна")
        elif require_ready:
            errors.append("Для запуска проверки требуется дата ГПЗУ")
        else:
            warnings.append("Дата ГПЗУ не заполнена")

        classification = data.get("classification", {})
        if not classification.get("functional_classes"):
            warnings.append("Не заполнен функциональный класс")
        if not classification.get("fire_resistance_degree"):
            warnings.append("Не заполнена степень огнестойкости")
        if not classification.get("constructive_fire_hazard_class"):
            warnings.append("Не заполнен класс конструктивной пожарной опасности")
        geometry = data.get("geometry", {})
        for key, label in (
            ("height_m", "Высота по п. 3.1 СП 1"),
            ("floor_to_ceiling_height_m", "Высота от пола до потолка для зданий Ф5"),
        ):
            value = geometry.get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
            ):
                errors.append(f"{label} должна быть положительным числом в метрах")
        if geometry.get("height_m") is None:
            warnings.append("Не заполнена высота по п. 3.1 СП 1")
        fire_compartment_division = geometry.get("fire_compartment_division_description", "")
        if not isinstance(fire_compartment_division, str):
            errors.append("Поле «Деление на пожарные отсеки» должно содержать текст")
        elif not fire_compartment_division.strip():
            warnings.append("Не заполнено деление на пожарные отсеки")
        for section_name, description_key, legacy_key, label in MANUAL_TEXT_FIELDS:
            section = data.get(section_name, {})
            value = manual_text_value(section, description_key, legacy_key)
            if not isinstance(value, str):
                errors.append(f"Поле «{label}» должно содержать текст")
            elif not value.strip():
                warnings.append(f"Не заполнено поле «{label}»")
        functional_classes = [
            str(value).upper().replace("F", "Ф")
            for value in classification.get("functional_classes", [])
        ]
        if (
            any(value.startswith("Ф5") for value in functional_classes)
            and geometry.get("floor_to_ceiling_height_m") is None
        ):
            warnings.append("Для здания Ф5 не заполнена высота от пола до потолка")
        if not dwg.get("path"):
            warnings.append("Путь к DWG не заполнен; его потребуется закрепить перед AutoCAD-операциями")
        return errors, warnings
