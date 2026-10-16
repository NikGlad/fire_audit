from __future__ import annotations

from pathlib import Path
from typing import Any

from .io_utils import read_json
from .knowledge import KnowledgeBase
from .norms import NormativeIndex
from .paths import get_paths
from .projects import ProjectStore
from .runs import RunStore
from .control import ReviewControl
from .cad_guard import GuardedCAD
from .quality import QualityChecks
from .barrier_workflow import BarrierWorkflow


class FireAuditApp:
    def __init__(self, root: str | Path | None = None):
        self.paths = get_paths(root)
        self.knowledge = KnowledgeBase(self.paths)
        self.projects = ProjectStore(self.paths)
        self.norms = NormativeIndex(self.paths)
        self.runs = RunStore(self.paths, self.projects, self.knowledge)
        self.control = ReviewControl(self)
        self.cad_guard = GuardedCAD(self.control)
        self.quality = QualityChecks(self.paths)
        self.runs.control = self.control
        self.barrier_workflow = BarrierWorkflow(self)
        self.runs.barrier_workflow = self.barrier_workflow

    def status(self) -> dict[str, Any]:
        config = read_json(self.paths.app_config, {})
        return {
            "application": config.get("application_name", "Пожарная проверка АР"),
            "version": config.get("version", "0.1.0"),
            "root": str(self.paths.root),
            "projects": len(self.projects.list_projects()),
            "knowledge_documents": len(self.knowledge.inventory()),
            "assistant_workflow": {"full_task": True, "all_modes": True,
                "direct_autom8labs_reads_and_writes": True, "assistant_quality_control": True,
                "local_control_confirmation_required": False},
            "review_control": {"visible_in_new_interface": False, "compatibility_state_only": True,
                "used_by_new_tasks": False, "cad_writes_require_local_confirmation": False},
            "barrier_assessment": {"instructions":"18 и 21", "special_inputs_form":True,
                "control_tab_supported":False,
                "executor":"assistant_via_autom8labs", "assistant_normative_decisions_required":True,
                "assistant_result_verification":True, "result_copy_becomes_active_document":True,
                "results_remain_in_open_source_document":False, "source_document_unchanged":True,
                "source_remarks_inventory_required":True, "all_source_remarks_require_decisions":True,
                "fresh_source_layer_read_before_completion":True},
            "normative_base": self.norms.status(),
            "hard_constraints": {
                "autocad_connector": config.get(
                    "autocad_connector_required", "AUTOM8LABS MCP Connector for AutoCAD"
                ),
                "save_dwg": False,
                "overwrite_source_dwg": False,
                "save_separate_result_dwg": True,
                "result_dwg_same_folder_as_source": True,
                "results_remain_in_open_source_document": False,
                "create_result_copy_before_writes": True,
                "delete_objects": False,
                "delete_expertise_layers": False,
            },
        }
