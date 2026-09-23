from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, filedialog

from .control import STATE_LABELS
from .io_utils import read_json
from . import barrier_control


class ControlPanel(ttk.Frame):
    def __init__(self, gui):
        super().__init__(gui.tabs, padding=12)
        self.gui = gui
        gui.tabs.add(self, text="Контроль")
        self.busy = False
        self.scope = None
        self.mode = None
        self.pp_data = None
        self._refresh_pending = False
        self.summary = tk.StringVar(value="Выберите объект и запуск во вкладке «Проверка», затем нажмите «Обновить».")
        ttk.Label(self, textvariable=self.summary, wraplength=850).pack(fill="x", pady=6)
        buttons = ttk.Frame(self); buttons.pack(fill="x")
        self.buttons = {}
        for name, command in (("Обновить", self.refresh), ("Следующий шаг", self.next_step),
                              ("Модель здания", self.model), ("План изменений", self.plan),
                              ("Версия правил", self.rules), ("Тесты качества", self.quality)):
            button=ttk.Button(buttons, text=name, command=command)
            button.pack(side="left", padx=2, pady=4)
            self.buttons[name]=button
        self.pp_actions=ttk.Frame(self)
        ttk.Button(self.pp_actions,text="Дополнительные данные",command=self.barrier_inputs).pack(side="left",padx=2,pady=4)
        self.pp_verify=ttk.Button(self.pp_actions,text="Проверить схему ПП",command=self.verify_barrier)
        self.pp_verify.pack(side="left",padx=2,pady=4)
        self.table = ttk.Treeview(self, columns=("block", "floor", "title", "status"), show="headings", height=9)
        for key, label, width in (("block", "Блок", 55), ("floor", "Этаж", 65), ("title", "Замечание", 410), ("status", "Следующий этап", 180)):
            self.table.heading(key, text=label); self.table.column(key, width=width)
        self.table.pack(fill="both", expand=True, pady=8)
        gui._enable_tree_copy(self.table)
        self.detail = tk.Text(self, wrap="word", height=12, font=("Segoe UI", 10))
        self.detail.pack(fill="both", expand=True)
        gui._enable_text_clipboard(self.detail, editable=False)
        gui._tab_scroll_targets[self] = self.detail
        self.table.bind("<<TreeviewSelect>>", self.show_item)
        gui.run_tree.bind("<<TreeviewSelect>>", self.schedule_refresh, add="+")
        gui.tabs.bind("<<NotebookTabChanged>>", self.tab_changed, add="+")

    def schedule_refresh(self, event=None):
        if not self._refresh_pending:
            self._refresh_pending=True
            self.after_idle(self._scheduled_refresh)

    def _scheduled_refresh(self):
        self._refresh_pending=False
        self.refresh()

    def tab_changed(self, event=None):
        if self.gui.tabs.select()==str(self):
            self.schedule_refresh()

    def set_mode(self, mode):
        self.mode=mode
        pp=mode=="barrier_assessment"
        self.buttons["Модель здания"].configure(text="Помещения ПП" if pp else "Модель здания")
        self.buttons["План изменений"].configure(text="План схемы ПП" if pp else "План изменений")
        self.buttons["Тесты качества"].configure(text="Проверки качества ПП" if pp else "Тесты качества")
        for key,label in (("block","№" if pp else "Блок"),("floor","Область" if pp else "Этаж"),
                          ("title","Этап оценки преград" if pp else "Замечание"),("status","Состояние" if pp else "Следующий этап")):
            self.table.heading(key,text=label)
        if pp:self.pp_actions.pack(fill="x",before=self.table)
        else:self.pp_actions.pack_forget()

    def context(self):
        slug, run_id = self.gui.current_slug, self.gui._selected_run_id()
        if not slug or not run_id:
            raise ValueError("Выберите объект и запуск во вкладке «Проверка»")
        return slug, run_id

    def show(self, value):
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))

    def refresh(self):
        try:
            self.table.delete(*self.table.get_children())
            self.pp_data=None
            self.scope = self.context()
            run=self.gui.app.runs.load(*self.scope)["run"]
            self.set_mode(run["mode"])
            if self.mode=="barrier_assessment":
                self.pp_data=barrier_control.snapshot(self.gui.app,*self.scope)
                for index,step in enumerate(self.pp_data["steps"],1):
                    self.table.insert("","end",iid=step["id"],values=(index,"ПП",step["title"],step["status"]))
                self.pp_verify.configure(state="normal" if self.pp_data["can_verify"] and not self.busy else "disabled")
                self.summary.set(self.pp_data["summary"])
                self.show(barrier_control.overview(self.pp_data))
                return
            state = self.gui.app.control.load(*self.scope)
            status = self.gui.app.control.status(*self.scope)
            for row in self.table.get_children():
                self.table.delete(row)
            for item in state["inventory"]:
                self.table.insert("", "end", iid=item["id"], values=(item["block_order"], item["floor"], item["title"], STATE_LABELS[item["state"]]))
            self.summary.set(f"Завершено {status['verified']} из {status['total']}. Ожидают данных: {len(status['waiting_for_data'])}. "
                             + (" ".join(status["errors"]) or "Версия правил и паспорт соответствуют запуску."))
            self.show("Выполнение изменений — только после просмотра плана и вашего подтверждения.\n"
                      "Программа не блокирует действия в AutoCAD, выполненные вне неё.\n"
                      "Чертёж автоматически не сохраняется и не очищается.")
        except Exception as exc:
            self.scope=None;self.set_mode(None)
            self.show(str(exc)); self.summary.set("Контроль: выберите доступный объект и запуск")

    def show_item(self, event=None):
        if self.scope and self.table.selection():
            try:current=self.context()
            except ValueError:current=None
            if self.scope!=current:
                self.refresh();return
            if self.mode=="barrier_assessment":
                if self.pp_data:
                    item=next((i for i in self.pp_data["steps"] if i["id"]==self.table.selection()[0]),None)
                    if item:self.show(item["title"]+"\n"+item["detail"]+"\n\n"+self.pp_data["next_step"])
                return
            state = self.gui.app.control.load(*self.scope)
            self.show(next(i for i in state["inventory"] if i["id"] == self.table.selection()[0]))

    def next_step(self):
        try:
            slug, run_id = self.context()
            run = self.gui.app.runs.load(slug, run_id)["run"]
            if run["mode"] == "barrier_assessment":
                handoff = self.gui.app.runs.chat_handoff(slug, run_id)
                self.gui.clipboard_clear(); self.gui.clipboard_append(handoff["text"])
                message = "Полное задание оценки преград скопировано для продолжения в чате."
                if handoff["warnings"]:
                    message = "\n".join(handoff["warnings"])
                data=barrier_control.snapshot(self.gui.app,slug,run_id)
                self.show(message + "\n\nСледующее действие:\n"+data["next_step"]+
                          "\nПросмотр решений, построение копии и повторная сверка доступны здесь, во вкладке «Контроль».")
                self.summary.set(message)
                return
            result = self.gui.app.control.status(slug, run_id)
            self.show(result)
            text = (f"Продолжи контролируемую проверку объекта {slug}, запуск {run_id}. "
                    "Сначала вызови get_next_review_task. Выполняй только текущий этап, не изменяй DWG напрямую. "
                    "Нормы читай через read_requirement_source; изменения готовь через prepare_cad_plan.")
            self.gui.clipboard_clear(); self.gui.clipboard_append(text)
            self.summary.set("Задание для продолжения скопировано. Вставьте его в чат с подключением fire-audit.")
        except Exception as exc:
            self.show(str(exc))

    def preview(self, title, text):
        window = tk.Toplevel(self)
        window.title(title); window.geometry("950x650")
        body = tk.Text(window, wrap="word", font=("Segoe UI", 10))
        body.pack(fill="both", expand=True, padx=10, pady=10)
        body.insert("1.0", text)
        self.gui._enable_text_clipboard(body, editable=False)
        actions = ttk.Frame(window); actions.pack(fill="x", padx=10, pady=10)
        ttk.Button(actions, text="Закрыть", command=window.destroy).pack(side="right")
        return window, actions

    def model(self):
        try:
            scope = self.context()
            if self.gui.app.runs.load(*scope)["run"]["mode"]=="barrier_assessment":
                data=barrier_control.snapshot(self.gui.app,*scope)
                self.preview("Помещения и границы ПП",barrier_control.rooms_text(data))
                return
            state = self.gui.app.control.load(*scope)
            text = "Проверьте назначение объектов по планам. Геометрические данные сами по себе не доказывают, что объект — дверь или лестница.\n\n"
            for node in state["nodes"]:
                text += f"{node['id']} | этаж {node['floor']} | {node['kind']}\nОснование: {node['recognition_basis']}\nОбъекты AutoCAD: {', '.join(node['entity_ids'])}\n\n"
            window, buttons = self.preview("Подтверждение модели здания", text or "Модель не заполнена")
            def confirm():
                try:
                    if messagebox.askyesno("Модель здания", "Вы проверили назначение всех перечисленных объектов по планам и подтверждаете его?", parent=window):
                        self.gui.app.control.confirm_model_from_ui(*scope, state["model_revision"])
                        window.destroy(); self.refresh()
                except Exception as exc:
                    messagebox.showerror("Модель", str(exc), parent=window)
            ttk.Button(buttons, text="Подтвердить назначение объектов", command=confirm).pack(side="left")
        except Exception as exc:
            self.show(str(exc))

    def background(self, function, done):
        if self.busy or getattr(getattr(self.gui,"barrier_panel",None),"busy",False):
            messagebox.showinfo("Подождите", "Предыдущая операция ещё выполняется", parent=self)
            return
        self.busy = True
        results = queue.Queue()
        def work():
            try:
                results.put((True, function()))
            except Exception as exc:
                results.put((False, str(exc)))
        threading.Thread(target=work, daemon=True).start()
        def poll():
            try:
                ok, value = results.get_nowait()
            except queue.Empty:
                self.after(150, poll); return
            self.busy = False
            if ok:
                done(value)
            else:
                self.summary.set("Операция остановлена; причина показана ниже.")
                self.show(value)
        poll()

    def plan(self):
        try:
            scope = self.context()
            if self.gui.app.runs.load(*scope)["run"]["mode"]=="barrier_assessment":
                self.barrier_plan(scope)
                return
            state = self.gui.app.control.load(*scope)
            plans = [p for p in state["plans"].values() if p["status"] == "awaiting_user"]
            if not plans:
                self.show("Нет подготовленного плана изменений. Чат должен сначала завершить анализ и вызвать prepare_cad_plan."); return
            plan = plans[-1]
            payload = plan["payload"]
            item = next(i for i in state["inventory"] if i["id"] == payload["item_id"])
            text = f"{item['title']}\nЭтаж: {item['floor']}\nDWG: {state['source_path']}\n{payload['explanation']}\n\nРешение и основание:\n" + json.dumps(item["steps"], ensure_ascii=False, indent=2)
            text += "\n\nОперации (удаление и сохранение исходника запрещены):\n" + json.dumps(payload["actions"], ensure_ascii=False, indent=2)
            text += "\n\nПредварительно рассчитанные габарит, начало лучей и цели:\n" + json.dumps(payload["preflight"], ensure_ascii=False, indent=2)
            text += "\n\nПри частичной ошибке программа остановится, сохранив уже созданное. Автоматического удаления и повторения нет.\nВо время выполнения не переключайте активный DWG и не редактируйте этот чертёж параллельно. Коннектор работает с текущим документом."
            window, buttons = self.preview("Просмотр изменений AutoCAD", text)
            def apply():
                if self.busy:
                    return
                if messagebox.askyesno("Изменить открытый DWG", "Подтверждаете нормативное решение, адресацию объектов и перечисленные изменения на указанном этаже?\nФайл на диске не будет сохранён.", parent=window):
                    window.destroy()
                    self.summary.set("Выполнение подтверждённого плана и независимая сверка…")
                    def done(result):
                        self.refresh(); self.show(result)
                    self.background(lambda: self.gui.app.cad_guard.execute_from_ui(*scope, plan["id"], plan["hash"]), done)
            ttk.Button(buttons, text="Подтвердить и выполнить", command=apply).pack(side="left")
        except Exception as exc:
            self.show(str(exc))

    def barrier_inputs(self):
        self.gui.tabs.select(self.gui.barrier_panel)

    def barrier_plan(self, scope=None):
        try:
            scope=scope or self.context()
            data=barrier_control.snapshot(self.gui.app,*scope)
            window,buttons=self.preview("План схемы ПП — "+scope[0],barrier_control.plan_text(data))
            if not data["can_build"]:
                return
            plan_digest=data["state"]["plan_digest"]
            def apply():
                if self.busy or self.gui.barrier_panel.busy:
                    messagebox.showinfo("Оценка преград","Дождитесь завершения текущей операции ПП",parent=window);return
                if messagebox.askyesno("Построение схемы ПП","Построить показанные решения в отдельной копии DWG? Результат откроется в AutoCAD; исходник не перезаписывается.",parent=window):
                    window.destroy()
                    self.summary.set("Построение схемы ПП и независимая сверка…")
                    self.background(lambda:self.gui.app.barrier_workflow.build_copy_from_ui(*scope,expected_plan_digest=plan_digest),
                                    lambda result:self.barrier_done(scope,result))
            ttk.Button(buttons,text="Подтвердить и построить копию DWG",command=apply).pack(side="left")
        except Exception as exc:self.show(str(exc))

    def verify_barrier(self, scope=None):
        try:
            scope=scope or self.context()
            if self.gui.barrier_panel.busy:
                raise ValueError("Дождитесь завершения текущей операции ПП")
            data=barrier_control.snapshot(self.gui.app,*scope)
            if not data["can_verify"]:
                self.show(barrier_control.overview(data));return
            self.summary.set("Проверка фактической схемы ПП через AutoCAD…")
            self.background(lambda:self.gui.app.barrier_workflow.verify(*scope),lambda result:self.barrier_done(scope,result))
        except Exception as exc:self.show(str(exc))

    def barrier_done(self, scope, result):
        text=("Сверка графики пройдена." if result.get("ok") else "Сверка обнаружила ошибки.")
        text+="\n"+"\n".join(result.get("errors",[]))
        for key,label in (("result_dwg","Копия DWG"),("report","Отчёт")):
            if result.get(key):text+="\n"+label+": "+result[key]
        try:current=self.context()
        except ValueError:current=None
        if current==scope:
            self.gui.refresh_runs();self.refresh()
            self.gui.barrier_panel.work_status()
        else:
            self.preview("Результат ПП — "+scope[0]+" / "+scope[1],text)

    def rules(self):
        try:
            path = self.gui.app.control.folder(*self.context()) / "ПРАВИЛА_ЗАПУСКА.md"
            self.preview("Закреплённая версия правил", path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.show(str(exc))

    def quality(self):
        try:
            try:scope=self.context()
            except ValueError:scope=None
            if scope and self.gui.app.runs.load(*scope)["run"]["mode"]=="barrier_assessment":
                result=self.gui.app.quality.run_barriers()
                text="\n".join(("✓ " if r["ok"] else "Ошибка: ")+r["name"] for r in result["software_checks"])
                text+="\n\n"+result["notice"]+"\n\n"+barrier_control.overview(barrier_control.snapshot(self.gui.app,*scope))
                self.preview("Проверки качества ПП",text)
                return
        except Exception as exc:
            self.show(str(exc));return
        result = self.gui.app.quality.run()
        window, buttons = self.preview("Качество: программные и экспертные проверки", json.dumps(result, ensure_ascii=False, indent=2))
        def add_case():
            path = filedialog.askopenfilename(title="Утверждённый эталонный пример", filetypes=[("JSON", "*.json")], parent=window)
            if not path:
                return
            try:
                case = read_json(Path(path))
                self.preview("Содержимое примера", json.dumps(case, ensure_ascii=False, indent=2))
                if messagebox.askyesno("Эталон", "Подтвердить этот файл как проверенный вами эталон?", parent=window):
                    self.gui.app.quality.approve_case_from_ui(case)
                    window.destroy(); self.quality()
            except Exception as exc:
                messagebox.showerror("Эталон", str(exc), parent=window)
        ttk.Button(buttons, text="Добавить проверенный мной пример", command=add_case).pack(side="left")
        ttk.Button(buttons, text="Открыть шаблон примера", command=lambda: os.startfile(self.gui.app.paths.templates / "expert_case_template.json")).pack(side="left", padx=6)
