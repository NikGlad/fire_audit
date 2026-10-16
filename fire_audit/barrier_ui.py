from __future__ import annotations

import copy
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .barrier_inputs import (inputs_for, validate_inputs, read_category_workbook,
                             merge_category_rows, snapshot_attachment, room_key,
                             snapshot_manual_categories, category_values, fire_pump_text_for_ui)
from .barriers import preparation_errors, CATEGORIES
from .io_utils import read_json
from .control import project_digest


class AdditionalDataPanel(ttk.Frame):
    def __init__(self, gui):
        super().__init__(gui.tabs, padding=12)
        self.gui = gui
        gui.tabs.add(self, text="Дополнительные данные")
        self.slug = ""
        self.data = inputs_for({})
        self.loaded_digest = ""
        self.categories_dirty = False
        self.message = tk.StringVar(value="Выберите объект. Данные этой вкладки используются во всех видах проверок выбранного объекта.")
        ttk.Label(self, textvariable=self.message, wraplength=950).pack(fill="x", pady=(0, 8))
        header = ttk.Frame(self); header.pack(fill="x")
        ttk.Button(header, text="Сохранить дополнительные данные", command=self.save).pack(side="left", padx=3)
        ttk.Button(header, text="Проверить данные", command=self.readiness).pack(side="left", padx=3)
        tabs = ttk.Notebook(self); tabs.pack(fill="both", expand=True, pady=8)
        data = ttk.Frame(tabs, padding=8); tabs.add(data, text="Вводные и документы")
        rooms = ttk.Frame(tabs, padding=8); tabs.add(rooms, text="Помещения и категории")
        special = ttk.Frame(tabs, padding=8); tabs.add(special, text="Уточнения")
        self.special_texts = {}
        for key, label in [("lifts_and_hoists", "Лифты, подъёмники, шахты и холлы: наличие, помещения / оси"),
                           ("corridor_division", "Деление коридоров: замечания, принятый вариант, расположение перегородок"),
                           ("slabs_and_openings", "Перекрытия, двери, окна, ворота и шторы: специальные условия"),
                           ("storage_and_equipment", "Хранение выше 5,5 м, состав хранения, оборудование и недостающие сведения")]:
            ttk.Label(special, text=label, wraplength=920).pack(anchor="w", pady=(6,2))
            widget = tk.Text(special, height=3, wrap="word", font=("Segoe UI", 10))
            widget.pack(fill="both", expand=True)
            gui._enable_text_clipboard(widget, editable=True)
            self.special_texts[key] = widget
        self.flags = {}
        for key, label in [
            ("categories_approved", "Категории утверждены"),
            ("additional_documents_reviewed", "Дополнительные документы и ответы заказчика проверены"),
            ("preparation_completed", "Подготовка исходных планов завершена"),
            ("source_explanations_reviewed", "Замечания и ответы в слое пояснений прочитаны"),
            ("room_match_completed", "Помещения таблицы сопоставлены с планами"),
            ("refrigeration_equipment_rooms_confirmed", "Помещения с холодильным оборудованием уточнены")]:
            self.flags[key] = tk.BooleanVar(value=False)
            ttk.Checkbutton(data, text=label, variable=self.flags[key]).pack(anchor="w", pady=2)
        fields = ttk.Frame(data); fields.pack(fill="x", pady=8); fields.columnconfigure(1, weight=1)
        self.values = {}
        for row, (key, label, options) in enumerate([
            ("category_approval_source", "Кем / каким сообщением утверждены категории", None),
            ("stu_status", "СТУ", ["Не установлено", "Отсутствуют", "Представлены"]),
            ("refrigeration_present", "Холодильное оборудование", ["Не установлено", "Есть", "Нет"]),
            ("refrigeration_equipment_rooms", "Помещения с холодильным оборудованием (через запятую)", None),
            ("special_conditions", "Особые условия, решения заказчика и недостающие сведения", None)]):
            self.values[key] = tk.StringVar()
            ttk.Label(fields, text=label, wraplength=390).grid(row=row, column=0, sticky="w", pady=4)
            widget = ttk.Combobox(fields, textvariable=self.values[key], values=options, state="readonly") if options else ttk.Entry(fields, textvariable=self.values[key])
            widget.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
            gui._enable_widget_clipboard(widget, editable=not options)
        row = len(self.values)
        self.pump_text = tk.StringVar()
        ttk.Label(fields, text="Насосные АУП/ВПВ: наличие, помещения, этажи / оси и источник сведений",
                  wraplength=390).grid(row=row, column=0, sticky="w", pady=4)
        pump_widget = ttk.Entry(fields, textvariable=self.pump_text)
        pump_widget.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        gui._enable_widget_clipboard(pump_widget, editable=True)
        files = ttk.Frame(data); files.pack(fill="x")
        for label, role in [("Добавить СТУ", "stu"), ("Добавить другой документ", "additional")]:
            ttk.Button(files, text=label, command=lambda r=role: self.attach(r)).pack(side="left", padx=3)
        ttk.Button(files, text="Убрать выбранный документ", command=self.remove_attachment).pack(side="left", padx=3)
        self.attachments = tk.Listbox(data, height=4); self.attachments.pack(fill="both", expand=True, pady=7)
        actions = ttk.Frame(rooms); actions.pack(fill="x")
        for label, command in [("Загрузить категории из Excel", self.import_excel), ("Добавить помещение", self.add_room),
                               ("Изменить выбранное", self.edit_room), ("Убрать из таблицы", self.remove_room)]:
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=3)
        self.table = ttk.Treeview(rooms, columns=("floor", "number", "name", "area", "category", "part", "height"), show="headings", height=15)
        for key, label, width in [("floor", "Этаж", 80), ("number", "№", 55), ("name", "Помещение", 270),
                                 ("area", "м²", 75), ("category", "Категория", 75), ("part", "Класс части", 90), ("height", "Хранение, м", 90)]:
            self.table.heading(key, text=label); self.table.column(key, width=width, stretch=key=="name")
        self.table.pack(fill="both", expand=True, pady=8)
        gui._enable_tree_copy(self.table)
        self.table.bind("<Double-1>", lambda e: self.edit_room())
        ttk.Label(rooms, text="С / П в подписи вычисляется по названию. Категорию программа не рассчитывает. Класс части здания задаётся отдельно.", wraplength=940).pack(anchor="w")
        gui._tab_scroll_targets[self] = self.table
        self.load_project()

    def load_project(self):
        self.slug = self.gui.current_slug
        project = self.gui.current_project
        self.loaded_digest = project_digest(project)
        self.data = inputs_for(project)
        self.categories_dirty = False
        self.pump_text.set(fire_pump_text_for_ui(self.data))
        for key, widget in self.special_texts.items():
            widget.delete("1.0", "end"); widget.insert("1.0", self.data.get(key, ""))
        for key, var in self.flags.items():
            var.set(self.data.get(key) is True)
        self.values["category_approval_source"].set(self.data.get("category_approval_source", ""))
        self.values["stu_status"].set({"unknown": "Не установлено", "absent": "Отсутствуют", "provided": "Представлены"}[self.data["stu_status"]])
        self.values["refrigeration_present"].set({True:"Есть", False:"Нет", None:"Не установлено"}[self.data.get("refrigeration_present")])
        self.values["refrigeration_equipment_rooms"].set(", ".join(self.data.get("refrigeration_equipment_rooms", [])))
        self.values["special_conditions"].set(self.data.get("special_conditions", ""))
        self.refresh_tables()
        self.message.set(
            f"{project.get('name', self.slug)} — помещения: {len(self.data['rooms'])}. Дополнительные данные используются во всех видах проверок этого объекта; после сохранения создайте новое задание."
            if self.slug else "Выберите объект. Данные этой вкладки используются во всех видах проверок выбранного объекта.")

    def collect(self):
        data = copy.deepcopy(self.data)
        data["fire_pump_rooms_text"] = self.pump_text.get().strip()
        for key, var in self.flags.items():
            data[key] = var.get()
        data["category_approval_source"] = self.values["category_approval_source"].get().strip()
        data["stu_status"] = {"Не установлено":"unknown", "Отсутствуют":"absent", "Представлены":"provided"}[self.values["stu_status"].get()]
        data["refrigeration_present"] = {"Не установлено":None, "Есть":True, "Нет":False}[self.values["refrigeration_present"].get()]
        data["refrigeration_equipment_rooms"] = [x.strip() for x in self.values["refrigeration_equipment_rooms"].get().split(",") if x.strip()]
        data["special_conditions"] = self.values["special_conditions"].get().strip()
        for key, widget in self.special_texts.items():
            data[key] = widget.get("1.0", "end").strip()
        return validate_inputs(data)

    def update_project_for_save(self, project, *, new_project=False):
        """Include this panel's draft before either save button reloads the forms."""
        if not self.gui.current_slug or (
                self.slug != self.gui.current_slug and not (new_project and not self.slug)):
            raise ValueError("Выберите объект")
        if not new_project and project_digest(
                self.gui.app.projects.load(self.slug)) != self.loaded_digest:
            raise ValueError("Паспорт изменён после открытия дополнительных данных. "
                             "Сохраните несохранённые правки отдельно и повторно выберите объект. "
                             "Текущие данные на диске не перезаписаны.")
        data = self.collect()
        if self.categories_dirty:
            data["category_source"] = snapshot_manual_categories(
                self.gui.app.paths, self.gui.current_slug, data)
        project["barrier_assessment"] = data

    def save(self):
        try:
            project = self.gui._update_project_from_form()
            self.update_project_for_save(project)
            self.gui.current_project = self.gui.app.projects.save(self.slug, project)["project"]
            self.gui._load_project_form()
            self.message.set("Дополнительные данные сохранены для всех видов проверок этого объекта. Создайте новое задание.")
        except Exception as exc:
            messagebox.showerror("Дополнительные данные", str(exc), parent=self)

    def readiness(self):
        try:
            if not self.slug or self.slug != self.gui.current_slug:
                raise ValueError("Выберите объект")
            p = self.gui._update_project_from_form()
            p["barrier_assessment"] = self.collect()
            mode = self.gui.mode_values.get(self.gui.mode_var.get())
            errors = []
            if mode == "barrier_assessment":
                profile = read_json(self.gui.app.paths.knowledge / "barrier_drawing_profile.json")
                errors = preparation_errors(p, profile)
            message = ("Для выбранного вида «Оценка Преград» необходимо уточнить:\n" + "\n".join(errors)
                       if errors else "Формат дополнительных данных проверен. Данные доступны всем видам проверок этого объекта. "
                       "Это не подтверждение полноты исходных материалов: её помощник проверяет по выбранному виду работы. "
                       "Для передачи изменений сохраните данные и создайте новое задание.")
            messagebox.showinfo("Дополнительные данные", message, parent=self)
        except Exception as exc:
            messagebox.showerror("Дополнительные данные", str(exc), parent=self)

    def refresh_tables(self):
        self.table.delete(*self.table.get_children())
        for idx, r in enumerate(self.data["rooms"]):
            self.table.insert("", "end", iid=str(idx), values=(r["floor"], r["number"], r["name"], r.get("area_m2", ""), r["category"], r.get("host_part_functional_class", ""), r.get("storage_height_m", "")))
        self.attachments.delete(0, "end")
        for f in self.data.get("attachments", []):
            self.attachments.insert("end", ("СТУ: " if f.get("role")=="stu" else "Документ: ") + Path(f["path"]).name)

    def attach(self, role):
        try:
            self._attach(role)
        except Exception as exc:
            messagebox.showerror("Документ", str(exc), parent=self)

    def remove_attachment(self):
        if not self.attachments.curselection():
            return
        item = self.data["attachments"].pop(self.attachments.curselection()[0])
        if item.get("role") == "stu":
            self.data["stu_documents"] = [p for p in self.data["stu_documents"] if p != item["path"]]
            if not self.data["stu_documents"]:
                self.values["stu_status"].set("Не установлено")
        self.flags["additional_documents_reviewed"].set(False)
        self.refresh_tables()

    def _attach(self, role):
        if not self.slug:
            return
        path = filedialog.askopenfilename(parent=self, title="Документ объекта")
        if not path:
            return
        item = snapshot_attachment(self.gui.app.paths, self.slug, path, role)
        if not any(a.get("sha256")==item["sha256"] and a.get("role")==role for a in self.data["attachments"]):
            self.data["attachments"].append(item)
        if role == "stu":
            if item["path"] not in self.data["stu_documents"]:
                self.data["stu_documents"].append(item["path"])
            self.values["stu_status"].set("Представлены")
        self.flags["additional_documents_reviewed"].set(False)
        self.refresh_tables()

    def import_excel(self):
        path = filedialog.askopenfilename(parent=self, title="Утверждённая таблица категорий", filetypes=[("Таблица Excel", "*.xlsx")])
        if not path or not self.slug:
            return
        try:
            result = read_category_workbook(path)
            removed = set(room_key(r) for r in self.data["rooms"]) - set(room_key(r) for r in result["rooms"])
            message = f"Найдено помещений: {len(result['rooms'])}. Загрузить эту таблицу?\nУтверждение категорий потребуется подтвердить заново."
            if result["skipped_sheets"]:
                message += "\nЛисты без таблицы категорий: " + ", ".join(result["skipped_sheets"])
            if removed:
                message += "\nВ новой таблице отсутствуют: " + ", ".join(sorted(removed))
            if not messagebox.askyesno("Загрузка категорий", message, parent=self):
                return
            self.data["rooms"] = merge_category_rows(self.data["rooms"], result["rooms"])
            self.data["category_source"] = snapshot_attachment(self.gui.app.paths, self.slug, path, "categories")
            self.categories_dirty = False
            self.flags["categories_approved"].set(False)
            self.flags["room_match_completed"].set(False)
            self.refresh_tables()
        except Exception as exc:
            messagebox.showerror("Категории", str(exc), parent=self)

    def add_room(self):
        self.room_dialog(None)

    def edit_room(self):
        if self.table.selection():
            self.room_dialog(int(self.table.selection()[0]))

    def remove_room(self):
        if self.table.selection():
            index = int(self.table.selection()[0])
            if messagebox.askyesno("Помещение", "Убрать помещение из вводимой таблицы? Исходный Excel останется неизменным.", parent=self):
                self.data["rooms"].pop(index)
                self.categories_dirty = True
                self.flags["categories_approved"].set(False)
                self.flags["room_match_completed"].set(False)
                self.refresh_tables()

    def room_dialog(self, index):
        value = copy.deepcopy(self.data["rooms"][index]) if index is not None else {}
        window = tk.Toplevel(self); window.title("Помещение — дополнительные данные"); window.geometry("790x670")
        form = ttk.Frame(window, padding=14); form.pack(fill="both", expand=True); form.columnconfigure(1, weight=1)
        fields = {}
        specs = [("floor", "Этаж", None), ("number", "Номер", None), ("name", "Наименование", None),
            ("category", "Утверждённая категория", sorted(CATEGORIES)), ("area_m2", "Площадь, м²", None),
            ("host_part_functional_class", "Класс части здания", ["", "Ф4.3", "Ф5.1", "Ф5.2", "другое"]),
            ("host_part_description", "Часть здания / АБК", None), ("equipment_description", "Оборудование", None),
            ("storage_height_m", "Высота хранения, м", None), ("storage_description", "Состав хранения / горючие газы / ЛВЖ", None),
            ("special_conditions", "Уточнения и ответы заказчика", None)]
        for row, (key, label, options) in enumerate(specs):
            fields[key] = tk.StringVar(value="" if value.get(key) is None else str(value.get(key, "")))
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=7)
            widget = ttk.Combobox(form, textvariable=fields[key], values=options, state="readonly") if options else ttk.Entry(form, textvariable=fields[key])
            widget.grid(row=row, column=1, sticky="ew", padx=8, pady=7)
            self.gui._enable_widget_clipboard(widget, editable=not options)
        def save():
            try:
                proposed = {**value, **{k:v.get().strip() for k,v in fields.items()}}
                rows = copy.deepcopy(self.data["rooms"])
                if index is None:
                    rows.append(proposed)
                else:
                    rows[index] = proposed
                data = {**self.data, "rooms": rows, "categories_approved": False}
                checked = validate_inputs(data)["rooms"]
                if category_values(self.data["rooms"]) != category_values(checked):
                    self.categories_dirty = True
                    self.flags["categories_approved"].set(False)
                    self.flags["room_match_completed"].set(False)
                    for row in checked:
                        for key in ("matched_cad_handles", "cad_match_status", "category_entity_handle"):
                            row.pop(key, None)
                self.data["rooms"] = checked
                self.refresh_tables(); window.destroy()
            except Exception as exc:
                messagebox.showerror("Помещение", str(exc), parent=window)
        ttk.Button(form, text="Сохранить помещение", command=save).grid(row=len(specs), column=1, sticky="e", pady=15)


# Preserve imports used by earlier integrations; this is one shared form.
BarrierPanel = AdditionalDataPanel
