import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

import customtkinter as ctk
from matplotlib import text

from app.services.cleanup_old_visits import cleanup_old_visits
from app.services.directory_sync import full_sync
from app.services.sync_service import SyncService
from app.services.reader import RFIDReader
from app.utils.config import RFID_PORT
from app.utils.logger import get_logger
from app.services.beeper import Beeper
from app.services.db_helpers import *
from app.database.sqlite_db import init_sqlite

logger = get_logger("MonolithCustomTK")


# ------------------ resource/icon helpers ------------------

def resource_path(relative_path: str) -> str:
    if getattr(sys, "frozen", False):
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def set_app_icon(win, ico_path: str = "favicon.ico"):
    path = resource_path(ico_path)
    try:
        if os.path.exists(path):
            win.iconbitmap(path)
        else:
            print(f"[ICON] File not found: {path}")
    except Exception as e:
        print(f"[ICON] Failed to set icon: {e}")


def bind_enter_to_button(btn, command):
    btn.bind("<Return>", lambda e: command())
    btn.bind("<KP_Enter>", lambda e: command())


# ------------------ ttk styling inside CustomTkinter ------------------

def setup_ttk_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    bg = "#1f1f1f"
    surface = "#2b2b2b"
    fg = "#f5f5f5"
    muted = "#b8b8b8"
    accent = "#3B8ED0"

    style.configure(
        "Dark.Treeview",
        background=surface,
        foreground=fg,
        fieldbackground=surface,
        bordercolor=surface,
        borderwidth=0,
        rowheight=28
    )
    style.map(
        "Dark.Treeview",
        background=[("selected", accent)],
        foreground=[("selected", "#ffffff")]
    )
    style.configure(
        "Dark.Treeview.Heading",
        background=bg,
        foreground=fg,
        relief="flat"
    )
    style.map(
        "Dark.Treeview.Heading",
        background=[("active", "#343638")]
    )
    style.configure(
        "Vertical.TScrollbar",
        background=surface,
        troughcolor=bg,
        arrowcolor=fg
    )


# ------------------ Tk logger with line limit ------------------

class TkLogger:
    def __init__(self, root, text: ctk.CTkTextbox, max_lines: int = 500):
        self.root = root
        self.text = text
        self.max_lines = max_lines

    def write(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.root.after(0, self._append, line)

    def _append(self, line: str):
        self.text.configure(state="normal")
        self.text.insert("end", line)

        raw = self.text.get("1.0", "end-1c")
        lines = raw.splitlines()
        if len(lines) > self.max_lines:
            trimmed = "\n".join(lines[-self.max_lines:])
            self.text.delete("1.0", "end")
            self.text.insert("1.0", trimmed + ("\n" if trimmed else ""))

        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


# ------------------ Registration logic ------------------

class RegistrationClass:
    def __init__(self, tklog: TkLogger, beeper, update_status, update_last_registered):
        self.tklog = tklog
        self.beeper = beeper
        self.update_status = update_status
        self.update_last_registered = update_last_registered

    def process_rfid(self, rfid: str):
        try:
            emp = db_find_employee_by_rfid(rfid)
            if not emp:
                self.tklog.write("❌ Невідомий брелок")
                self.beeper.beep_unknown()
                return

            full_name = (emp.get("full_name") or "").strip()
            emp_id = int(emp["id"])

            status, err = db_register_visit(emp_id, source="rfid")
            if status == "ok":
                self.tklog.write(f"✅ Відмічено — {full_name}")
                self.beeper.beep_ok()
                self.update_status()
                self.update_last_registered(flash=True)
            elif status == "duplicate":
                self.tklog.write(f"⚠️ Вже був сьогодні — {full_name} - не реєструємо")
                self.beeper.beep_repeated()
                self.update_status()
            else:
                self.tklog.write(f"🔥 Помилка реєстрації — {full_name}: {err}")
                self.beeper.beep_error()

        except Exception as e:
            logger.exception("process_rfid error")
            self.tklog.write(f"🔥 Помилка: {e}")
            self.beeper.beep_error()


# ------------------ Manual registration window ------------------

class ManualRegisterWindow:
    def __init__(self, root, tklog: TkLogger, beeper, update_status, update_last_registered):
        self.root = root
        self.tklog = tklog
        self.beeper = beeper
        self.update_status = update_status
        self.update_last_registered = update_last_registered

        self.win = ctk.CTkToplevel(root)
        self.win.withdraw()
        set_app_icon(self.win, "favicon.ico")
        self.win.title("Ручна реєстрація")
        self.win.geometry("560x560+40+40")
        self.win.transient(root)
        self.win.grab_set()
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

        self.debounce_id = None
        self.selected_id = None
        self.results = []

        main = ctk.CTkFrame(self.win, corner_radius=12)
        main.pack(fill="both", expand=True, padx=12, pady=12)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            header,
            text="Пошук співробітника",
            font=ctk.CTkFont(size=20, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Введіть мінімум 2 символи",
            text_color="#9aa0a6"
        ).pack(anchor="w", pady=(4, 0))

        entry_wrap = ctk.CTkFrame(main, fg_color="transparent")
        entry_wrap.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        entry_wrap.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(entry_wrap, height=38, placeholder_text="ПІБ співробітника")
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.focus_set()

        body = ctk.CTkFrame(main)
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(
            body,
            height=16,
            activestyle="dotbox",
            bg="#2b2b2b",
            fg="#f5f5f5",
            selectbackground="#3B8ED0",
            selectforeground="#ffffff",
            highlightthickness=0,
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 11),
        )
        self.listbox.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)

        sb = ttk.Scrollbar(body, command=self.listbox.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, 10), pady=10)
        self.listbox.configure(yscrollcommand=sb.set)

        footer = ctk.CTkFrame(main, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)

        self.msg = ctk.CTkLabel(footer, text="", text_color="#9aa0a6", anchor="w")
        self.msg.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        actions = ctk.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="e")

        self.btn_register = ctk.CTkButton(
            actions,
            text="Реєстрація",
            width=130,
            command=self.register_selected,
            state="disabled"
        )
        self.btn_register.grid(row=0, column=0, padx=(0, 8))

        self.btn_close = ctk.CTkButton(
            actions,
            text="Закрити",
            width=110,
            fg_color="#444",
            hover_color="#555",
            command=self.win.destroy
        )
        self.btn_close.grid(row=0, column=1)

        self.win.bind("<Escape>", lambda e: self.win.destroy())

        self.entry.bind("<KeyRelease>", self.on_input)
        self.entry.bind("<Return>", self.on_entry_return)
        self.entry.bind("<KP_Enter>", self.on_entry_return)
        self.entry.bind("<Down>", self.on_entry_down)

        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.listbox.bind("<Double-Button-1>", lambda e: self.register_selected())
        self.listbox.bind("<Return>", self.on_listbox_return)
        self.listbox.bind("<KP_Enter>", self.on_listbox_return)

        self.btn_register.bind("<Return>", lambda e: self.register_selected())
        self.btn_register.bind("<KP_Enter>", lambda e: self.register_selected())
        self.btn_close.bind("<Return>", lambda e: self.win.destroy())
        self.btn_close.bind("<KP_Enter>", lambda e: self.win.destroy())

    def on_input(self, _e=None):
        if self.debounce_id:
            self.win.after_cancel(self.debounce_id)
        self.debounce_id = self.win.after(250, self.run_search)

    def run_search(self):
        q = (self.entry.get() or "").strip()
        self.listbox.delete(0, "end")
        self.results = []
        self.selected_id = None
        self.btn_register.configure(state="disabled")

        if len(q) < 2:
            self.msg.configure(text="Введіть мінімум 2 символи")
            return

        try:
            rows = db_search_employees(q, limit=30)
            self.results = rows

            if not rows:
                self.msg.configure(text="Нічого не знайдено")
                return

            for r in rows:
                self.listbox.insert("end", r.get("full_name") or "")

            self.msg.configure(text=f"Знайдено: {len(rows)}")

            if len(rows) == 1:
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(0)
                self.listbox.activate(0)
                self.listbox.see(0)
                self.selected_id = int(rows[0]["id"])
                self.btn_register.configure(state="normal")

        except Exception as e:
            self.msg.configure(text=f"Помилка пошуку: {e}")

    def on_entry_down(self, _e=None):
        if not self.results:
            return "break"

        self.listbox.focus_set()

        cur = self.listbox.curselection()
        idx = int(cur[0]) if cur else 0

        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self.listbox.activate(idx)
        self.listbox.see(idx)
        self.on_select()

        return "break"

    def on_entry_return(self, _e=None):
        if len(self.results) == 1:
            self.selected_id = int(self.results[0]["id"])
            self.register_selected()
            return "break"

        if len(self.results) > 1:
            self.listbox.focus_set()
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(0)
            self.listbox.activate(0)
            self.listbox.see(0)
            self.on_select()
            return "break"

        return "break"

    def on_select(self, _e=None):
        sel = self.listbox.curselection()
        if not sel:
            self.selected_id = None
            self.btn_register.configure(state="disabled")
            return

        idx = int(sel[0])
        if idx < 0 or idx >= len(self.results):
            self.selected_id = None
            self.btn_register.configure(state="disabled")
            return

        self.selected_id = int(self.results[idx]["id"])
        self.btn_register.configure(state="normal")

    def on_listbox_return(self, _e=None):
        sel = self.listbox.curselection()
        if not sel and len(self.results) == 1:
            self.selected_id = int(self.results[0]["id"])
        else:
            self.on_select()

        if self.selected_id:
            self.register_selected()

        return "break"

    def register_selected(self):
        if not self.selected_id:
            return

        name = ""
        try:
            for r in self.results:
                if int(r["id"]) == int(self.selected_id):
                    name = (r.get("full_name") or "").strip()
                    break
        except Exception:
            pass

        status, err = db_register_visit(self.selected_id, source="manual")

        if status == "ok":
            self.tklog.write(f"✅ Ручна реєстрація — {name}")
            self.beeper.beep_ok()
            self.update_status()
            self.update_last_registered(flash=True)
            self.win.destroy()
        elif status == "duplicate":
            self.msg.configure(text="⚠️ Вже був сьогодні. Не реєструємо.")
            self.beeper.beep_repeated()
            self.update_status()
        else:
            self.msg.configure(text=f"🔥 Помилка: {err}")
            self.beeper.beep_error()


# ------------------ Report window ------------------

class ReportWindow:
    def __init__(self, root, tklog: TkLogger):
        self.root = root
        self.tklog = tklog
        self.rows = []

        today = datetime.now().strftime("%Y-%m-%d")

        self.win = ctk.CTkToplevel(root)
        self.win.withdraw()
        set_app_icon(self.win, "favicon.ico")
        self.win.title("Звіт")
        self.win.geometry("820x600+40+40")
        self.win.transient(root)
        self.win.grab_set()
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

        main = ctk.CTkFrame(self.win, corner_radius=12)
        main.pack(fill="both", expand=True, padx=12, pady=12)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 10))

        ctk.CTkLabel(
            header,
            text="Звіт за дату",
            font=ctk.CTkFont(size=20, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Формат дати: YYYY-MM-DD",
            text_color="#9aa0a6"
        ).pack(anchor="w", pady=(4, 0))

        filters = ctk.CTkFrame(main, fg_color="transparent")
        filters.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(filters, text="Дата:").pack(side="left")

        self.date_entry = ctk.CTkEntry(filters, width=160)
        self.date_entry.pack(side="left", padx=(8, 12))
        self.date_entry.insert(0, today)
        self.date_entry.focus_set()

        self.btn_show = ctk.CTkButton(filters, text="Показати", width=110, command=self.load_report)
        self.btn_show.pack(side="left")

        self.msg = ctk.CTkLabel(main, text="", text_color="#9aa0a6")
        self.msg.pack(fill="x", padx=16, pady=(0, 8))

        table_wrap = ctk.CTkFrame(main)
        table_wrap.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        self.tree = ttk.Treeview(table_wrap, columns=("dt", "name"), show="headings", style="Dark.Treeview")
        self.tree.heading("dt", text="Дата-час")
        self.tree.heading("name", text="ПІБ")
        self.tree.column("dt", width=180, anchor="w")
        self.tree.column("name", width=560, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)

        ysb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        ysb.pack(side="right", fill="y", padx=(0, 10), pady=10)
        self.tree.configure(yscrollcommand=ysb.set)

        actions = ctk.CTkFrame(main, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=(0, 16))

        self.btn_close = ctk.CTkButton(
            actions,
            text="Закрити",
            width=110,
            fg_color="#444",
            hover_color="#555",
            command=self.win.destroy
        )
        self.btn_close.pack(side="right")

        self.btn_save = ctk.CTkButton(actions, text="Зберегти", width=110, command=self.save_report)
        self.btn_save.pack(side="right", padx=(0, 8))

        self.win.bind("<Escape>", lambda e: self.win.destroy())

        self.date_entry.bind("<Return>", self.on_date_return)
        self.date_entry.bind("<KP_Enter>", self.on_date_return)
        self.date_entry.bind("<Down>", self.on_date_down)

        self.tree.bind("<Return>", self.on_tree_return)
        self.tree.bind("<KP_Enter>", self.on_tree_return)

        self.btn_show.bind("<Return>", lambda e: self.load_report())
        self.btn_show.bind("<KP_Enter>", lambda e: self.load_report())

        self.btn_save.bind("<Return>", lambda e: self.save_report())
        self.btn_save.bind("<KP_Enter>", lambda e: self.save_report())

        self.btn_close.bind("<Return>", lambda e: self.win.destroy())
        self.btn_close.bind("<KP_Enter>", lambda e: self.win.destroy())

        self.load_report()

    def validate_date(self, value: str) -> bool:
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def clear_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def ensure_tree_selection(self):
        items = self.tree.get_children()
        if not items:
            return False

        selected = self.tree.selection()
        if selected:
            return True

        first = items[0]
        self.tree.selection_set(first)
        self.tree.focus(first)
        self.tree.see(first)
        return True

    def on_date_return(self, _e=None):
        self.load_report()
        return "break"

    def on_date_down(self, _e=None):
        if self.ensure_tree_selection():
            self.tree.focus_set()
        return "break"

    def on_tree_return(self, _e=None):
        if self.ensure_tree_selection():
            self.save_report()
        return "break"

    def load_report(self):
        report_date = (self.date_entry.get() or "").strip()

        if not self.validate_date(report_date):
            self.msg.configure(text="Некоректний формат дати. Використовуй YYYY-MM-DD")
            return

        try:
            self.rows = db_get_visits_for_date(report_date)
            self.clear_table()

            for row in self.rows:
                self.tree.insert("", "end", values=(row["visit_time"], row["full_name"]))

            if self.tree.get_children():
                first = self.tree.get_children()[0]
                self.tree.selection_set(first)
                self.tree.focus(first)
                self.tree.see(first)

            self.msg.configure(text=f"Записів: {len(self.rows)}")
            self.tklog.write(f"📄 Сформовано звіт за дату {report_date} ({len(self.rows)} записів)")

        except Exception as e:
            logger.exception("load_report error")
            self.msg.configure(text=f"Помилка формування звіту: {e}")

    def save_report(self):
        if not self.rows:
            messagebox.showwarning("Звіт", "Немає даних для збереження")
            return

        report_date = (self.date_entry.get() or "").strip()
        default_name = f"report_{report_date}.txt"
        title_line = f"Звіт за дату: {report_date}"

        path = filedialog.asksaveasfilename(
            parent=self.win,
            title="Зберегти звіт",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )

        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(title_line + "\n")
                f.write("=" * len(title_line) + "\n\n")
                for row in self.rows:
                    f.write(f"{row['visit_time']} | {row['full_name']}\n")

            self.tklog.write(f"💾 Звіт збережено: {path}")
            messagebox.showinfo("Звіт", "Файл успішно збережено")
        except Exception as e:
            logger.exception("save_report error")
            messagebox.showerror("Звіт", f"Не вдалося зберегти файл:\n{e}")


# ------------------ UI ------------------

def build_ui(root, on_manual, on_report):
    root.title("KONSORT - їдальня")
    root.geometry("1300x700+0+0")
    # root.attributes("-fullscreen", True)
    root.minsize(860, 560)

    container = ctk.CTkFrame(root, corner_radius=0)
    container.pack(fill="both", expand=True)

    top = ctk.CTkFrame(container, corner_radius=12)
    top.pack(fill="x", padx=12, pady=(12, 8))

    now = datetime.now().strftime("%d.%m.%Y")

    title = ctk.CTkLabel(
        top,
        text=f"{now}",
        font=ctk.CTkFont(size=24, weight="bold")
    )
    title.pack(side="left", padx=16, pady=14)

    manual_btn = ctk.CTkButton(top, text="Ручна реєстрація", width=170, command=on_manual)
    manual_btn.pack(side="right", padx=16, pady=14)
    bind_enter_to_button(manual_btn, on_manual)

    last_frame = ctk.CTkFrame(container, corner_radius=12)
    last_frame.pack(fill="x", padx=12, pady=(0, 8))

    last_frame.grid_columnconfigure(0, weight=1)
    last_frame.grid_columnconfigure(1, weight=0)

    last_registered_label = ctk.CTkLabel(
        last_frame,
        text="—",
        font=ctk.CTkFont(size=30, weight="bold"),
        anchor="center",
        justify="center",
    )
    last_registered_label.grid(row=0, column=0, sticky="ew", padx=(16, 8), pady=16)

    today_count_label = ctk.CTkLabel(
        last_frame,
        text="0",
        font=ctk.CTkFont(size=28, weight="bold"),
        text_color="#3B8ED0",
    )
    today_count_label.grid(row=0, column=1, sticky="e", padx=(8, 16), pady=16)

    body = ctk.CTkFrame(container, corner_radius=12)
    body.pack(fill="both", expand=True, padx=12, pady=(0, 8))

    text = ctk.CTkTextbox(body, wrap="word", font=("Consolas", 22), corner_radius=10)
    text.pack(fill="both", expand=True, padx=14, pady=14)
    text.configure(state="disabled")

    bottom = ctk.CTkFrame(container, corner_radius=12)
    bottom.pack(fill="x", padx=12, pady=(0, 12))

    status = ctk.CTkLabel(
        bottom,
        text=f"Статус: очікую RFID… | Port: {RFID_PORT} | Чекає синхронізації: 0",
        anchor="w",
        justify="left",
    )
    status.pack(side="left", fill="x", expand=True, padx=16, pady=14)

    report_btn = ctk.CTkButton(bottom, text="Звіт", width=100, command=on_report)
    report_btn.pack(side="right", padx=16, pady=14)
    bind_enter_to_button(report_btn, on_report)

    return text, status, last_registered_label, today_count_label


# ------------------ main ------------------

def main():
    init_sqlite()
    full_sync()
    cleanup_old_visits()

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    set_app_icon(root, "favicon.ico")
    setup_ttk_theme(root)

    beeper = Beeper()
    sync = SyncService()
    sync.start()

    tklog = None
    status_label = None
    last_registered_label = None
    today_count_label = None

    def update_status():
        try:
            unsynced_count = db_get_unsynced_count()
            today = datetime.now().strftime("%Y-%m-%d")
            today_rows = db_get_visits_for_date(today)
            today_count = len(today_rows)

            status_label.configure(
                text=(
                    f"Статус: очікую RFID… | Port: {RFID_PORT} | "
                    f"Чекає синхронізації: {unsynced_count}"
                )
            )

            today_count_label.configure(text=str(today_count))

        except Exception:
            logger.exception("update_status error")
            try:
                status_label.configure(
                    text=f"Статус: очікую RFID… | Port: {RFID_PORT} | Чекає синхронізації: ?"
                )
                today_count_label.configure(text="?")
            except Exception:
                pass

    last_flash_job = None

    def update_last_registered(flash: bool = False):
        nonlocal last_flash_job

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            rows = db_get_visits_for_date(today)

            if not rows:
                last_registered_label.configure(
                    text="Гарного робочого дня!",
                    text_color=("#0f8f3d", "#4fe37a")
                )
                return

            row = rows[-1]
            full_name = (row.get("full_name") or "").strip()
            normal_color = ("gray10", "gray90")
            flash_color = ("#0f8f3d", "#4fe37a")

            last_registered_label.configure(
                text=full_name.upper() if full_name else "—"
            )

            if last_flash_job is not None:
                try:
                    root.after_cancel(last_flash_job)
                except Exception:
                    pass
                last_flash_job = None

            if flash and full_name:
                last_registered_label.configure(text_color=flash_color)

                def reset_last_label_color():
                    nonlocal last_flash_job
                    try:
                        last_registered_label.configure(text_color=normal_color)
                    finally:
                        last_flash_job = None

                last_flash_job = root.after(1500, reset_last_label_color)
            else:
                last_registered_label.configure(text_color=normal_color)

        except Exception:
            logger.exception("update_last_registered error")
            try:
                last_registered_label.configure(text="—", text_color=("gray10", "gray90"))
            except Exception:
                pass

    def open_manual():
        ManualRegisterWindow(
            root=root,
            tklog=tklog,
            beeper=beeper,
            update_status=update_status,
            update_last_registered=update_last_registered,
        )

    def open_report():
        ReportWindow(root=root, tklog=tklog)

    text_widget, status_label, last_registered_label, today_count_label = build_ui(
        root,
        on_manual=open_manual,
        on_report=open_report
    )
    tklog = TkLogger(root, text_widget, max_lines=500)

    tklog.write("🚀 Застосунок працює. Очікую зчитування RFID…")
    update_last_registered()
    update_status()

    registration = RegistrationClass(
        tklog=tklog,
        beeper=beeper,
        update_status=update_status,
        update_last_registered=update_last_registered,
    )

    reader = RFIDReader(port=RFID_PORT, callback=registration.process_rfid)
    reader.start()

    def refresh_status_loop():
        update_status()
        root.after(2000, refresh_status_loop)

    refresh_status_loop()

    def on_close():
        try:
            reader.stop()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()