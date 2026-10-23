from __future__ import annotations

import copy
import json
import os
import queue
import shutil
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from fire_audit.core import FireAuditApp
from fire_audit.io_utils import atomic_write_text, read_json
from fire_audit.norm_lock import NormativeUpdateBusy
from fire_audit.projects import CHANGE_CATEGORIES, change_categories, manual_text_value
from fire_audit.control import project_digest
from fire_audit.barrier_ui import AdditionalDataPanel


ROOT = Path(__file__).resolve().parent


def number_to_ui(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    return str(value).replace(".", ",")


class FireAuditGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Подготовка задания на пожарную проверку")
        self.geometry("1220x780")
        self.minsize(1040, 680)
        self.app = FireAuditApp(ROOT)
        self.current_slug = ""
        self.current_project: dict = {}
        self.project_rows: list[dict] = []
        self._clipboard_menus: list[tk.Menu] = []
        self._tab_scroll_targets: dict[tk.Widget, tk.Widget] = {}
        self._norm_update_running = False
        self._background_results: queue.Queue = queue.Queue()
        self._build_ui()
        self.bind("<MouseWheel>", self._scroll_current_tab, add="+")
        self.bind("<Button-4>", self._scroll_current_tab, add="+")
        self.bind("<Button-5>", self._scroll_current_tab, add="+")
        self.refresh_projects()
        self.refresh_norm_status()

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Heading.TLabel", font=("Segoe UI", 10, "bold"))

        header = ttk.Frame(self, padding=(14, 12))
        header.pack(fill="x")
        ttk.Label(header, text="Подготовка задания на пожарную проверку", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="Приложение собирает данные и правила; помощник выполняет работу через AUTOM8LABS",
            foreground="#8b1a1a",
        ).pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        left = ttk.Frame(body, padding=8)
        body.add(left, weight=1)
        ttk.Label(left, text="Объекты", style="Heading.TLabel").pack(anchor="w")
        self.project_list = tk.Listbox(left, exportselection=False, font=("Segoe UI", 10))
        self.project_list.pack(fill="both", expand=True, pady=8)
        self.project_list.bind("<<ListboxSelect>>", self._on_project_selected)
        self._enable_listbox_copy(self.project_list)
        ttk.Button(left, text="Создать объект", command=self.create_project).pack(fill="x", pady=2)
        ttk.Button(left, text="Обновить список", command=self.refresh_projects).pack(fill="x", pady=2)
        ttk.Button(left, text="Открыть папку объекта", command=self.open_project_folder).pack(fill="x", pady=2)

        right = ttk.Frame(body)
        body.add(right, weight=4)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        self._build_project_tab()
        self._build_change_log_tab()
        self.additional_data_panel = AdditionalDataPanel(self)
        self.barrier_panel = self.additional_data_panel  # Compatibility with saved integrations.
        self._build_run_tab()
        self._build_norms_tab()
        self._build_knowledge_tab()

        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 4)).pack(fill="x")

    def _build_project_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(tab, text="Паспорт объекта")
        canvas = tk.Canvas(tab, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        form = ttk.Frame(canvas)
        form.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=form, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._tab_scroll_targets[tab] = canvas

        self.fields: dict[str, tk.Variable] = {}
        row = 0
        row = self._entry(form, row, "name", "Название объекта")
        row = self._entry(form, row, "dwg", "Полный путь к DWG", browse=True)
        row = self._entry(form, row, "gpzu", "Дата ГПЗУ (ГГГГ-ММ-ДД)")
        row = self._entry(form, row, "classes", "Функциональные классы через запятую")
        row = self._entry(form, row, "fire_degree", "Степень огнестойкости")
        row = self._entry(form, row, "constructive_class", "Класс конструктивной пожарной опасности")
        row = self._entry(form, row, "building_category", "Категория здания")
        row = self._entry(form, row, "height_sp1", "Высота по п. 3.1 СП 1 (м; можно с пояснениями)")
        row = self._entry(form, row, "f5_floor_to_ceiling_height", "Высота от пола до потолка для зданий Ф5 (м; можно с пояснениями)")
        row = self._entry(form, row, "fire_compartment_division", "Деление на пожарные отсеки")
        for key, label in (
            ("mgn", "Есть МГН"),
            ("m4", "Есть МГН группы М4"),
            ("abk", "Есть АБК (для зданий Ф5)"),
            ("fire_lifts", "Есть лифты для пожарных"),
            ("all_stairs", "Все лестницы эвакуационные"),
            ("smoke_free_stairwells", "Наличие незадымляемых лестничных клеток"),
            ("facades", "Представлены фасады"),
            ("sections", "Представлены разрезы"),
            ("roof", "Представлена кровля"),
        ):
            row = self._entry(form, row, key, label)
        ttk.Label(form, text="Примечания", style="Heading.TLabel").grid(row=row, column=0, sticky="nw", padx=4, pady=6)
        self.notes = tk.Text(form, height=8, width=72, wrap="word", undo=True, maxundo=-1, font=("Segoe UI", 10))
        self.notes.grid(row=row, column=1, sticky="nsew", padx=4, pady=6)
        self._enable_text_clipboard(self.notes)
        ttk.Button(form, text="Вставить из буфера", command=lambda: self._paste_into_widget(self.notes)).grid(
            row=row, column=2, sticky="nw", padx=4, pady=6
        )
        row += 1
        buttons = ttk.Frame(form)
        buttons.grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)
        ttk.Button(buttons, text="Сохранить паспорт", command=self.save_project).pack(side="left", padx=3)
        ttk.Button(buttons, text="Проверить заполнение", command=self.validate_project).pack(side="left", padx=3)
        ttk.Button(buttons, text="Открыть project.json", command=self.open_project_json).pack(side="left", padx=3)
        form.columnconfigure(1, weight=1)

    def _build_change_log_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(tab, text="Изменения объекта")
        ttk.Label(tab, text="История изменений выбранного объекта доступна во всех видах работ.",
                  style="Heading.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        self.change_date = tk.StringVar(value=datetime.now().date().isoformat())
        self.change_category_vars = {
            name: tk.BooleanVar(value=name == "Планы") for name in CHANGE_CATEGORIES
        }
        self.change_scope = tk.StringVar()
        self.change_source = tk.StringVar()
        for row, label, variable in (
            (1, "Дата изменения (ГГГГ-ММ-ДД)", self.change_date),
            (3, "Затронутые листы, помещения, разделы", self.change_scope),
            (4, "Источник: документ, лист или сообщение", self.change_source),
        ):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=3)
            entry = ttk.Entry(tab, textvariable=variable, width=78)
            entry.grid(row=row, column=1, columnspan=3, sticky="ew", padx=8, pady=3)
            self._enable_widget_clipboard(entry, editable=True)
        ttk.Label(tab, text="Виды изменений (можно выбрать несколько)").grid(
            row=2, column=0, sticky="w", pady=3)
        category_frame = ttk.Frame(tab)
        category_frame.grid(row=2, column=1, columnspan=3, sticky="w", padx=8, pady=3)
        for index, (name, variable) in enumerate(self.change_category_vars.items()):
            ttk.Checkbutton(category_frame, text=name, variable=variable).grid(
                row=index // 3, column=index % 3, sticky="w", padx=(0, 16), pady=2)
        ttk.Label(tab, text="Что изменилось").grid(row=5, column=0, sticky="nw", pady=3)
        self.change_description = tk.Text(tab, height=4, wrap="word", undo=True, font=("Segoe UI", 10))
        self.change_description.grid(row=5, column=1, columnspan=3, sticky="nsew", padx=8, pady=3)
        self._enable_text_clipboard(self.change_description)
        ttk.Label(tab, text="На что может повлиять").grid(row=6, column=0, sticky="nw", pady=3)
        self.change_impact = tk.Text(tab, height=3, wrap="word", undo=True, font=("Segoe UI", 10))
        self.change_impact.grid(row=6, column=1, columnspan=3, sticky="nsew", padx=8, pady=3)
        self._enable_text_clipboard(self.change_impact)
        ttk.Button(tab, text="Добавить запись в историю", command=self.add_project_change).grid(
            row=7, column=1, sticky="w", padx=8, pady=8)
        ttk.Label(tab, text="История (новые записи сверху; прежние записи сохраняются)",
                  style="Heading.TLabel").grid(row=8, column=0, columnspan=4, sticky="w", pady=(10, 4))
        self.change_tree = ttk.Treeview(tab, columns=("date", "category", "scope", "description"),
                                        show="headings", height=9)
        for key, label, width in (("date", "Дата", 115), ("category", "Виды", 230),
                                  ("scope", "Где", 215), ("description", "Изменение", 470)):
            self.change_tree.heading(key, text=label)
            self.change_tree.column(key, width=width, stretch=key == "description")
        self.change_tree.grid(row=9, column=0, columnspan=4, sticky="nsew")
        self.change_tree.bind("<<TreeviewSelect>>", self._show_selected_change)
        self._enable_tree_copy(self.change_tree)
        self.change_details = tk.Text(tab, height=5, wrap="word", font=("Segoe UI", 10), state="disabled")
        self.change_details.grid(row=10, column=0, columnspan=4, sticky="nsew", pady=(6, 0))
        self._enable_text_clipboard(self.change_details, editable=False)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(9, weight=1)

    def refresh_change_log(self) -> None:
        for item_id in self.change_tree.get_children():
            self.change_tree.delete(item_id)
        changes = self.current_project.get("change_log", []) if self.current_slug else []
        for entry in sorted(changes, key=lambda item: (item["event_date"], item["recorded_at"]), reverse=True):
            self.change_tree.insert("", "end", iid=entry["id"], values=(
                entry["event_date"], ", ".join(change_categories(entry)), entry.get("affected_scope", ""),
                entry["description"].replace("\n", " ")[:150]))
        self._show_selected_change()

    def _show_selected_change(self, event=None) -> None:
        selected = self.change_tree.selection()
        entry = next((item for item in self.current_project.get("change_log", [])
                      if selected and item.get("id") == selected[0]), None)
        value = "" if entry is None else (
            f"Дата изменения: {entry['event_date']}   Записано: {entry['recorded_at']}\n"
            f"Виды: {', '.join(change_categories(entry))}   Где: {entry.get('affected_scope', '')}\n"
            f"Источник: {entry['source_reference']}\n"
            f"Изменение: {entry['description']}\n"
            f"Возможное влияние: {entry.get('impact_notes', '')}")
        self.change_details.configure(state="normal")
        self.change_details.delete("1.0", "end")
        self.change_details.insert("1.0", value)
        self.change_details.configure(state="disabled")

    def _collect_change_draft(self, *, required: bool = False) -> dict | None:
        values = dict(
            event_date=self.change_date.get().strip(),
            categories=[name for name, variable in self.change_category_vars.items() if variable.get()],
            description=self.change_description.get("1.0", "end").strip(),
            affected_scope=self.change_scope.get().strip(),
            source_reference=self.change_source.get().strip(),
            impact_notes=self.change_impact.get("1.0", "end").strip())
        # Date and category have defaults; they alone do not constitute an event.
        if not required and not any(values[key] for key in (
                "description", "affected_scope", "source_reference", "impact_notes")):
            return None
        return self.app.projects.build_change(**values)

    def _clear_saved_change_draft(self) -> None:
        self.change_description.delete("1.0", "end")
        self.change_impact.delete("1.0", "end")
        self.change_scope.set("")
        self.change_source.set("")

    def add_project_change(self) -> None:
        if not self.current_slug:
            messagebox.showwarning("Изменения объекта", "Сначала выберите или создайте объект", parent=self)
            return
        try:
            change = self._collect_change_draft(required=True)
            project = self._update_project_from_form()
            self.additional_data_panel.update_project_for_save(project)
            project.setdefault("change_log", []).append(change)
            self.current_project = self.app.projects.save(self.current_slug, project)["project"]
            self._clear_saved_change_draft()
            self._load_project_form()
            self.refresh_change_log()
            self.refresh_projects()
            self.status_var.set("Изменение добавлено; паспорт и дополнительные данные сохранены")
        except Exception as exc:
            messagebox.showerror("Изменения объекта", str(exc), parent=self)

    def _scroll_current_tab(self, event: tk.Event) -> str | None:
        """Scroll the active tab when the widget under the pointer has no own scrolling."""
        widget = self.winfo_containing(event.x_root, event.y_root)
        current = widget
        while current is not None:
            if isinstance(current, (tk.Text, tk.Listbox, ttk.Treeview)):
                return None
            current = getattr(current, "master", None)

        selected = self.tabs.select()
        if not selected:
            return None
        tab = self.nametowidget(selected)
        target = self._tab_scroll_targets.get(tab)
        if target is None or not target.winfo_exists():
            return None

        if getattr(event, "num", None) == 4:
            direction = -1
        elif getattr(event, "num", None) == 5:
            direction = 1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return None
            direction = -1 if delta > 0 else 1
        target.yview_scroll(direction * 3, "units")
        return "break"

    def _copy_from_widget(self, widget: tk.Widget) -> str:
        try:
            if isinstance(widget, tk.Text):
                value = widget.get("sel.first", "sel.last") if widget.tag_ranges("sel") else widget.get("1.0", "end-1c")
            else:
                full_value = widget.get()
                try:
                    value = full_value[widget.index("sel.first") : widget.index("sel.last")]
                except tk.TclError:
                    value = full_value
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update_idletasks()
        except tk.TclError as exc:
            messagebox.showerror("Буфер обмена", str(exc), parent=self)
        return "break"

    def _paste_into_widget(self, widget: tk.Widget) -> str:
        try:
            value = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Буфер обмена", "В буфере обмена нет текста", parent=self)
            return "break"
        try:
            if isinstance(widget, tk.Text):
                if widget.tag_ranges("sel"):
                    widget.delete("sel.first", "sel.last")
                widget.insert("insert", value)
            else:
                if widget.selection_present():
                    widget.delete("sel.first", "sel.last")
                widget.insert("insert", value)
            widget.focus_set()
        except tk.TclError as exc:
            messagebox.showerror("Буфер обмена", str(exc), parent=self)
        return "break"

    def _select_all_widget(self, widget: tk.Widget) -> str:
        if isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "1.0")
            widget.see("insert")
        else:
            widget.selection_range(0, "end")
            widget.icursor("end")
        widget.focus_set()
        return "break"

    def _cut_from_widget(self, widget: tk.Widget) -> str:
        try:
            if isinstance(widget, tk.Text):
                if not widget.tag_ranges("sel"):
                    return "break"
                value = widget.get("sel.first", "sel.last")
                widget.delete("sel.first", "sel.last")
            else:
                if not widget.selection_present():
                    return "break"
                value = widget.get()[widget.index("sel.first") : widget.index("sel.last")]
                widget.delete("sel.first", "sel.last")
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update_idletasks()
        except tk.TclError as exc:
            messagebox.showerror("Буфер обмена", str(exc), parent=self)
        return "break"

    def _enable_widget_clipboard(self, widget: tk.Widget, *, editable: bool) -> None:
        menu = tk.Menu(widget, tearoff=False)
        if editable:
            menu.add_command(label="Вырезать", command=lambda: self._cut_from_widget(widget))
        menu.add_command(label="Копировать", command=lambda: self._copy_from_widget(widget))
        if editable:
            menu.add_command(label="Вставить", command=lambda: self._paste_into_widget(widget))
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: self._select_all_widget(widget))

        def show_menu(event: tk.Event) -> str:
            widget.focus_set()
            menu.tk_popup(event.x_root, event.y_root)
            return "break"

        def control_key(event: tk.Event) -> str | None:
            if event.keycode == 65:
                return self._select_all_widget(widget)
            if event.keycode == 67:
                return self._copy_from_widget(widget)
            if editable and event.keycode == 86:
                return self._paste_into_widget(widget)
            if editable and event.keycode == 88:
                return self._cut_from_widget(widget)
            return None

        widget.bind("<Button-3>", show_menu)
        if editable:
            widget.bind("<Shift-Insert>", lambda event: self._paste_into_widget(widget))
        widget.bind("<Control-KeyPress>", control_key, add="+")
        self._clipboard_menus.append(menu)

    def _enable_text_clipboard(self, widget: tk.Text, *, editable: bool = True) -> None:
        self._enable_widget_clipboard(widget, editable=editable)

    def _enable_listbox_copy(self, widget: tk.Listbox) -> None:
        def copy_selected(event=None) -> str:
            selection = widget.curselection()
            if selection:
                value = "\n".join(str(widget.get(index)) for index in selection)
                self.clipboard_clear()
                self.clipboard_append(value)
                self.update_idletasks()
            return "break"

        menu = tk.Menu(widget, tearoff=False)
        menu.add_command(label="Копировать", command=copy_selected)
        widget.bind("<Button-3>", lambda event: (widget.focus_set(), menu.tk_popup(event.x_root, event.y_root), "break")[-1])
        widget.bind("<Control-KeyPress>", lambda event: copy_selected() if event.keycode == 67 else None, add="+")
        self._clipboard_menus.append(menu)

    def _enable_tree_copy(self, widget: ttk.Treeview) -> None:
        def copy_selected(event=None) -> str:
            rows: list[str] = []
            for item_id in widget.selection():
                item = widget.item(item_id)
                values = [str(value) for value in item.get("values", [])]
                if item.get("text"):
                    values.insert(0, str(item["text"]))
                rows.append("\t".join(values))
            if rows:
                self.clipboard_clear()
                self.clipboard_append("\n".join(rows))
                self.update_idletasks()
            return "break"

        menu = tk.Menu(widget, tearoff=False)
        menu.add_command(label="Копировать строку", command=copy_selected)
        widget.bind("<Button-3>", lambda event: (widget.focus_set(), menu.tk_popup(event.x_root, event.y_root), "break")[-1])
        widget.bind("<Control-KeyPress>", lambda event: copy_selected() if event.keycode == 67 else None, add="+")
        self._clipboard_menus.append(menu)

    def _entry(self, parent: ttk.Frame, row: int, key: str, label: str, browse: bool = False) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=5)
        variable = tk.StringVar()
        self.fields[key] = variable
        entry = ttk.Entry(parent, textvariable=variable, width=75)
        entry.grid(row=row, column=1, sticky="ew", padx=4, pady=5)
        self._enable_widget_clipboard(entry, editable=True)
        if browse:
            ttk.Button(parent, text="Выбрать…", command=self.choose_dwg).grid(row=row, column=2, padx=4, pady=5)
        return row + 1

    def _combo(self, parent: ttk.Frame, row: int, key: str, label: str) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=5)
        variable = tk.StringVar(value="Неизвестно")
        self.fields[key] = variable
        combo = ttk.Combobox(parent, textvariable=variable, values=["Неизвестно", "Да", "Нет"], state="readonly", width=24)
        combo.grid(row=row, column=1, sticky="w", padx=4, pady=5)
        self._enable_widget_clipboard(combo, editable=False)
        return row + 1

    def _build_run_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(tab, text="Задание")
        ttk.Label(tab, text="Вид работы", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        self.mode_var = tk.StringVar(value="Оценка соответствия АР")
        self.mode_values = {
            "Этапизация": "stage",
            "Оценка соответствия АР": "compliance",
            "Часть 3 статьи 89": "article89",
            "Актуализация + протокол": "actualization_protocol",
            "Перенос оценки АР": "ar_assessment_transfer",
            "Подготовка планов АР": "ar_plan_preparation",
            "Сравнение планов АР": "ar_plan_comparison",
            "Протокол": "protocol",
            "Оценка Преград": "barrier_assessment",
            "Оценка ДУ": "du_assessment",
        }
        mode_combo = ttk.Combobox(tab, textvariable=self.mode_var, values=list(self.mode_values), state="readonly", width=34)
        mode_combo.grid(row=1, column=0, sticky="w", pady=7)
        self._enable_widget_clipboard(mode_combo, editable=False)
        ttk.Label(tab, text="Результат работы", style="Heading.TLabel").grid(row=0, column=1, sticky="w", padx=8)
        self.task_variant_var = tk.StringVar(value="Полная проверка с AutoCAD")
        self.task_variant_values = {
            "Полная проверка с AutoCAD": "full_execution",
            "Анализ и задание в Word": "analysis_word",
            "Нормы для объекта": "object_norms",
        }
        task_variant_combo = ttk.Combobox(
            tab, textvariable=self.task_variant_var, values=list(self.task_variant_values), state="readonly", width=30
        )
        task_variant_combo.grid(row=1, column=1, sticky="w", padx=8, pady=7)
        self._enable_widget_clipboard(task_variant_combo, editable=False)
        ttk.Button(tab, text="Сформировать и скопировать задание", command=self.create_run).grid(row=1, column=2, sticky="w", padx=8)
        ttk.Button(tab, text="Обновить историю", command=self.refresh_runs).grid(row=1, column=3, sticky="w")
        ttk.Label(tab, text="Запуски выбранного объекта", style="Heading.TLabel").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(18, 6)
        )
        self.run_tree = ttk.Treeview(tab, columns=("mode", "iteration", "variant", "plans", "status", "created"), show="headings", height=12)
        self.run_tree.heading("mode", text="Режим")
        self.run_tree.heading("iteration", text="Итерация")
        self.run_tree.heading("variant", text="Результат")
        self.run_tree.heading("plans", text="Планы")
        self.run_tree.heading("status", text="Статус")
        self.run_tree.heading("created", text="Создан")
        self.run_tree.column("mode", width=220)
        self.run_tree.column("iteration", width=75)
        self.run_tree.column("variant", width=210)
        self.run_tree.column("plans", width=110)
        self.run_tree.column("status", width=130)
        self.run_tree.column("created", width=220)
        self.run_tree.grid(row=3, column=0, columnspan=4, sticky="nsew")
        self._enable_tree_copy(self.run_tree)
        ttk.Button(tab, text="Открыть папку задания", command=self.open_run_folder).grid(row=4, column=0, sticky="w", pady=8)
        ttk.Button(tab, text="Скопировать выбранное задание", command=self.copy_run_task).grid(row=4, column=1, sticky="w", pady=8)
        ttk.Button(tab, text="Открыть реестр объекта", command=self.open_project_history).grid(row=4, column=2, sticky="w", pady=8)
        self.run_info = tk.Text(tab, height=11, wrap="word", font=("Segoe UI", 10))
        self.run_info.grid(row=5, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        self._enable_text_clipboard(self.run_info, editable=False)
        self._tab_scroll_targets[tab] = self.run_info
        tab.columnconfigure(2, weight=1)
        tab.rowconfigure(3, weight=1)
        tab.rowconfigure(5, weight=1)

    def _build_norms_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(tab, text="Нормативы")
        self.norm_status = tk.Text(tab, height=8, wrap="word", font=("Consolas", 9))
        self.norm_status.pack(fill="x")
        self._enable_text_clipboard(self.norm_status, editable=False)
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=8)
        self.norm_update_buttons = [
            ttk.Button(buttons, text="Обновить с GitHub и индексировать", command=self.sync_norms),
            ttk.Button(buttons, text="Прочитать всю локальную базу", command=self.index_norms),
        ]
        for button in self.norm_update_buttons:
            button.pack(side="left", padx=3)
        ttk.Button(buttons, text="Обновить состояние", command=self.refresh_norm_status).pack(side="left", padx=3)
        search_row = ttk.Frame(tab)
        search_row.pack(fill="x", pady=6)
        ttk.Label(search_row, text="Поиск").pack(side="left")
        self.norm_query = tk.StringVar()
        entry = ttk.Entry(search_row, textvariable=self.norm_query)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        entry.bind("<Return>", lambda event: self.search_norms())
        self._enable_widget_clipboard(entry, editable=True)
        ttk.Button(search_row, text="Найти", command=self.search_norms).pack(side="left")
        self.norm_results = tk.Text(tab, wrap="word", font=("Segoe UI", 10))
        self.norm_results.pack(fill="both", expand=True)
        self._enable_text_clipboard(self.norm_results, editable=False)
        self._tab_scroll_targets[tab] = self.norm_results

    def _build_knowledge_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Постоянные инструкции")
        pane = ttk.Panedwindow(tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=1)
        pane.add(right, weight=4)
        self.knowledge_list = tk.Listbox(left, exportselection=False)
        self.knowledge_list.pack(fill="both", expand=True)
        self.knowledge_list.bind("<<ListboxSelect>>", self.load_knowledge_file)
        self._enable_listbox_copy(self.knowledge_list)
        for item in self.app.knowledge.inventory():
            self.knowledge_list.insert("end", item["name"])
        self.knowledge_editor = tk.Text(right, wrap="word", undo=True, font=("Consolas", 10))
        self.knowledge_editor.pack(fill="both", expand=True)
        self._enable_text_clipboard(self.knowledge_editor, editable=True)
        self._tab_scroll_targets[tab] = self.knowledge_editor
        ttk.Button(right, text="Сохранить с резервной копией", command=self.save_knowledge_file).pack(anchor="e", pady=6)

    def refresh_projects(self) -> None:
        selected_slug = self.current_slug
        self.project_rows = self.app.projects.list_projects()
        self.project_list.delete(0, "end")
        for row in self.project_rows:
            self.project_list.insert("end", row.get("name", row.get("slug", "")))
        if selected_slug:
            self._select_project_by_slug(selected_slug, load_form=False)
        self.status_var.set(f"Объектов: {len(self.project_rows)}")

    def _on_project_selected(self, event=None) -> None:
        selection = self.project_list.curselection()
        if not selection:
            return
        row = self.project_rows[selection[0]]
        self.current_slug = row["slug"]
        self.current_project = self.app.projects.load(self.current_slug)
        self._load_project_form()
        self.refresh_change_log()
        self.refresh_runs()

    def _select_project_by_slug(self, slug: str, *, load_form: bool = True) -> bool:
        for index, row in enumerate(self.project_rows):
            if row.get("slug") == slug:
                self.project_list.selection_clear(0, "end")
                self.project_list.selection_set(index)
                self.project_list.activate(index)
                self.project_list.see(index)
                if load_form:
                    self._on_project_selected()
                return True
        return False

    def _load_project_form(self) -> None:
        p = self.current_project
        c = p.get("classification", {})
        self.fields["name"].set(p.get("name", ""))
        self.fields["dwg"].set(p.get("dwg", {}).get("path", ""))
        self.fields["gpzu"].set(p.get("dates", {}).get("gpzu", ""))
        self.fields["classes"].set(", ".join(c.get("functional_classes", [])))
        self.fields["fire_degree"].set(c.get("fire_resistance_degree", ""))
        self.fields["constructive_class"].set(c.get("constructive_fire_hazard_class", ""))
        self.fields["building_category"].set(c.get("building_category", ""))
        geometry = p.get("geometry", {})
        self.fields["height_sp1"].set(number_to_ui(geometry.get("height_m")))
        self.fields["f5_floor_to_ceiling_height"].set(number_to_ui(geometry.get("floor_to_ceiling_height_m")))
        accessibility = p.get("accessibility", {})
        self.fields["mgn"].set(str(manual_text_value(accessibility, "mgn_description", "mgn_present")))
        self.fields["m4"].set(str(manual_text_value(accessibility, "m4_description", "m4_present")))
        building_parts = p.get("building_parts", {})
        self.fields["abk"].set(str(manual_text_value(building_parts, "abk_description", "abk_present")))
        fire_systems = p.get("fire_systems", {})
        self.fields["fire_lifts"].set(
            str(manual_text_value(fire_systems, "firefighter_lifts_description", "firefighter_lifts"))
        )
        self.fields["fire_compartment_division"].set(
            geometry.get("fire_compartment_division_description", "")
        )
        evacuation = p.get("evacuation", {})
        self.fields["all_stairs"].set(
            str(manual_text_value(evacuation, "stairs_evacuation_description", "all_stairs_are_evacuation"))
        )
        self.fields["smoke_free_stairwells"].set(
            str(manual_text_value(
                evacuation, "smoke_free_stairwells_description", "smoke_free_stairwells_present"
            ))
        )
        provided = p.get("provided_materials", {})
        self.fields["facades"].set(str(manual_text_value(provided, "facades_description", "facades")))
        self.fields["sections"].set(str(manual_text_value(provided, "sections_description", "sections")))
        self.fields["roof"].set(str(manual_text_value(provided, "roof_description", "roof")))
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", p.get("notes", ""))
        if hasattr(self, "barrier_panel"):
            self.barrier_panel.load_project()

    def _update_project_from_form(self) -> dict:
        if self.current_slug:
            latest = self.app.projects.load(self.current_slug)
            if project_digest(latest) != project_digest(self.current_project):
                raise ValueError("Паспорт обновлён вне этого окна. Чтобы не потерять категории и уточнения, "
                    "перед сохранением сохраните свои несохранённые правки отдельно и повторно выберите объект в списке. "
                    "Текущие данные на диске не перезаписаны.")
        p = copy.deepcopy(self.current_project)
        p["name"] = self.fields["name"].get().strip()
        p.setdefault("dwg", {})["path"] = self.fields["dwg"].get().strip()
        p["dwg"]["save_allowed"] = False
        p["dwg"]["delete_allowed"] = False
        p.setdefault("dates", {})["gpzu"] = self.fields["gpzu"].get().strip()
        c = p.setdefault("classification", {})
        c["functional_classes"] = [value.strip() for value in self.fields["classes"].get().split(",") if value.strip()]
        c["fire_resistance_degree"] = self.fields["fire_degree"].get().strip()
        c["constructive_fire_hazard_class"] = self.fields["constructive_class"].get().strip()
        c["building_category"] = self.fields["building_category"].get().strip()
        geometry = p.setdefault("geometry", {})
        geometry["height_m"] = self.fields["height_sp1"].get().strip() or None
        geometry["floor_to_ceiling_height_m"] = self.fields["f5_floor_to_ceiling_height"].get().strip() or None
        accessibility = p.setdefault("accessibility", {})
        accessibility.pop("mgn_present", None)
        accessibility.pop("m4_present", None)
        accessibility["mgn_description"] = self.fields["mgn"].get().strip()
        accessibility["m4_description"] = self.fields["m4"].get().strip()
        building_parts = p.setdefault("building_parts", {})
        building_parts.pop("abk_present", None)
        building_parts["abk_description"] = self.fields["abk"].get().strip()
        fire_systems = p.setdefault("fire_systems", {})
        fire_systems.pop("firefighter_lifts", None)
        fire_systems["firefighter_lifts_description"] = self.fields["fire_lifts"].get().strip()
        geometry.pop("fire_compartment_division_present", None)
        geometry["fire_compartment_division_description"] = (
            self.fields["fire_compartment_division"].get().strip()
        )
        evacuation = p.setdefault("evacuation", {})
        evacuation.pop("all_stairs_are_evacuation", None)
        evacuation["stairs_evacuation_description"] = self.fields["all_stairs"].get().strip()
        evacuation.pop("smoke_free_stairwells_present", None)
        evacuation["smoke_free_stairwells_description"] = (
            self.fields["smoke_free_stairwells"].get().strip()
        )
        provided = p.setdefault("provided_materials", {})
        provided.pop("facades", None)
        provided.pop("sections", None)
        provided.pop("roof", None)
        provided["facades_description"] = self.fields["facades"].get().strip()
        provided["sections_description"] = self.fields["sections"].get().strip()
        provided["roof_description"] = self.fields["roof"].get().strip()
        p["notes"] = self.notes.get("1.0", "end").strip()
        return p

    def create_project(self) -> None:
        name = simpledialog.askstring("Новый объект", "Введите название объекта:", parent=self)
        if not name:
            return
        try:
            project = self.app.projects.create(name)
            self.refresh_projects()
            self._select_project_by_slug(project["slug"])
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self)

    def save_project(self) -> None:
        created_new = False
        try:
            change = self._collect_change_draft()
            if not self.current_slug:
                name = self.fields["name"].get().strip()
                if not name:
                    messagebox.showwarning(
                        "Объект",
                        "Введите название объекта. При сохранении новый объект будет создан автоматически.",
                        parent=self,
                    )
                    return
                self.current_project = self.app.projects.create(name)
                self.current_slug = self.current_project["slug"]
                created_new = True
            project = self._update_project_from_form()
            if change is not None:
                project.setdefault("change_log", []).append(change)
            self.additional_data_panel.update_project_for_save(project, new_project=created_new)
            result = self.app.projects.save(self.current_slug, project)
            self.current_project = result["project"]
            if change is not None:
                self._clear_saved_change_draft()
            saved_slug = self.current_slug
            self.refresh_projects()
            self._select_project_by_slug(saved_slug)
            message = ("Новый объект создан; паспорт и дополнительные данные сохранены"
                       if created_new else "Паспорт объекта и дополнительные данные сохранены")
            if change is not None:
                message += ". Запись добавлена в историю изменений объекта"
            messagebox.showinfo("Сохранено", message, parent=self)
        except Exception as exc:
            if created_new:
                self.refresh_projects()
                self._select_project_by_slug(self.current_slug, load_form=False)
            messagebox.showerror("Ошибка", str(exc), parent=self)

    def validate_project(self) -> None:
        if not self.current_slug:
            return
        project = self._update_project_from_form()
        errors, warnings = self.app.projects.validate(project, require_ready=True)
        lines = ["Ошибки:"] + [f"• {value}" for value in errors] + ["", "Предупреждения:"] + [f"• {value}" for value in warnings]
        if not errors and not warnings:
            lines = ["Паспорт готов к созданию задания."]
        messagebox.showinfo("Проверка паспорта", "\n".join(lines), parent=self)

    def choose_dwg(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Выберите DWG", filetypes=[("AutoCAD DWG", "*.dwg"), ("Все файлы", "*.*")])
        if path:
            self.fields["dwg"].set(path)

    def open_project_folder(self) -> None:
        if self.current_slug:
            os.startfile(self.app.projects.project_dir(self.current_slug))

    def open_project_json(self) -> None:
        if self.current_slug:
            os.startfile(self.app.projects.project_dir(self.current_slug) / "project.json")

    def create_run(self) -> None:
        if not self.current_slug:
            messagebox.showwarning("Запуск", "Сначала выберите объект", parent=self)
            return
        try:
            candidate_project = self._update_project_from_form()
            variant_values = getattr(self, "task_variant_values", {"Полная проверка с AutoCAD": "full_execution"})
            variant_var = getattr(self, "task_variant_var", None)
            variant_label = variant_var.get() if variant_var is not None else "Полная проверка с AutoCAD"
            task_variant = variant_values[variant_label]
            # Normative inventory covers the whole selected object, regardless of the work mode shown above.
            mode = "compliance" if task_variant == "object_norms" else self.mode_values[self.mode_var.get()]
            baseline = self.app.runs.comparison_baseline(self.current_slug, mode, task_variant)
            plan_change_status = None
            plan_change_note = ""
            if baseline:
                answer = messagebox.askyesnocancel(
                    "Планы для повторной проверки",
                    "Сравниваем с непосредственно предыдущей итерацией этого же вида работы: "
                    f"№ {baseline['work_iteration']}, запуск {baseline['run_id']}.\n\n"
                    "Для новой итерации представлены новые или изменённые планы относительно этой предыдущей?\n\n"
                    "Да — планы новые/изменены; Нет — планы прежние; Отмена — не создавать задание.",
                    parent=self,
                )
                if answer is None:
                    return
                plan_change_status = "new" if answer else "same"
                if answer:
                    plan_change_note = simpledialog.askstring(
                        "Что изменилось в планах?",
                        "Если известно, укажите изменённые листы, этажи, помещения или разделы. "
                        "Можно оставить пустым — помощник уточнит это при сравнении.",
                        parent=self,
                    ) or ""
            saved = self.app.projects.save(self.current_slug, candidate_project)
            self.current_project = saved["project"]
            result = self.app.runs.create(self.current_slug, mode, task_variant,
                                          plan_change_status=plan_change_status,
                                          plan_change_note=plan_change_note)
            self.refresh_runs()
            self.run_tree.selection_set(result["run"]["run_id"])
            self.run_tree.see(result["run"]["run_id"])
            handoff = self.app.runs.chat_handoff(self.current_slug, result["run"]["run_id"])
            if hasattr(self, "clipboard_clear"):
                self.clipboard_clear()
                self.clipboard_append(handoff["text"])
                if hasattr(self, "update_idletasks"):
                    self.update_idletasks()
            self.run_info.delete("1.0", "end")
            self.run_info.insert("1.0", f"Задание создано и скопировано в буфер обмена:\n{result['task_file']}\n\n"
                f"Объект: {self.current_slug}\nЗапуск: {result['run']['run_id']}\n\n"
                f"Результат: {result['run']['task_variant_label']}\n\n"
                f"Итерация этого вида: {result['run'].get('plan_revision', {}).get('work_iteration', 1)}\n"
                f"Планы: {result['run'].get('plan_revision', {}).get('status', 'не уточнено')}\n"
                f"Сравнение с запуском: {result['run'].get('plan_revision', {}).get('comparison_run_id', '') or 'нет'}\n\n"
                "Вставьте текст в чат объекта. В нём уже находятся снимок паспорта, все относящиеся правила, "
                "требования к выбранному результату и контроль качества.")
            messagebox.showinfo("Задание готово", "Задание создано и скопировано в буфер обмена.", parent=self)
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self)

    def refresh_runs(self) -> None:
        selected=self._selected_run_id() if getattr(self,"_runs_slug",None)==self.current_slug else ""
        self._runs_slug=self.current_slug
        for item in self.run_tree.get_children():
            self.run_tree.delete(item)
        if not self.current_slug:
            return
        plan_labels = {"initial": "первый прогон", "same": "те же", "new": "новые/изменены",
                       "unconfirmed": "не уточнено", "unknown": "нет данных"}
        for row in self.app.runs.list_runs(self.current_slug):
            self.run_tree.insert("", "end", iid=row["run_id"], values=(
                row["mode_label"], row["work_iteration"], row["task_variant_label"],
                plan_labels.get(row["plan_change_status"], row["plan_change_status"]),
                row["status"], row["created_at"]
            ))
        if selected and self.run_tree.exists(selected):
            self.run_tree.selection_set(selected)

    def copy_run_task(self) -> None:
        run_id = self._selected_run_id()
        if not self.current_slug or not run_id:
            messagebox.showwarning("Задание", "Выберите объект и запуск", parent=self)
            return
        try:
            handoff = self.app.runs.chat_handoff(self.current_slug, run_id)
            self.clipboard_clear()
            self.clipboard_append(handoff["text"])
            message = "Задание с паспортом, правилами, выбранным результатом и контролем качества скопировано. Вставьте его в чат объекта."
            if handoff["warnings"]:
                message = "\n".join(handoff["warnings"]) + "\n\nТекст скопирован с предупреждением об устаревшем задании."
            self.run_info.delete("1.0", "end")
            self.run_info.insert("1.0", message + "\n\nФайл задания: " + handoff["task_file"])
            self.status_var.set(message)
        except Exception as exc:
            messagebox.showerror("Задание", str(exc), parent=self)

    def _selected_run_id(self) -> str:
        selected = self.run_tree.selection()
        return selected[0] if selected else ""

    def open_run_folder(self) -> None:
        run_id = self._selected_run_id()
        if run_id and self.current_slug:
            os.startfile(self.app.runs.run_dir(self.current_slug, run_id))

    def open_project_history(self) -> None:
        if not self.current_slug:
            messagebox.showwarning("Реестр объекта", "Сначала выберите объект", parent=self)
            return
        try:
            self.app.runs.refresh_project_history(self.current_slug)
            os.startfile(self.app.projects.project_dir(self.current_slug) / "ИСТОРИЯ_ОЦЕНОК.json")
        except Exception as exc:
            messagebox.showerror("Реестр объекта", str(exc), parent=self)

    def refresh_norm_status(self) -> None:
        status = self.app.norms.status()
        self.norm_status.delete("1.0", "end")
        self.norm_status.insert("1.0", json.dumps(status, ensure_ascii=False, indent=2))

    def _background(self, label: str, function) -> None:
        if self._norm_update_running:
            self.status_var.set("Обновление нормативной базы уже выполняется…")
            return
        self._norm_update_running = True
        self._norm_update_started = time.time()
        self._norm_progress_polled = 0.0
        for button in self.norm_update_buttons:
            button.state(["disabled"])
        self.status_var.set(label)

        def worker() -> None:
            try:
                result = function()
                self._background_results.put(("done", result))
            except NormativeUpdateBusy as exc:
                self._background_results.put(("busy", str(exc)))
            except Exception as exc:
                detail = f"{exc}\n\n{traceback.format_exc()}"
                self._background_results.put(("error", detail))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll_background)

    def _poll_background(self) -> None:
        # All Tk calls stay on the main thread, including completion/error callbacks.
        try:
            state, result = self._background_results.get_nowait()
        except queue.Empty:
            now = time.time()
            if now - self._norm_progress_polled >= 1:
                self._norm_progress_polled = now
                path = self.app.paths.data / "index/progress.json"
                try:
                    if path.exists() and path.stat().st_mtime >= self._norm_update_started:
                        progress = read_json(path, {})
                        self.status_var.set(
                            f"Прочитано файлов: {progress['processed']} из {progress['total']}. "
                            "Распознавание сканов может занять время…"
                        )
                except (OSError, ValueError, KeyError):
                    # A status display must not interrupt the actual background job.
                    pass
            self.after(100, self._poll_background)
            return
        try:
            if state == "done":
                self._background_done(result)
            elif state == "busy":
                self.status_var.set("Нормативная база обновляется в другом окне")
                messagebox.showinfo("Обновление нормативной базы", result, parent=self)
            else:
                self._background_error(result)
        except Exception as exc:
            self._background_error(f"{exc}\n\n{traceback.format_exc()}")
        finally:
            self._norm_update_running = False
            for button in self.norm_update_buttons:
                button.state(["!disabled"])

    def _background_done(self, result: object) -> None:
        self.status_var.set("Готово")
        if isinstance(result, dict):
            index = result.get("index", result)
            if isinstance(index, dict) and index.get("full_text_coverage_complete") is False:
                self.status_var.set(
                    f"Индекс обновлён. Требуют внимания: {index.get('documents_requiring_attention', 0)} документов"
                )
        self.refresh_norm_status()
        self.norm_results.delete("1.0", "end")
        self.norm_results.insert("1.0", json.dumps(result, ensure_ascii=False, indent=2))

    def _background_error(self, detail: str) -> None:
        self.status_var.set("Ошибка")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        log_path = self.app.paths.logs / f"norms-update-{stamp}.log"
        message = detail.splitlines()[0] if detail else "Не удалось обновить нормативную базу"
        try:
            atomic_write_text(log_path, detail)
            message += f"\n\nПодробности ошибки сохранены:\n{log_path}"
        except OSError:
            message += "\n\n" + detail
        messagebox.showerror("Ошибка нормативной базы", message, parent=self)

    def sync_norms(self) -> None:
        self._background(
            "Обновление и чтение всех документов и писем, включая сканы…",
            lambda: self.app.norms.sync_repository(rebuild_index=True, full_text=True, full_text_letters=True),
        )

    def index_norms(self) -> None:
        self._background("Чтение всей базы и распознавание сканов…", lambda: self.app.norms.rebuild(full_text=True, full_text_letters=True))

    def search_norms(self) -> None:
        results = self.app.norms.search(self.norm_query.get(), limit=50)
        self.norm_results.delete("1.0", "end")
        self.norm_results.insert("1.0", json.dumps(results, ensure_ascii=False, indent=2))

    def load_knowledge_file(self, event=None) -> None:
        selected = self.knowledge_list.curselection()
        if not selected:
            return
        name = self.knowledge_list.get(selected[0])
        path = self.app.paths.knowledge / name
        self.knowledge_editor.delete("1.0", "end")
        self.knowledge_editor.insert("1.0", path.read_text(encoding="utf-8-sig"))

    def save_knowledge_file(self) -> None:
        selected = self.knowledge_list.curselection()
        if not selected:
            return
        name = self.knowledge_list.get(selected[0])
        path = self.app.paths.knowledge / name
        text = self.knowledge_editor.get("1.0", "end").rstrip() + "\n"
        try:
            self.app.knowledge.validate_document(name, text)
        except ValueError as exc:
            messagebox.showerror("Проверка правила", f"Файл не сохранён: {exc}", parent=self)
            return
        if not messagebox.askyesno("Постоянное правило", f"Сохранить изменения в {name}?\nБудет создана резервная копия.", parent=self):
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.app.paths.logs / f"{name}.{stamp}.bak"
        shutil.copy2(path, backup)
        atomic_write_text(path, text)
        self.status_var.set(f"Сохранено: {name}; резервная копия: {backup.name}")


if __name__ == "__main__":
    FireAuditGUI().mainloop()
