from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from fire_audit.core import FireAuditApp
from fire_audit.io_utils import read_json, write_json
from mcp_server import MCPServer


SOURCE_ROOT = Path(__file__).resolve().parent.parent


class FireAuditTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "app"
        self.root.mkdir()
        for folder in ("config", "knowledge", "templates"):
            shutil.copytree(SOURCE_ROOT / folder, self.root / folder)
        (self.root / "projects").mkdir()
        (self.root / "inbox").mkdir()
        self.app = FireAuditApp(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_ready_project(self) -> dict:
        project = self.app.projects.create("Тестовый объект", "test")
        project["dates"]["gpzu"] = "2026-04-23"
        project["classification"]["functional_classes"] = ["Ф5.1"]
        project["classification"]["fire_resistance_degree"] = "IV"
        project["classification"]["constructive_fire_hazard_class"] = "С1"
        return self.app.projects.save("test", project)["project"]

    def test_projects_are_listed_by_display_name_in_russian_alphabetical_order(self) -> None:
        for name, slug in (
            ("Ясень", "a"), ("Ёж", "z"), ("Абрикос", "m"),
            ("Жук", "b"), ("Ель", "q"),
        ):
            self.app.projects.create(name, slug)
        self.assertEqual(
            [item["name"] for item in self.app.projects.list_projects()],
            ["Абрикос", "Ель", "Ёж", "Жук", "Ясень"],
        )

    def test_dated_project_change_is_shared_across_work_modes(self) -> None:
        self.create_ready_project()
        event = self.app.projects.add_change(
            "test", event_date="2026-10-05", category="Планы",
            description="Изменены двери второго этажа", affected_scope="План второго этажа",
            source_reference="Письмо заказчика от 05.10.2026", impact_notes="Проверить выходы и пути")
        self.assertEqual(event["event_date"], "2026-10-05")
        self.assertTrue(event["recorded_at"])
        self.assertEqual(self.app.projects.load("test")["change_log"], [event])
        first = self.app.runs.create("test", "actualization_protocol")
        second = self.app.runs.create("test", "compliance")
        for result in (first, second):
            self.assertEqual(result["run"]["project_change_log_snapshot"], [event])
            task = Path(result["task_file"]).read_text(encoding="utf-8")
            self.assertIn("Датированная история изменений объекта", task)
            self.assertIn("Изменены двери второго этажа", task)
            self.assertIn("Письмо заказчика от 05.10.2026", task)
        later = self.app.projects.add_change(
            "test", event_date="2026-10-06", category="СТУ", description="Уточнено специальное условие",
            source_reference="СТУ, редакция 06.10.2026")
        self.assertEqual(len(self.app.projects.load("test")["change_log"]), 2)
        self.assertEqual(first["run"]["project_change_log_snapshot"], [event])
        self.assertNotEqual(event["id"], later["id"])
        stale_passport = self.app.projects.load("test")
        stale_passport["change_log"] = [later]
        with self.assertRaisesRegex(ValueError, "Историю изменений нельзя удалить"):
            self.app.projects.save("test", stale_passport)
        with self.assertRaisesRegex(ValueError, "недействительная дата"):
            self.app.projects.add_change(
                "test", event_date="2026-02-30", category="Планы",
                description="Ошибка", source_reference="Письмо")

    def test_one_change_can_have_multiple_kinds_and_legacy_kind_remains_readable(self) -> None:
        project = self.create_ready_project()
        legacy = {
            "id": "legacy-change", "event_date": "2026-09-25",
            "recorded_at": "2026-09-25T10:00:00+00:00", "category": "Планы",
            "description": "Прежняя запись", "source_reference": "Письмо заказчика",
        }
        project["change_log"] = [legacy]
        self.app.projects.save("test", project)
        item = self.app.projects.add_change(
            "test", event_date="2026-10-05", categories=["Планы", "Вводные", "СТУ"],
            description="Изменены планы и условия", source_reference="Комплект от заказчика")
        self.assertEqual(item["categories"], ["Планы", "Вводные", "СТУ"])
        self.assertEqual(item["category"], "Планы")
        self.assertEqual(self.app.projects.load("test")["change_log"], [legacy, item])
        task = self.app.runs.create("test", "actualization_protocol")
        task_text = Path(task["task_file"]).read_text(encoding="utf-8")
        self.assertIn('"categories": [', task_text)
        self.assertIn('"СТУ"', task_text)
        with self.assertRaisesRegex(ValueError, "один или несколько"):
            self.app.projects.add_change(
                "test", event_date="2026-10-05", categories=[],
                description="Без вида", source_reference="Письмо")
        with self.assertRaisesRegex(ValueError, "один или несколько"):
            self.app.projects.add_change(
                "test", event_date="2026-10-05", categories=["Планы", "Планы"],
                description="Повтор", source_reference="Письмо")

    def set_valid_ray_origin(self, finding: dict, plan_position: str = "right") -> None:
        side_and_point = {
            "right": ("right", [100.0, 50.0]),
            "left": ("left", [0.0, 50.0]),
            "above": ("top", [50.0, 100.0]),
            "below": ("bottom", [50.0, 0.0]),
            "diagonal": ("right", [100.0, 75.0]),
        }
        side, point = side_and_point[plan_position]
        finding["autocad"]["ray_origin"].update(
            {
                "rule": "outermost_group_point_toward_plan",
                "coordinates": point,
                "full_group_bounds": [0.0, 0.0, 100.0, 100.0],
                "plan_position_relative_to_group": plan_position,
                "side_toward_plan": side,
                "bounds_checked": True,
                "bounds_check_result": "verified",
                "path_clear_of_group": True,
            }
        )

    def set_complete_individual_analysis(self, finding: dict, reasoning: str | None = None) -> None:
        finding.setdefault("analysis", {})["source_reading"] = {
            "required_documents": [{"relative_path": "рабочая/тест.pdf", "source_sha256": "a" * 64,
                                    "pages_read": [1], "full_context_read": True, "unresolved_pages": [],
                                    "ocr_used": False, "visual_review_required": False}],
            "detected_letter_references": [], "letter_checks": [],
            "norm_comments_letters_separated": True,
            "order_2594_check": {"status": "included", "source_file": "перечень.pdf", "exact_item": "тестовый пункт"}
        }
        finding_id = finding.get("id") or "finding"
        finding["source_text_id"] = f"SOURCE-TEXT-{finding_id}"
        finding["norm"].update(
            {
                "edition": "редакция на дату ГПЗУ",
                "subpoint": "не применимо",
                "table": "не применимо",
                "notes": "примечания проверены, применимых нет",
                "pages_read": [1],
                "requirement_summary": f"Индивидуально проверено требование {finding['norm'].get('point')}",
                "related_provisions_read": ["область применения", "определения", "соседние пункты"],
            }
        )
        finding["applicability"].update(
            {
                "regulated_subject": "конкретный предмет текущего замечания",
                "established_scope": "сопоставлено с классом, этажом и назначением объекта",
                "mgn_dependency": "does_not_depend",
                "exceptions_checked": ["применимые исключения отсутствуют"],
                "alternative_solutions_checked": ["допустимые альтернативы проверены"],
            }
        )
        finding["measurement"]["result_summary"] = "Обмер выполнен либо установлено, что для этого требования он не требуется"
        finding["analysis"].update(
            {
                "decision_method": "individual_normative_analysis",
                "automatic_decision_used": False,
                "individual_analysis_complete": True,
                "facts_complete": True,
                "checked_project_materials": ["план", "экспликация"],
                "checked_autocad_entity_ids": [f"CHECKED-{finding_id}"],
                "object_facts": [
                    {
                        "fact": f"Факт объекта для {finding_id}",
                        "source_type": "drawing",
                        "source_reference": f"AutoCAD CHECKED-{finding_id}",
                    }
                ],
                "comparison_result": f"Фактические данные сопоставлены с требованием для {finding_id}",
                "individual_reasoning": reasoning or f"Индивидуальное обоснование решения для {finding_id}",
            }
        )
        finding["analysis"]["second_pass"].update(
            {
                "completed": True,
                "norm_reopened": True,
                "number_and_text_verified": True,
                "scope_and_exceptions_verified": True,
                "project_facts_verified": True,
                "reasoning_specific_verified": True,
                "no_copied_explanation_verified": True,
            }
        )

    def set_valid_compliance_source_copy(self, finding: dict, *, repeated_floor: bool = False) -> None:
        source_group = finding["autocad"]["source_group"]
        purple_present = source_group.get("purple_explanation_present") is True
        purple_ids = list(source_group.get("purple_explanation_entity_ids", []))
        source_group.update(
            {
                "source_template_key": "approved-template-4.2.1",
                "approved_block_header_entity_id": "BLOCK-HEADER-1",
                "left_explanation_checked": True,
                "left_explanation_position_verified": True,
                "left_explanation_present": purple_present,
                "left_explanation_entity_ids": purple_ids if purple_present else [],
                "copy_case": "repeated_floor" if repeated_floor else "first_occurrence",
                "copy_source": (
                    "previous_actualized_compliance_group" if repeated_floor else "source_approved_block"
                ),
                "copy_method": "copy_existing_complete_group",
                "manual_text_creation_used": False,
                "source_floor": "1 этаж" if repeated_floor else "зона утверждённых блоков",
                "new_floor": finding["autocad"].get("floor", ""),
                "previous_actualized_group_finding_id": "previous-floor-finding" if repeated_floor else "",
                "source_group_fully_detected": True,
                "left_explanation_copied_when_present": True,
                "text_styles_preserved": True,
                "relative_positions_preserved": True,
                "repeated_floor_group_copied_from_actualized_group": True,
            }
        )
        text_ids = (
            list(source_group.get("remark_text_entity_ids", []))
            + list(source_group.get("number_entity_ids", []))
            + list(source_group.get("left_explanation_entity_ids", []))
        )
        source_group["source_text_styles"] = [
            {"entity_id": entity_id, "text_style": "Standard"} for entity_id in text_ids
        ]

    def test_knowledge_excludes_lisp_as_engine(self) -> None:
        context = self.app.knowledge.context_for_mode("compliance")
        self.assertIn("Блок — смысловая группа", context)
        self.assertIn("LISP-файлы", context)
        self.assertIn("одну любую фактически существующую двупольную дверь на первом этаже", context)
        self.assertIn("Новый MText вместо копирования исходного текста создавать запрещено", context)
        self.assertIn("previous_actualized_compliance_group", context)
        self.assertNotIn("DIM_LT_NEAR_PLINE", context)

    def test_modes_keep_drawing_sources_separate(self) -> None:
        stage = self.app.knowledge.context_for_mode("stage")
        compliance = self.app.knowledge.context_for_mode("compliance")
        article89 = self.app.knowledge.context_for_mode("article89")
        self.assertIn("не перемещать и не переставлять", stage)
        self.assertIn("скопировать всю неделимую исходную группу", compliance)
        self.assertNotIn("переместить к соответствующему плану всю неделимую группу", compliance)
        self.assertIn("Сохранение оригинала в блоке после выдачи его копии не является отказом", compliance)
        self.assertNotIn("у каждого неперенесённого исходного элемента", compliance)
        self.assertIn("ч.3 фз 123", article89)
        self.assertIn("для каких этажей", article89)

    def test_full_document_search_regulation_is_in_every_mode(self) -> None:
        from fire_audit.knowledge import MODE_FILES
        for mode in MODE_FILES:
            context = self.app.knowledge.context_for_mode(mode)
            self.assertEqual(context.count("===== 14_РЕГЛАМЕНТ_ПОИСКА_ДОКУМЕНТОВ.md ====="), 1)
            self.assertIn("read_norm_document", context)
            self.assertIn("find_vniipo_letters", context)

    def test_article89_sanitary_exclusion_is_shared_but_not_a_general_exemption(self) -> None:
        from fire_audit.knowledge import MODE_FILES
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.app.knowledge.context_for_mode(mode)
                self.assertIn("душевые, ванные, туалеты и санузлы не проверять по статье 89", context)
                self.assertIn("не проверялось по указанию пользователя", context)
                self.assertIn("не отменяет проверок по другим применимым требованиям", context)
                self.assertIn("не исключать автоматически преддушевые", context)
        self.create_ready_project()
        for mode in ("article89", "compliance", "actualization_protocol"):
            with self.subTest(run_mode=mode):
                run = self.app.runs.create("test", mode)["run"]
                constraints = run["hard_constraints"]
                self.assertEqual(constraints["article89_excluded_initial_room_purposes"],
                                 ["душевая", "ванная", "туалет", "санузел"])
                self.assertTrue(constraints["article89_exclusion_is_user_scope_not_normative_exemption"])
                self.assertTrue(constraints["article89_exclusion_does_not_apply_to_other_requirements"])
                self.assertTrue(constraints["article89_excluded_rooms_preserve_transit_topology"])

    def test_article89_task_uses_confirmed_passport_stair_status(self) -> None:
        project = self.create_ready_project()
        project["evacuation"]["stairs_evacuation_description"] = "Все лестницы считать эвакуационными"
        self.app.projects.save("test", project)

        result = self.app.runs.create("test", "article89")
        run = result["run"]
        constraints = run["hard_constraints"]
        self.assertTrue(constraints["confirmed_passport_positions_control_object_facts"])
        self.assertTrue(constraints["object_specific_user_positions_override_generic_defaults"])
        self.assertTrue(constraints["article89_passport_stair_evacuation_status_controls_type_e"])

        task = Path(result["task_file"]).read_text(encoding="utf-8")
        self.assertIn(
            "Принятое положение об эвакуационных лестницах: Все лестницы считать эвакуационными",
            task,
        )
        self.assertIn("в том числе открытую лестницу 2-го типа", task)
        self.assertIn("типовыми допущениями общих инструкций", task)

    def test_actualization_protocol_uses_approved_rules_and_preserves_scope(self) -> None:
        self.create_ready_project()
        context = self.app.knowledge.context_for_mode("actualization_protocol")
        self.assertIn("проверять изменённые места и всё, на что эти изменения могут повлиять", context.lower())
        self.assertIn("Обратный перенос ошибочно закрытого нарушения", context)
        self.assertIn("экспертиза устранено протокол", context)
        self.assertIn("экспертиза устранено", context)
        result = self.app.runs.create("test", "actualization_protocol")
        run = result["run"]
        self.assertEqual(run["mode_label"], "Актуализация")
        self.assertTrue(run["hard_constraints"]["workflow_instructions_ready"])
        self.assertTrue(run["hard_constraints"]["autocad_actions_enabled"])
        self.assertFalse(run["hard_constraints"]["actualization_existing_findings_only"])
        self.assertTrue(run["hard_constraints"]["actualization_unified_review"])
        self.assertTrue(run["hard_constraints"]["actualization_deduplicate_existing_targets"])
        self.assertTrue(run["hard_constraints"]["actualization_all_targets_required_to_resolve_group"])
        self.assertTrue(run["hard_constraints"]["actualization_fire_safety_frame_excluded"])
        self.assertEqual(run["hard_constraints"]["actualization_resolved_layer_color"], 3)
        task_text = Path(result["task_file"]).read_text(encoding="utf-8")
        self.assertIn("экспертиза устранено", task_text)
        self.assertIn("сохранять номера", task_text)

    def test_ar_assessment_transfer_mode_is_registered_and_safe_until_instruction(self) -> None:
        self.create_ready_project()
        context = self.app.knowledge.context_for_mode("ar_assessment_transfer")
        self.assertIn("Алгоритм этого вида проверки ещё не передан", context)
        result = self.app.runs.create("test", "ar_assessment_transfer")
        run = result["run"]
        self.assertEqual(run["mode_label"], "Перенос оценки АР")
        self.assertFalse(run["hard_constraints"]["workflow_instructions_ready"])
        self.assertFalse(run["hard_constraints"]["autocad_actions_enabled"])
        task_text = Path(result["task_file"]).read_text(encoding="utf-8")
        self.assertIn("не выполнять анализ, не переносить графику и не изменять AutoCAD", task_text)

    def test_ar_plan_preparation_mode_is_registered_and_safe_until_instruction(self) -> None:
        self.create_ready_project()
        context = self.app.knowledge.context_for_mode("ar_plan_preparation")
        self.assertIn("Алгоритм этого вида проверки ещё не передан", context)
        result = self.app.runs.create("test", "ar_plan_preparation")
        run = result["run"]
        self.assertEqual(run["mode_label"], "Подготовка планов АР")
        self.assertFalse(run["hard_constraints"]["workflow_instructions_ready"])
        self.assertFalse(run["hard_constraints"]["autocad_actions_enabled"])
        task_text = Path(result["task_file"]).read_text(encoding="utf-8")
        self.assertIn("не выполнять анализ, не подготавливать планы и не изменять AutoCAD", task_text)

    def test_stage_context_contains_detailed_instruction_matrix(self) -> None:
        stage = self.app.knowledge.context_for_mode("stage")
        self.assertIn("более 40% площади здания", stage)
        self.assertIn("стеллажах выше 12,5 м", stage)
        self.assertIn("площадь лестничных клеток не включать", stage.lower())
        self.assertIn("не задавать общий вопрос", stage)
        self.assertIn("Внешнее письмо отправлять только по прямой команде", stage)
        self.assertIn("Запрос о наличии холодильников и охлаждаемых помещений выдавать всегда", stage)
        self.assertIn("варианты решения сохранять", stage)
        self.assertIn("выделить красным", stage)

    def test_ar_plan_comparison_is_registered_and_waits_for_instructions(self) -> None:
        from cli import parser
        from mcp_server import TOOLS, SERVER_INSTRUCTIONS
        from fire_audit.knowledge import MODE_FILES
        from fire_audit.runs import MODE_LABELS, MODE_STEPS, PENDING_INSTRUCTION_MODES

        mode = "ar_plan_comparison"
        self.create_ready_project()
        self.assertEqual(parser().parse_args(["create-run", "test", mode]).mode, mode)
        self.assertIn(mode, PENDING_INSTRUCTION_MODES)
        configured = read_json(self.root / "config/app.json")["supported_modes"]
        self.assertEqual(set(configured), set(MODE_LABELS))
        self.assertEqual(set(MODE_LABELS), set(MODE_STEPS))
        self.assertEqual(set(MODE_LABELS), set(MODE_FILES))
        for name in ("get_project_context", "create_run"):
            schema = next(t for t in TOOLS if t["name"] == name)["inputSchema"]
            self.assertEqual(set(schema["properties"]["mode"]["enum"]), set(MODE_LABELS))
        self.assertIn("Сравнение планов АР", SERVER_INSTRUCTIONS)

        context = self.app.knowledge.context_for_mode(mode)
        self.assertIn("Алгоритм этого вида проверки ещё не передан", context)
        self.assertIn("15_СРАВНЕНИЕ_ПЛАНОВ_АР.md", context)
        self.assertIn("14_РЕГЛАМЕНТ_ПОИСКА_ДОКУМЕНТОВ.md", context)
        result = self.app.runs.create("test", mode)
        run = result["run"]
        self.assertEqual(run["mode_label"], "Сравнение планов АР")
        self.assertFalse(run["hard_constraints"]["workflow_instructions_ready"])
        self.assertFalse(run["hard_constraints"]["autocad_actions_enabled"])
        self.assertIn(mode, run["run_id"])
        task = Path(result["task_file"]).read_text(encoding="utf-8")
        self.assertIn("не выполнять сравнение, не создавать графику, не изменять и не сохранять DWG", task)
        self.assertEqual(self.app.runs.load("test", run["run_id"])["registry"]["findings"], [])
        validation = self.app.runs.validate("test", run["run_id"], for_application=True)
        self.assertFalse(validation["ok"])
        self.assertTrue(any("алгоритм ещё не утверждён" in error for error in validation["errors"]))

    def test_project_rejects_persistent_save_permission(self) -> None:
        project = self.create_ready_project()
        project["dwg"]["save_allowed"] = True
        with self.assertRaises(ValueError):
            self.app.projects.save("test", project)

    def test_protocol_is_separate_and_waits_for_instructions(self) -> None:
        from cli import parser
        from fire_audit.runs import MODE_LABELS, PENDING_INSTRUCTION_MODES

        self.create_ready_project()
        self.assertEqual(parser().parse_args(["create-run", "test", "protocol"]).mode, "protocol")
        self.assertEqual(MODE_LABELS["protocol"], "Протокол")
        self.assertEqual(MODE_LABELS["actualization_protocol"], "Актуализация")
        self.assertIn("protocol", PENDING_INSTRUCTION_MODES)
        context = self.app.knowledge.context_for_mode("protocol")
        self.assertIn("16_ПРОТОКОЛ.md", context)
        self.assertIn("Алгоритм этого вида проверки ещё не передан", context)
        self.assertIn("14_РЕГЛАМЕНТ_ПОИСКА_ДОКУМЕНТОВ.md", context)
        self.assertNotIn("===== 11_АКТУАЛИЗАЦИЯ.md =====", context)
        protocol = self.app.runs.create("test", "protocol")
        actualization = self.app.runs.create("test", "actualization_protocol")
        self.assertNotEqual(protocol["folder"], actualization["folder"])
        self.assertNotEqual(protocol["registry_file"], actualization["registry_file"])
        run = protocol["run"]
        self.assertEqual(run["mode_label"], "Протокол")
        self.assertFalse(run["hard_constraints"]["workflow_instructions_ready"])
        self.assertFalse(run["hard_constraints"]["autocad_actions_enabled"])
        self.assertEqual(self.app.runs.load("test", run["run_id"])["registry"]["findings"], [])
        task = Path(protocol["task_file"]).read_text(encoding="utf-8")
        self.assertIn("не выполнять проверку, не составлять протокол, не изменять и не сохранять DWG", task)
        result = self.app.runs.validate("test", run["run_id"], for_application=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("алгоритм ещё не утверждён" in e for e in result["errors"]))

    def test_du_mode_waits_for_its_algorithm(self) -> None:
        from cli import parser
        from mcp_server import TOOLS, SERVER_INSTRUCTIONS
        from fire_audit.knowledge import MODE_FILES
        from fire_audit.runs import MODE_LABELS, MODE_STEPS, PENDING_INSTRUCTION_MODES

        project = self.create_ready_project()
        cases = [
            ("du_assessment", "Оценка ДУ", "19_ОЦЕНКА_ДУ.md"),
        ]
        configured = read_json(self.root / "config/app.json")["supported_modes"]
        self.assertEqual(set(configured), set(MODE_LABELS))
        self.assertEqual(set(MODE_LABELS), set(MODE_STEPS))
        self.assertEqual(set(MODE_LABELS), set(MODE_FILES))
        for name in ("get_project_context", "create_run"):
            schema = next(t for t in TOOLS if t["name"] == name)["inputSchema"]
            self.assertEqual(set(schema["properties"]["mode"]["enum"]), set(MODE_LABELS))

        folders, registries = set(), set()
        for mode, label, instruction in cases:
            with self.subTest(mode=mode):
                self.assertEqual(parser().parse_args(["create-run", "test", mode]).mode, mode)
                self.assertEqual(MODE_LABELS[mode], label)
                self.assertIn(mode, PENDING_INSTRUCTION_MODES)
                self.assertIn(label, SERVER_INSTRUCTIONS)
                context = self.app.knowledge.context_for_mode(mode)
                self.assertIn(instruction, context)
                self.assertIn("Алгоритм этого вида проверки ещё не передан", context)
                for other_mode, _, other_instruction in cases:
                    if other_mode != mode:
                        self.assertNotIn(other_instruction, context)
                result = self.app.runs.create("test", mode)
                folders.add(result["folder"])
                registries.add(result["registry_file"])
                run = result["run"]
                self.assertEqual(run["mode_label"], label)
                self.assertFalse(run["hard_constraints"]["workflow_instructions_ready"])
                self.assertFalse(run["hard_constraints"]["autocad_actions_enabled"])
                task = Path(result["task_file"]).read_text(encoding="utf-8")
                self.assertIn(label, task)
                self.assertIn("не выполнять проверку, не создавать графику, не изменять и не сохранять DWG", task)
                loaded = self.app.runs.load("test", run["run_id"])
                self.assertEqual(loaded["registry"]["findings"], [])
                validation = self.app.runs.validate("test", run["run_id"], for_application=True)
                self.assertFalse(validation["ok"])
                self.assertTrue(any("алгоритм ещё не утверждён" in error for error in validation["errors"]))
                self.assertNotIn("prepare_cad_plan", task)
        self.assertEqual(len(folders), 1)
        self.assertEqual(len(registries), 1)
        self.assertEqual(self.app.projects.load("test"), project)

    def test_project_height_fields_are_validated_and_passed_to_task(self) -> None:
        project = self.create_ready_project()
        _, missing_warnings = self.app.projects.validate(project, require_ready=True)
        self.assertTrue(any("высота по п. 3.1" in value.lower() for value in missing_warnings))
        self.assertTrue(
            any("ф5" in value.lower() and "от пола до потолка" in value.lower() for value in missing_warnings)
        )

        project["geometry"]["height_m"] = 12.4
        project["geometry"]["floor_to_ceiling_height_m"] = 3.6
        saved = self.app.projects.save("test", project)["project"]
        errors, warnings = self.app.projects.validate(saved, require_ready=True)
        self.assertFalse(errors)
        self.assertFalse(any("высота по п. 3.1" in value.lower() for value in warnings))
        self.assertFalse(any("ф5" in value.lower() and "от пола до потолка" in value.lower() for value in warnings))

        run = self.app.runs.create("test", "compliance")
        task_text = Path(run["task_file"]).read_text(encoding="utf-8")
        self.assertIn("Высота по п. 3.1 СП 1: 12.4 м", task_text)
        self.assertIn("Высота от пола до потолка для зданий Ф5: 3.6 м", task_text)

        saved["geometry"]["height_m"] = -1
        invalid_errors, _ = self.app.projects.validate(saved)
        self.assertTrue(any("положительным числом" in value for value in invalid_errors))

    def test_height_explanations_are_preserved_in_passport_and_task(self) -> None:
        from fire_audit.projects import height_for_task
        project = self.create_ready_project()
        height = 'не более 28 м; по п.3.1 СП1.'
        ceiling = '3,2–4.5 м, в производственной части; уточняется.'
        project['geometry'].update(height_m=height, floor_to_ceiling_height_m=ceiling)
        saved = self.app.projects.save('test', project)['project']
        errors, warnings = self.app.projects.validate(saved)
        self.assertFalse(errors)
        self.assertFalse(any('высота' in text.lower() for text in warnings))
        run = self.app.runs.create('test', 'compliance')
        task = Path(run['task_file']).read_text(encoding='utf-8')
        self.assertIn('Высота по п. 3.1 СП 1: ' + height + '\n', task)
        self.assertIn('Высота от пола до потолка для зданий Ф5: ' + ceiling + '\n', task)
        for value, expected in [(None, 'не заполнено'), ('  ', 'не заполнено'),
                                ('12,4', '12,4 м'), (3.6, '3.6 м'),
                                ('уточняется', 'уточняется')]:
            self.assertEqual(height_for_task(value), expected)
        saved['geometry'].update(height_m='', floor_to_ceiling_height_m='  ')
        errors, warnings = self.app.projects.validate(saved)
        self.assertFalse(errors)
        self.assertTrue(any('Не заполнена высота по' in text for text in warnings))
        self.assertTrue(any('не заполнена высота от пола' in text for text in warnings))

    def test_fire_compartment_division_description_is_stored_and_passed_to_task(self) -> None:
        project = self.create_ready_project()
        self.assertEqual(project["geometry"].get("fire_compartment_division_description"), "")
        _, warnings = self.app.projects.validate(project, require_ready=True)
        self.assertTrue(any("деление на пожарные отсеки" in value.lower() for value in warnings))

        description = "Здание делится на 2 пожарных отсека по осям 1–8 и 8–15"
        project["geometry"]["fire_compartment_division_description"] = description
        saved = self.app.projects.save("test", project)["project"]
        self.assertEqual(saved["geometry"]["fire_compartment_division_description"], description)
        run = self.app.runs.create("test", "compliance")
        task_text = Path(run["task_file"]).read_text(encoding="utf-8")
        self.assertIn(f"Деление на пожарные отсеки: {description}", task_text)

        saved["geometry"]["fire_compartment_division_description"] = ["неверный формат"]
        invalid_errors, _ = self.app.projects.validate(saved)
        self.assertTrue(any("должно содержать текст" in value for value in invalid_errors))

        saved["geometry"]["fire_compartment_division_description"] = description
        saved["geometry"]["fire_compartment_division_present"] = True
        migrated = self.app.projects.save("test", saved)["project"]
        self.assertNotIn("fire_compartment_division_present", migrated["geometry"])

    def test_remaining_passport_questions_use_manual_text_and_migrate_legacy_values(self) -> None:
        project = self.create_ready_project()
        manual_values = {
            ("accessibility", "mgn_description"): "МГН предусмотрены на первом этаже",
            ("accessibility", "m4_description"): "МГН группы М4 не предусматриваются, запрос выполнить",
            ("building_parts", "abk_description"): "Есть две встройки АБК в осях 1–4 и 8–11",
            ("fire_systems", "firefighter_lifts_description"): "Лифты для пожарных не предусмотрены",
            ("evacuation", "stairs_evacuation_description"): "Все лестницы считать эвакуационными",
            ("evacuation", "smoke_free_stairwells_description"): "Предусмотрены две лестничные клетки типа Н2",
            ("provided_materials", "facades_description"): "Фасады представлены",
            ("provided_materials", "sections_description"): "Представлены два разреза",
            ("provided_materials", "roof_description"): "План кровли представлен",
        }
        for (section_name, key), value in manual_values.items():
            project[section_name][key] = value
        saved = self.app.projects.save("test", project)["project"]
        for (section_name, key), value in manual_values.items():
            self.assertEqual(saved[section_name][key], value)

        run = self.app.runs.create("test", "stage")
        task_text = Path(run["task_file"]).read_text(encoding="utf-8")
        for value in manual_values.values():
            self.assertIn(value, task_text)

        legacy = copy.deepcopy(saved)
        migrations = (
            ("accessibility", "mgn_description", "mgn_present", True, "Да"),
            ("accessibility", "m4_description", "m4_present", False, "Нет"),
            ("building_parts", "abk_description", "abk_present", True, "Да"),
            ("fire_systems", "firefighter_lifts_description", "firefighter_lifts", False, "Нет"),
            ("evacuation", "stairs_evacuation_description", "all_stairs_are_evacuation", True, "Да"),
            ("evacuation", "smoke_free_stairwells_description", "smoke_free_stairwells_present", True, "Да"),
            ("provided_materials", "facades_description", "facades", True, "Да"),
            ("provided_materials", "sections_description", "sections", False, "Нет"),
            ("provided_materials", "roof_description", "roof", None, ""),
        )
        for section_name, description_key, legacy_key, legacy_value, _expected in migrations:
            legacy[section_name].pop(description_key, None)
            legacy[section_name][legacy_key] = legacy_value
        migrated = self.app.projects.save("test", legacy)["project"]
        for section_name, description_key, legacy_key, _legacy_value, expected in migrations:
            self.assertEqual(migrated[section_name][description_key], expected)
            self.assertNotIn(legacy_key, migrated[section_name])

    def test_run_creation_isolated_and_safe(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run = result["run"]
        self.assertFalse(run["hard_constraints"]["save_dwg"])
        self.assertFalse(run["hard_constraints"]["overwrite_source_dwg"])
        self.assertTrue(run["hard_constraints"]["save_separate_result_dwg"])
        self.assertTrue(run["hard_constraints"]["result_dwg_one_copy_per_assignment"])
        self.assertTrue(run["hard_constraints"]["reuse_pinned_result_dwg_on_continuation"])
        self.assertFalse(run["hard_constraints"]["new_run_requires_new_dwg"])
        self.assertEqual(run["hard_constraints"]["result_dwg_filename_pattern_scope"], "initial_creation_only")
        self.assertFalse(run["hard_constraints"]["results_remain_in_open_source_document"])
        self.assertTrue(run["hard_constraints"]["assistant_executes_autocad_directly"])
        self.assertFalse(run["hard_constraints"]["local_control_confirmation_required"])
        self.assertFalse(run["hard_constraints"]["delete_objects"])
        self.assertTrue(Path(result["task_file"]).exists())
        registry = read_json(Path(result["registry_file"]))
        self.assertEqual(registry["findings"], [])
        self.assertTrue(registry["dwg_actions"]["result_copy_required"])
        task_text = Path(result["task_file"]).read_text(encoding="utf-8")
        self.assertIn("отдельную копию результата", task_text)
        self.assertIn("Одно поручение — одна копия результата DWG", task_text)
        self.assertIn("без нового SaveAs", task_text)
        self.assertIn("record_result_dwg", task_text)
        self.assertTrue(Path(result["quality_file"]).is_file())

    def test_completed_run_requires_separate_result_dwg_beside_source(self) -> None:
        project = self.create_ready_project()
        drawings = self.root / "drawings"
        drawings.mkdir()
        source = drawings / "source.dwg"
        source.write_bytes(b"source")
        project["dwg"]["path"] = str(source)
        self.app.projects.save("test", project)

        created = self.app.runs.create("test", "compliance")
        run_id = created["run"]["run_id"]
        registry_path = Path(created["registry_file"])
        registry = read_json(registry_path)
        registry["dwg_actions"]["work_completed"] = True
        write_json(registry_path, registry)
        incomplete = self.app.runs.validate("test", run_id)
        self.assertFalse(incomplete["ok"])
        self.assertTrue(any("отдельный новый DWG" in error for error in incomplete["errors"]))

        result_path = drawings / f"source__compliance__{run_id}.dwg"
        result_path.write_bytes(b"result")
        recorded = self.app.runs.record_result_dwg(
            "test",
            run_id,
            str(result_path),
            source_document_contains_results=False,
            copy_did_not_change_open_source_identity=True,
        )
        self.assertTrue(recorded["result_copy_created"])
        self.assertEqual(Path(recorded["result_copy_path"]), result_path.resolve())
        checks = {
            "scope_complete": True, "sources_verified": True, "mode_rules_verified": True,
            "geometry_verified": True, "object_properties_verified": True,
            "visual_review_complete": True, "source_dwg_unchanged": True,
            "result_saved_and_reopened": True, "registry_complete": True,
        }
        self.app.runs.record_quality_control("test", run_id, {
            "checks": checks, "created_or_changed_entity_ids": [], "unresolved": []})
        complete = self.app.runs.validate("test", run_id)
        self.assertTrue(complete["ok"])
        self.assertEqual(complete["errors"], [])
        application = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(application["ok"])
        self.assertEqual(application["errors"], ["Не зарегистрирован полный перечень исходных замечаний"])

    def test_result_dwg_can_reuse_prior_runs_pinned_copy_after_save_as(self) -> None:
        project = self.create_ready_project()
        drawings = self.root / "drawings"
        drawings.mkdir()
        source = drawings / "source.dwg"
        source.write_bytes(b"source")
        project["dwg"]["path"] = str(source)
        self.app.projects.save("test", project)
        first = self.app.runs.create("test", "actualization_protocol")
        current = self.app.runs.create("test", "actualization_protocol")
        result = drawings / f"source__actualization_protocol__{first['run']['run_id']}.dwg"
        result.write_bytes(b"result")
        registry_path = Path(current["registry_file"])
        registry = read_json(registry_path)
        actions = registry["dwg_actions"]
        actions.update({
            "result_copy_created": True, "result_copy_path": str(result),
            "source_path": str(source),
            "source_preservation": {
                "sha256_before_edits": "a" * 64,
                "sha256_after_edits": "a" * 64,
                "source_last_write_unchanged": True,
            },
        })
        write_json(registry_path, registry)
        recorded = self.app.runs.record_result_dwg(
            "test", current["run"]["run_id"], str(result),
            source_document_contains_results=False,
            copy_did_not_change_open_source_identity=False,
        )
        self.assertEqual(Path(recorded["result_copy_path"]), result.resolve())
        self.assertFalse(recorded["copy_did_not_change_open_source_identity"])
        self.assertEqual(recorded["source_preservation"]["sha256_after_edits"], "a" * 64)

    def test_semantic_blocks_preserve_order(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        recorded = self.app.runs.record_semantic_blocks(
            "test",
            run_id,
            [
                {"title": "Блок 2. Коридоры", "remarks": [{"text": "Первое"}]},
                {"title": "Блок 3. Выходы", "remarks": [{"text": "Второе"}]},
            ],
        )
        self.assertEqual([row["order"] for row in recorded["semantic_blocks"]], [1, 2])

    def test_non_violation_cannot_have_graphics(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        template = read_json(self.root / "templates" / "finding_template.json")
        finding = copy.deepcopy(template)
        finding["id"] = "f1"
        finding["source_text"] = "Проверить ширину"
        finding["norm"]["document"] = "СП 1.13130.2020"
        finding["norm"]["point"] = "4.2.1"
        finding["decision"]["status"] = "compliant"
        finding["autocad"]["target_entity_ids"] = ["ABC"]
        self.app.runs.add_finding("test", run_id, finding)
        validation = self.app.runs.validate("test", run_id)
        self.assertFalse(validation["ok"])
        self.assertTrue(any("графика запрещена" in error for error in validation["errors"]))

    def test_compliant_fire_compartment_area_cannot_have_ray(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "compartment-area-ok"
        finding["mode"] = "compliance"
        finding["source_text"] = "Превышена площадь пожарного отсека"
        finding["norm"]["document"] = "СП 2.13130.2020"
        finding["norm"]["point"] = "6.1.1"
        finding["decision"]["status"] = "compliant"
        finding["output"]["non_issue_explanation"].update(
            {
                "text": "Не выдано: превышение допустимой площади отсутствует.",
                "green": True,
                "placement": "next_to_source",
            }
        )
        finding["autocad"].update(
            {
                "ray_required": True,
                "ray_planned": True,
                "ray_entity_ids": ["RAY-WRONG"],
            }
        )
        self.app.runs.add_finding("test", run_id, finding)
        invalid = self.app.runs.validate("test", run_id)
        self.assertFalse(invalid["ok"])
        self.assertTrue(any("графика запрещена" in error for error in invalid["errors"]))

        finding["autocad"].update(
            {
                "ray_required": False,
                "ray_planned": False,
                "ray_entity_ids": [],
            }
        )
        self.app.runs.add_finding("test", run_id, finding)
        ready = self.app.runs.validate("test", run_id)
        self.assertTrue(ready["ok"], ready["errors"])

    def test_compliance_violation_requires_registered_ray(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "compliance-ray"
        finding["mode"] = "compliance"
        finding["source_text"] = "Устранить нарушение"
        finding["norm"]["document"] = "СП 1.13130.2020"
        finding["norm"]["point"] = "4.2.1"
        finding["applicability"]["status"] = "applicable"
        finding["decision"]["status"] = "violation"
        finding["output"]["remark_text"] = "Устранить нарушение"
        finding["autocad"]["floor"] = "2 этаж"
        self.app.runs.add_finding("test", run_id, finding)
        missing = self.app.runs.validate("test", run_id)
        self.assertFalse(missing["ok"])
        self.assertTrue(any("обязательный луч" in error for error in missing["errors"]))

        finding["autocad"].update(
            {
                "target_semantics": "фактическое место нарушения",
                "target_entity_ids": ["TARGET-2"],
                "target_scope": "specific_object",
                "target_floor": "2",
                "target_confirmed": True,
                "ray_required": True,
                "ray_requirement_reason": "Подтверждённое нарушение оценки соответствия",
                "ray_rule": "compliance_specific_violation",
                "ray_planned": True,
            }
        )
        self.set_valid_ray_origin(finding)
        self.app.runs.add_finding("test", run_id, finding)
        ready = self.app.runs.validate("test", run_id)
        self.assertTrue(ready["ok"], ready["errors"])

    def test_non_issued_source_requires_reason_not_drawing_comment(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        template = read_json(self.root / "templates" / "finding_template.json")
        finding = copy.deepcopy(template)
        finding["id"] = "f-green"
        finding["source_text"] = "Проверить требование"
        finding["norm"]["document"] = "СП 1.13130.2020"
        finding["norm"]["point"] = "4.2.1"
        finding["decision"]["status"] = "compliant"
        self.app.runs.add_finding("test", run_id, finding)
        missing = self.app.runs.validate("test", run_id)
        self.assertTrue(any("не указана причина" in error for error in missing["errors"]))

        finding["decision"]["reason"] = "Проверенное решение соответствует требованию; доказательства в реестре."
        finding["autocad"]["source_entity_ids"] = ["SOURCE-1"]
        finding["autocad"]["overlap_checked"] = True
        finding["autocad"]["status"] = "verified"
        self.set_complete_individual_analysis(finding)
        self.app.runs.add_finding("test", run_id, finding)
        without_note = self.app.runs.validate("test", run_id, for_application=True)
        self.assertEqual(without_note["errors"], ["Не зарегистрирован полный перечень исходных замечаний"])

        finding["output"]["non_issue_explanation"].update(
            {
                "text": "Не выдано: фактическая ширина соответствует требованию.",
                "green": True,
                "placement": "next_to_source",
            }
        )
        finding["autocad"]["source_entity_ids"] = ["SOURCE-1"]
        finding["autocad"]["overlap_checked"] = True
        finding["autocad"]["status"] = "verified"
        self.set_complete_individual_analysis(finding)
        self.app.runs.add_finding("test", run_id, finding)
        ready = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(ready["ok"])
        self.assertEqual(ready["errors"], ["Не зарегистрирован полный перечень исходных замечаний"])

    def test_stage_right_issued_element_requires_ray_and_confirmed_target(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "stage")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "stage-right"
        finding["mode"] = "stage"
        finding["source_text"] = "Уточнить назначение помещения"
        finding["norm"]["document"] = "СП 1.13130.2020"
        finding["norm"]["point"] = "4.2"
        finding["output"]["stage_element_kind"] = "query"
        finding["output"]["stage_is_issued"] = True
        finding["autocad"]["floor"] = "1 этаж"
        finding["autocad"]["placement"]["relative_to_stage_frame"] = "right"
        self.app.runs.add_finding("test", run_id, finding)
        missing = self.app.runs.validate("test", run_id)
        self.assertFalse(missing["ok"])
        self.assertTrue(any("обязательный луч" in error for error in missing["errors"]))
        self.assertTrue(any("цель" in error for error in missing["errors"]))

        finding["autocad"].update(
            {
                "target_semantics": "выделенное помещение первого этажа",
                "target_entity_ids": ["ROOM-1"],
                "target_scope": "specific_object",
                "target_floor": "1 этаж",
                "target_confirmed": True,
                "ray_required": True,
                "ray_requirement_reason": "Актуальный выдаваемый запрос справа от рамки этапизации",
                "ray_rule": "stage_specific_object",
                "ray_planned": True,
            }
        )
        self.app.runs.add_finding("test", run_id, finding)
        bad_origin = self.app.runs.validate("test", run_id)
        self.assertFalse(bad_origin["ok"])
        self.assertTrue(any("исходной точки луча" in error for error in bad_origin["errors"]))

        finding["autocad"]["ray_origin"].update(
            {
                "rule": "outermost_group_point_toward_plan",
                "coordinates": [50.0, 50.0],
                "full_group_bounds": [0.0, 0.0, 100.0, 100.0],
                "plan_position_relative_to_group": "right",
                "side_toward_plan": "right",
                "bounds_checked": True,
                "bounds_check_result": "verified",
                "path_clear_of_group": True,
            }
        )
        self.app.runs.add_finding("test", run_id, finding)
        inside_origin = self.app.runs.validate("test", run_id)
        self.assertFalse(inside_origin["ok"])
        self.assertTrue(any("не лежат" in error for error in inside_origin["errors"]))

        self.set_valid_ray_origin(finding)
        self.app.runs.add_finding("test", run_id, finding)
        ready = self.app.runs.validate("test", run_id)
        self.assertTrue(ready["ok"], ready["errors"])

    def test_stage_whole_building_ray_must_target_first_floor_lower_left_corner(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "stage")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "stage-whole"
        finding["mode"] = "stage"
        finding["source_text"] = "Уточнить строительный объём"
        finding["norm"]["document"] = "СП 8.13130.2020"
        finding["norm"]["point"] = "5"
        finding["output"]["stage_element_kind"] = "query"
        finding["output"]["stage_is_issued"] = True
        finding["autocad"].update(
            {
                "floor": "1 этаж",
                "target_semantics": "нижний левый угол здания",
                "target_entity_ids": ["CORNER-1"],
                "target_scope": "whole_building",
                "target_floor": "2 этаж",
                "target_confirmed": True,
                "ray_required": True,
                "ray_requirement_reason": "Общий запрос справа от рамки этапизации",
                "ray_rule": "stage_specific_object",
                "ray_planned": True,
            }
        )
        finding["autocad"]["placement"]["relative_to_stage_frame"] = "right"
        self.app.runs.add_finding("test", run_id, finding)
        invalid = self.app.runs.validate("test", run_id)
        self.assertFalse(invalid["ok"])
        self.assertTrue(any("нижнему левому углу" in error for error in invalid["errors"]))
        self.assertTrue(any("первом этаже" in error for error in invalid["errors"]))

        finding["autocad"]["target_floor"] = "1 этаж"
        finding["autocad"]["ray_rule"] = "stage_whole_building_first_floor_lower_left_corner"
        self.set_valid_ray_origin(finding)
        self.app.runs.add_finding("test", run_id, finding)
        ready = self.app.runs.validate("test", run_id)
        self.assertTrue(ready["ok"], ready["errors"])

    def test_stage_direct_minimum_exit_rule_is_enforced(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "stage")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "stage-min-exit"
        finding["mode"] = "stage"
        finding["source_text"] = "Общее требование по п. 4.2.19 и п. 9.3.3"
        finding["norm"]["document"] = "СП 1.13130.2020"
        finding["norm"]["point"] = "4.2.19; 9.3.3"
        finding["output"]["stage_element_kind"] = "requirement"
        finding["output"]["stage_is_issued"] = True
        finding["autocad"]["floor"] = "1 этаж"
        finding["autocad"]["placement"]["relative_to_stage_frame"] = "inside_frame"
        finding["autocad"]["ray_required"] = True
        finding["autocad"]["ray_rule"] = "stage_specific_object"
        self.app.runs.add_finding("test", run_id, finding)
        invalid = self.app.runs.validate("test", run_id)
        self.assertFalse(invalid["ok"])
        self.assertTrue(any("наименьшего выхода" in error for error in invalid["errors"]))

        finding["autocad"].update(
            {
                "target_semantics": "наименьший выход из помещения первого этажа",
                "target_entity_ids": ["DOOR-MIN"],
                "target_scope": "specific_object",
                "target_floor": "первый этаж",
                "target_confirmed": True,
                "ray_requirement_reason": "Прямое правило п. 4.2.19 и п. 9.3.3",
                "ray_rule": "minimum_first_floor_room_exit_under_1200",
                "ray_planned": True,
            }
        )
        finding["measurement"].update(
            {
                "value": 850,
                "unit": "мм",
                "selection_rule": "minimum_first_floor_room_exit_under_1200",
                "selection_verified": True,
                "in_clear": True,
                "dimension_layer": "экспертиза",
                "dimension_by_layer": True,
                "dimension_entity_ids": ["DIM-DOOR-MIN"],
            }
        )
        self.set_valid_ray_origin(finding)
        self.app.runs.add_finding("test", run_id, finding)
        ready = self.app.runs.validate("test", run_id)
        self.assertTrue(ready["ok"], ready["errors"])

    def test_ambiguous_room_query_targets_only_verified_explication_gaps(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "stage")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "stage-room-purpose"
        finding["mode"] = "stage"
        finding["source_text"] = "Просим уточнить назначение выделенных помещений"
        finding["norm"]["document"] = "Экспликация помещений"
        finding["norm"]["point"] = "назначение"
        finding["output"]["stage_element_kind"] = "query"
        finding["output"]["stage_is_issued"] = True
        finding["autocad"].update(
            {
                "floor": "1 этаж",
                "target_semantics": "помещения с неоднозначным назначением по экспликации",
                "target_entity_ids": ["ROOM-UNKNOWN-1", "ROOM-UNKNOWN-2"],
                "target_scope": "specific_object",
                "target_floor": "1 этаж",
                "target_confirmed": True,
                "ray_required": True,
                "ray_requirement_reason": "Адресный запрос справа от рамки",
                "ray_rule": "ambiguous_rooms_by_explication",
                "ray_planned": True,
            }
        )
        finding["autocad"]["placement"]["relative_to_stage_frame"] = "right"
        self.app.runs.add_finding("test", run_id, finding)
        invalid = self.app.runs.validate("test", run_id)
        self.assertFalse(invalid["ok"])
        self.assertTrue(any("всех помещений этажа" in error for error in invalid["errors"]))

        finding["autocad"]["target_selection_verified"] = True
        self.set_valid_ray_origin(finding)
        self.app.runs.add_finding("test", run_id, finding)
        ready = self.app.runs.validate("test", run_id)
        self.assertTrue(ready["ok"], ready["errors"])

    def test_issued_remark_requires_complete_group_and_placement(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "f-group"
        finding["source_text"] = "Устранить нарушение"
        finding["norm"]["document"] = "СП 1.13130.2020"
        finding["norm"]["point"] = "4.2.1"
        finding["applicability"]["status"] = "applicable"
        finding["decision"]["status"] = "violation"
        finding["output"]["remark_text"] = "Устранить нарушение"
        finding["autocad"]["floor"] = "1"
        finding["autocad"]["target_semantics"] = "эвакуационный выход"
        finding["autocad"]["overlap_checked"] = True
        finding["autocad"]["status"] = "verified"
        self.app.runs.add_finding("test", run_id, finding)
        missing = self.app.runs.validate("test", run_id, for_application=True)
        self.assertTrue(any("рамка" in error for error in missing["errors"]))

        finding["autocad"]["source_group"].update(
            {
                "remark_text_entity_ids": ["TEXT-1"],
                "frame_entity_ids": ["FRAME-1"],
                "number_entity_ids": ["NUMBER-1"],
                "purple_explanation_present": True,
                "purple_explanation_entity_ids": ["PURPLE-SOURCE-1"],
            }
        )
        finding["autocad"]["placement"].update(
            {
                "side": "left",
                "sequence_index": 1,
                "exterior_wall_offset_mm": 30000,
                "explanation_side": "left",
            }
        )
        finding["autocad"].update(
            {
                "target_entity_ids": ["TARGET-1"],
                "target_scope": "specific_object",
                "target_floor": "1",
                "target_confirmed": True,
                "ray_required": True,
                "ray_requirement_reason": "Подтверждённое нарушение оценки соответствия",
                "ray_rule": "compliance_specific_violation",
                "ray_planned": True,
            }
        )
        self.set_valid_ray_origin(finding)
        self.set_complete_individual_analysis(finding)
        self.set_valid_compliance_source_copy(finding)
        self.app.runs.add_finding("test", run_id, finding)
        ready = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(ready["ok"])
        self.assertEqual(ready["errors"], ["Не зарегистрирован полный перечень исходных замечаний"])

        finding["autocad"]["placement"]["explanation_side"] = "top"
        self.app.runs.add_finding("test", run_id, finding)
        wrong_explanation_side = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(wrong_explanation_side["ok"])
        self.assertTrue(any("не с требуемой стороны" in error for error in wrong_explanation_side["errors"]))
        finding["autocad"]["placement"]["explanation_side"] = "left"

        for remark_side, explanation_side in (
            ("top", "top"),
            ("right", "right"),
            ("bottom", "left"),
        ):
            finding["autocad"]["placement"]["side"] = remark_side
            finding["autocad"]["placement"]["explanation_side"] = explanation_side
            self.app.runs.add_finding("test", run_id, finding)
            correctly_placed = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(correctly_placed["ok"])
        self.assertEqual(correctly_placed["errors"], ["Не зарегистрирован полный перечень исходных замечаний"])
        finding["autocad"]["placement"]["side"] = "left"
        finding["autocad"]["placement"]["explanation_side"] = "left"

        source_group = finding["autocad"]["source_group"]
        source_group["purple_explanation_present"] = False
        source_group["purple_explanation_entity_ids"] = []
        source_group["left_explanation_present"] = False
        source_group["left_explanation_entity_ids"] = []
        source_group["source_text_styles"] = [
            row for row in source_group["source_text_styles"] if row["entity_id"] != "PURPLE-SOURCE-1"
        ]
        finding["autocad"]["placement"]["explanation_side"] = ""
        self.app.runs.add_finding("test", run_id, finding)
        no_explanation = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(no_explanation["ok"])
        self.assertEqual(no_explanation["errors"], ["Не зарегистрирован полный перечень исходных замечаний"])

        finding["autocad"]["source_group"]["left_explanation_checked"] = False
        self.app.runs.add_finding("test", run_id, finding)
        missing_left_check = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(missing_left_check["ok"])
        self.assertTrue(any("left_explanation_checked" in error for error in missing_left_check["errors"]))
        finding["autocad"]["source_group"]["left_explanation_checked"] = True

        finding["autocad"]["source_group"]["manual_text_creation_used"] = True
        self.app.runs.add_finding("test", run_id, finding)
        manual_mtext = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(manual_mtext["ok"])
        self.assertTrue(any("новый MText" in error for error in manual_mtext["errors"]))

        finding["autocad"]["source_group"]["manual_text_creation_used"] = False
        finding["autocad"]["source_group"]["copy_case"] = "repeated_floor"
        self.app.runs.add_finding("test", run_id, finding)
        wrong_repeat_source = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(wrong_repeat_source["ok"])
        self.assertTrue(
            any("previous_actualized_compliance_group" in error for error in wrong_repeat_source["errors"])
        )

    def test_duplicate_template_reasoning_blocks_application(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        findings = []
        for index, point in enumerate(("4.2.1", "5.2.7"), start=1):
            finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
            finding["id"] = f"duplicate-{index}"
            finding["mode"] = "compliance"
            finding["source_text"] = f"Отдельное замечание {index}"
            finding["norm"]["document"] = "СП 1.13130.2020"
            finding["norm"]["point"] = point
            finding["decision"]["status"] = "compliant"
            finding["output"]["non_issue_explanation"].update(
                {
                    "text": f"Не выдано отдельное замечание {index}: установлен индивидуальный факт.",
                    "green": True,
                    "placement": "next_to_source",
                }
            )
            finding["autocad"]["source_entity_ids"] = [f"SOURCE-{index}"]
            finding["autocad"]["overlap_checked"] = True
            finding["autocad"]["status"] = "verified"
            self.set_complete_individual_analysis(finding, reasoning="Одинаковое шаблонное обоснование")
            self.app.runs.add_finding("test", run_id, finding)
            findings.append(finding)

        invalid = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(invalid["ok"])
        self.assertTrue(any("шаблонные объяснения" in error for error in invalid["errors"]))

        findings[1]["analysis"]["individual_reasoning"] = "Отдельное обоснование второго нормативного предмета"
        self.app.runs.add_finding("test", run_id, findings[1])
        ready = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(ready["ok"])
        self.assertEqual(ready["errors"], ["Не зарегистрирован полный перечень исходных замечаний"])

    def test_insufficient_data_requires_four_part_request(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "insufficient-data"
        finding["mode"] = "compliance"
        finding["source_text"] = "Проверить требование при неизвестном числе людей"
        finding["norm"]["document"] = "СП 1.13130.2020"
        finding["norm"]["point"] = "4.2.7"
        finding["decision"]["status"] = "insufficient_data"
        finding["output"]["non_issue_explanation"].update(
            {
                "text": "Не выдано: неизвестно расчётное количество людей для проверки п. 4.2.7.",
                "green": True,
                "placement": "next_to_source",
            }
        )
        finding["autocad"]["source_entity_ids"] = ["SOURCE-INSUFFICIENT"]
        finding["autocad"]["overlap_checked"] = True
        finding["autocad"]["status"] = "verified"
        self.set_complete_individual_analysis(finding)
        finding["analysis"]["facts_complete"] = False
        finding["analysis"]["insufficient_characteristics"] = ["расчётное количество людей"]
        self.app.runs.add_finding("test", run_id, finding)
        invalid = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(invalid["ok"])
        self.assertTrue(any("каких данных не хватает" in error for error in invalid["errors"]))

        finding["analysis"]["missing_data_request"].update(
            {
                "missing_data": "расчётное количество людей",
                "required_for_requirement": "проверка числа и ширины эвакуационных выходов по п. 4.2.7",
                "expected_document_or_section": "технологическая часть проекта или задание заказчика",
                "unverifiable_decision": "невозможно определить требуемое количество выходов",
            }
        )
        self.app.runs.add_finding("test", run_id, finding)
        ready = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(ready["ok"])
        self.assertEqual(ready["errors"], ["Не зарегистрирован полный перечень исходных замечаний"])

    def test_mgn_absence_cannot_justify_unrelated_requirement(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "mgn-unrelated"
        finding["mode"] = "compliance"
        finding["source_text"] = "Проверить огнестойкость конструкции"
        finding["norm"]["document"] = "СП 2.13130.2020"
        finding["norm"]["point"] = "5.4"
        finding["decision"]["status"] = "compliant"
        finding["output"]["non_issue_explanation"].update(
            {
                "text": "Не выдано, поскольку на объекте отсутствуют МГН.",
                "green": True,
                "placement": "next_to_source",
            }
        )
        finding["autocad"]["source_entity_ids"] = ["SOURCE-MGN"]
        finding["autocad"]["overlap_checked"] = True
        finding["autocad"]["status"] = "verified"
        self.set_complete_individual_analysis(
            finding,
            reasoning="Требование по огнестойкости не выдано, так как отсутствуют МГН.",
        )
        finding["applicability"]["mgn_dependency"] = "does_not_depend"
        self.app.runs.add_finding("test", run_id, finding)
        invalid = self.app.runs.validate("test", run_id, for_application=True)
        self.assertFalse(invalid["ok"])
        self.assertTrue(any("непосредственно от них не зависит" in error for error in invalid["errors"]))

    def test_cannot_finish_later_finding_before_previous_one(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        first = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        first["id"] = "first-unfinished"
        first["source_text"] = "Первое замечание"
        first["norm"]["document"] = "СП 1.13130.2020"
        first["norm"]["point"] = "4.2.1"
        self.app.runs.add_finding("test", run_id, first)

        second = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        second["id"] = "second-finished"
        second["source_text"] = "Второе замечание"
        second["norm"]["document"] = "СП 1.13130.2020"
        second["norm"]["point"] = "4.2.2"
        self.set_complete_individual_analysis(second)
        self.app.runs.add_finding("test", run_id, second)
        invalid = self.app.runs.validate("test", run_id)
        self.assertFalse(invalid["ok"])
        self.assertTrue(any("раньше предыдущего незавершённого" in error for error in invalid["errors"]))

    def test_final_numbering_order(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        items = [
            {
                "id": "floor-1",
                "final_number": 1,
                "category": "lowest_floor",
                "floor_label": "1 этаж",
                "floor_order": 1,
                "placement_sequence": 1,
                "number_entity_ids": ["N1"],
                "remark_entity_ids": ["R1"],
                "source_mode": "compliance",
            },
            {
                "id": "frame-1",
                "final_number": 2,
                "category": "stage_frame",
                "floor_label": "рамка этапизации",
                "placement_sequence": 1,
                "number_entity_ids": ["N2"],
                "remark_entity_ids": ["R2"],
                "source_mode": "stage",
            },
            {
                "id": "floor-2",
                "final_number": 3,
                "category": "upper_floor",
                "floor_label": "2 этаж",
                "floor_order": 2,
                "placement_sequence": 1,
                "number_entity_ids": ["N3"],
                "remark_entity_ids": ["R3"],
                "source_mode": "compliance",
            },
        ]
        recorded = self.app.runs.record_final_numbering("test", run_id, items)
        self.assertEqual(recorded["status"], "verified")
        self.assertTrue(self.app.runs.validate_final_numbering("test", run_id)["ok"])

        items[0]["final_number"] = 2
        items[1]["final_number"] = 1
        incorrect = self.app.runs.record_final_numbering("test", run_id, items)
        self.assertEqual(incorrect["status"], "needs_correction")
        self.assertTrue(any("Нарушен порядок" in error for error in incorrect["validation"]["errors"]))

    def test_stairwell_facade_opening_clearance_requires_dimension_target(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "stairwell-facade-opening-clearance"
        finding["mode"] = "compliance"
        finding["source_text"] = (
            "Расстояние между проёмом лестничной клетки и соседним проёмом "
            "в наружной стене меньше нормативного"
        )
        finding["norm"]["document"] = "СП 2.13130.2020"
        finding["norm"]["point"] = "проверяемый пункт"
        finding["applicability"]["status"] = "applicable"
        finding["decision"]["status"] = "violation"
        finding["output"]["remark_text"] = finding["source_text"]
        finding["measurement"].update(
            {
                "required": True,
                "method": "между ближайшими гранями вдоль наружной стены",
                "value": 900,
                "unit": "мм",
                "required_value": 1200,
                "measurement_type": "stairwell_to_adjacent_facade_opening_horizontal_clearance",
                "dimension_entity_ids": ["DIM-1"],
                "result_summary": "900 мм меньше нормативных 1200 мм",
                "opening_clearance_checks": [
                    {
                        "floor": "2 этаж",
                        "stairwell_opening_entity_id": "STAIR-OPENING-1",
                        "facade_opening_entity_id": "FACADE-OPENING-1",
                        "nearest_edge_points": [[0.0, 0.0], [900.0, 0.0]],
                        "nearest_edges_verified": True,
                        "measured_along_exterior_wall_plane": True,
                        "dimension_type": "aligned_parallel_to_wall",
                        "actual_distance_mm": 900,
                        "required_distance_mm": 1200,
                        "dimension_entity_id": "DIM-1",
                        "ray_entity_id": "RAY-1",
                        "dimension_layer": "экспертиза",
                        "dimension_by_layer": True,
                        "ray_rule": "ray_to_violation_dimension",
                        "ray_target_entity_id": "WRONG-TARGET",
                        "ray_endpoint_near_dimension_text": True,
                    }
                ],
            }
        )
        finding["autocad"].update(
            {
                "floor": "2 этаж",
                "target_semantics": "размер между ближайшими гранями проёмов",
                "target_entity_ids": ["DIM-1"],
                "target_scope": "specific_object",
                "target_floor": "2 этаж",
                "target_confirmed": True,
                "ray_required": True,
                "ray_requirement_reason": "нарушение подтверждается фактическим размером",
                "ray_rule": "ray_to_violation_dimension",
                "ray_planned": True,
                "ray_entity_ids": ["RAY-1"],
            }
        )
        self.set_valid_ray_origin(finding)
        self.app.runs.add_finding("test", run_id, finding)

        wrong_target = self.app.runs.validate("test", run_id)
        self.assertFalse(wrong_target["ok"])
        self.assertTrue(any("луч приходит не к объекту" in error for error in wrong_target["errors"]))

        finding["measurement"]["opening_clearance_checks"][0]["ray_target_entity_id"] = "DIM-1"
        self.app.runs.add_finding("test", run_id, finding)
        valid = self.app.runs.validate("test", run_id)
        self.assertTrue(valid["ok"], valid["errors"])

        finding["measurement"]["opening_clearance_checks"][0]["actual_distance_mm"] = 1200
        self.app.runs.add_finding("test", run_id, finding)
        not_a_violation = self.app.runs.validate("test", run_id)
        self.assertFalse(not_a_violation["ok"])
        self.assertTrue(any("не меньше нормативного" in error for error in not_a_violation["errors"]))

    def test_every_created_object_requires_exact_layer_and_by_layer_properties(self) -> None:
        self.create_ready_project()
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        finding = copy.deepcopy(read_json(self.root / "templates" / "finding_template.json"))
        finding["id"] = "exact-output-layers"
        finding["mode"] = "compliance"
        finding["source_text"] = "Подтверждённое нарушение"
        finding["norm"]["document"] = "СП 1.13130.2020"
        finding["norm"]["point"] = "4.2.1"
        finding["applicability"]["status"] = "applicable"
        finding["decision"]["status"] = "violation"
        finding["output"]["remark_text"] = "Подтверждённое нарушение"
        finding["autocad"].update(
            {
                "floor": "1 этаж",
                "target_semantics": "место подтверждённого нарушения",
                "target_entity_ids": ["TARGET-1"],
                "target_scope": "specific_object",
                "target_floor": "1 этаж",
                "target_confirmed": True,
                "ray_required": True,
                "ray_requirement_reason": "нарушение подтверждено",
                "ray_rule": "compliance_specific_violation",
                "ray_planned": True,
                "ray_entity_ids": ["RAY-1"],
                "status": "applied",
            }
        )
        finding["autocad"]["source_group"]["purple_explanation_present"] = True
        self.set_valid_ray_origin(finding)

        def object_row(entity_id: str, role: str, layer: str, entity_type: str) -> dict:
            return {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "related_finding_id": finding["id"],
                "floor": "1 этаж",
                "purpose": role,
                "role": role,
                "layer": layer,
                "color_by_layer": True,
                "linetype_by_layer": True,
                "lineweight_by_layer": True,
                "text_style": "Standard",
            }

        finding["autocad"]["created_objects"] = [
            object_row("TEXT-1", "main_text", "экспертиза", "MText"),
            object_row("FRAME-1", "frame", "экспертиза", "Polyline"),
            object_row("NUMBER-1", "number", "экспертиза", "Text"),
            object_row("SOLUTION-1", "solution_variant", "экспертиза", "MText"),
            object_row("RAY-1", "ray", "экспертиза обмеры 6", "Line"),
            object_row("PURPLE-1", "purple_explanation", "экспертиза", "MText"),
        ]
        finding["autocad"]["layer_verification"].update(
            {
                "all_created_objects_collected": True,
                "checked_against_autocad": True,
                "all_violation_graphics_on_expertise_layer": True,
                "all_purple_explanations_on_expertise_explanations_layer": True,
                "text_parts_separated_by_layer": True,
            }
        )
        self.app.runs.add_finding("test", run_id, finding)
        invalid = self.app.runs.validate("test", run_id)
        self.assertFalse(invalid["ok"])
        self.assertFalse(invalid["all_violation_graphics_on_expertise_layer"])
        self.assertFalse(invalid["all_purple_explanations_on_expertise_explanations_layer"])
        self.assertTrue(any("точный слой экспертиза" in error for error in invalid["errors"]))
        self.assertTrue(any("точный слой экспертиза пояснения" in error for error in invalid["errors"]))

        finding["autocad"]["created_objects"][4]["layer"] = "экспертиза"
        finding["autocad"]["created_objects"][5]["layer"] = "экспертиза пояснения"
        self.app.runs.add_finding("test", run_id, finding)
        valid = self.app.runs.validate("test", run_id)
        self.assertTrue(valid["ok"], valid["errors"])
        self.assertTrue(valid["all_violation_graphics_on_expertise_layer"])
        self.assertTrue(valid["all_purple_explanations_on_expertise_explanations_layer"])

        stored = self.app.runs.load("test", run_id)["registry"]["graphics_layer_check"]
        self.assertTrue(stored["all_violation_graphics_on_expertise_layer"])
        self.assertTrue(stored["all_purple_explanations_on_expertise_explanations_layer"])
        self.assertEqual(
            set(stored["checked_entity_ids"]),
            {"TEXT-1", "FRAME-1", "NUMBER-1", "SOLUTION-1", "RAY-1", "PURPLE-1"},
        )

    def test_metadata_index_from_zip(self) -> None:
        seed = self.root.parent / "fire-docs-main.zip"
        with zipfile.ZipFile(seed, "w") as archive:
            archive.writestr("fire-docs-main/рабочая/СП 1.pdf", b"dummy")
            archive.writestr("fire-docs-main/письма ВНИИПО/501.pdf", b"dummy")
        result = self.app.norms.rebuild(full_text=False)
        self.assertEqual(result["documents"], 2)
        found = self.app.norms.search("501")
        self.assertEqual(found[0]["folder"], "письма ВНИИПО")

    def test_mcp_initialize_and_tools(self) -> None:
        server = MCPServer(self.root)
        initialized = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
        )
        self.assertIn("AUTOM8LABS", initialized["result"]["instructions"])
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {item["name"] for item in listed["result"]["tools"]}
        self.assertIn("get_project_context", names)
        self.assertIn("record_semantic_blocks", names)
        self.assertIn("validate_run", names)
        self.assertIn("record_final_numbering", names)
        self.assertIn("validate_final_numbering", names)
        self.assertIn("record_result_dwg", names)
        self.assertIn("record_word_report", names)
        self.assertTrue({"read_norm_document", "find_vniipo_letters", "list_norm_documents", "render_norm_page"}.issubset(names))


    def test_compliance_ar_rename_preserves_existing_runs_and_mode(self) -> None:
        from cli import parser
        from fire_audit.runs import MODE_LABELS
        from mcp_server import SERVER_INSTRUCTIONS

        self.create_ready_project()
        self.assertEqual(MODE_LABELS["compliance"], "Оценка соответствия АР")
        self.assertEqual(parser().parse_args(["create-run", "test", "compliance"]).mode, "compliance")
        self.assertIn("Оценка соответствия АР", SERVER_INSTRUCTIONS)
        result = self.app.runs.create("test", "compliance")
        run_id = result["run"]["run_id"]
        self.assertEqual(result["run"]["mode_label"], "Оценка соответствия АР")
        self.assertIn("# Полное задание: Оценка соответствия АР\n",
                      Path(result["task_file"]).read_text(encoding="utf-8"))
        run_path = Path(result["folder"]) / "run.json"
        legacy = read_json(run_path)
        legacy["mode_label"] = "Оценка соответствия"
        write_json(run_path, legacy)
        paths = [run_path, Path(result["registry_file"]), Path(result["task_file"])]
        original = {path: path.read_bytes() for path in paths}
        loaded = self.app.runs.load("test", run_id)
        self.assertEqual(loaded["run"]["mode_label"], "Оценка соответствия АР")
        self.assertEqual(loaded["run"]["mode"], "compliance")
        self.assertEqual(loaded["run"]["run_id"], run_id)
        listed = self.app.runs.list_runs("test")
        self.assertEqual(listed[0]["mode_label"], "Оценка соответствия АР")
        self.assertEqual(listed[0]["run_id"], run_id)
        self.assertEqual(original, {path: path.read_bytes() for path in paths})

    def test_actualization_rename_keeps_existing_runs_readable_without_rewriting(self) -> None:
        from fire_audit.knowledge import MODE_FILES
        from mcp_server import SERVER_INSTRUCTIONS

        self.create_ready_project()
        result = self.app.runs.create("test", "actualization_protocol")
        run_id = result["run"]["run_id"]
        run_path = Path(result["folder"]) / "run.json"
        legacy = read_json(run_path)
        legacy["mode_label"] = "Актуализация и протокол"
        write_json(run_path, legacy)
        paths = [run_path, Path(result["registry_file"]), Path(result["task_file"])]
        original = {path: path.read_bytes() for path in paths}
        loaded = self.app.runs.load("test", run_id)
        self.assertEqual(loaded["run"]["mode_label"], "Актуализация")
        self.assertEqual(loaded["run"]["run_id"], run_id)
        self.assertEqual(loaded["run"]["mode"], "actualization_protocol")
        listed = self.app.runs.list_runs("test")
        self.assertEqual(listed[0]["mode_label"], "Актуализация")
        self.assertEqual(listed[0]["run_id"], run_id)
        self.assertEqual(original, {path: path.read_bytes() for path in paths})
        self.assertIn("# Полное задание: Актуализация\n", paths[2].read_text(encoding="utf-8"))
        names = {row["name"] for row in self.app.knowledge.inventory()}
        self.assertIn("11_АКТУАЛИЗАЦИЯ.md", names)
        self.assertNotIn("11_АКТУАЛИЗАЦИЯ_И_ПРОТОКОЛ.md", names)
        self.assertNotIn("Актуализация и протокол", SERVER_INSTRUCTIONS)
        for mode in MODE_FILES:
            self.assertNotIn("актуализация и протокол", self.app.knowledge.context_for_mode(mode).lower())


if __name__ == "__main__":
    unittest.main()
