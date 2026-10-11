#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cnc_prep_gui.py — WinForms-style desktop front end for cnc_prep.py (mycode.py).

A plain Tkinter form: fill in fields, click a button, watch the log. It never
imports mycode.py's pipeline directly -- it only assembles a command line and
runs mycode.py as a subprocess (the same way you'd run it from the terminal),
so the GUI and the CLI can never drift out of sync with each other.

Run:
    .venv/bin/python3 cnc_prep_gui.py
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "mycode.py"
SETTINGS_PATH = Path.home() / ".cnc_prep_gui_settings.json"


def find_python() -> str:
    """Prefer the project's own venv interpreter; fall back to whatever is running us."""
    for candidate in (HERE / ".venv" / "bin" / "python3", HERE / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def open_in_file_manager(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass


class LabeledEntry(ttk.Frame):
    """label + entry pair, aligned via a shared grid column from the parent."""

    def __init__(self, parent, label, width=12, default="", **kw):
        super().__init__(parent)
        ttk.Label(self, text=label, width=18, anchor="w").grid(row=0, column=0, sticky="w")
        self.var = tk.StringVar(value=default)
        self.entry = ttk.Entry(self, textvariable=self.var, width=width, **kw)
        self.entry.grid(row=0, column=1, sticky="w", padx=(4, 0))

    def get(self) -> str:
        return self.var.get().strip()


class CncPrepGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cut-Ready — cnc_prep")
        self.geometry("860x800")
        self.minsize(760, 680)
        self.configure(bg="#ECECEC")

        # macOS Tk (especially newer Tk 9.x aqua builds) has a known bug
        # where the window's Cocoa backing store never gets an initial paint
        # until it's activated -- it renders solid black instead. The native
        # aqua ttk theme also relies on that same backing store more than
        # 'clam' does, so switching themes plus forcing an activation cycle
        # right after the window exists is the standard workaround.
        try:
            style = ttk.Style(self)
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.proc: subprocess.Popen | None = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.suggest_queue: "queue.Queue[str]" = queue.Queue()
        self._last_report_path: Path | None = None
        self._last_job_kind: str = "run"

        self._build_form()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_queues()
        self.after(50, self._force_repaint)

    def _force_repaint(self):
        """Kick the window server into actually painting the window (macOS black-window fix)."""
        if sys.platform != "darwin":
            return
        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        self.focus_force()
        # Nudging the size by a pixel and back forces a full redraw on the
        # builds where activation alone isn't enough.
        w, h = self.winfo_width(), self.winfo_height()
        if w > 1 and h > 1:
            self.geometry(f"{w}x{h + 1}")
            self.after(30, lambda: self.geometry(f"{w}x{h}"))

    # ------------------------------------------------------------------ UI

    def _build_form(self):
        pad = {"padx": 8, "pady": 6}

        # ---- input/output --------------------------------------------
        io = ttk.LabelFrame(self, text="Girdi / Çıktı")
        io.pack(fill="x", **pad)

        row = ttk.Frame(io); row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Girdi (dosya/klasör)", width=18, anchor="w").pack(side="left")
        self.input_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.input_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Dosya...", command=self._browse_input_file).pack(side="left", padx=2)
        ttk.Button(row, text="Klasör...", command=self._browse_input_dir).pack(side="left")

        row = ttk.Frame(io); row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Çıktı klasörü", width=18, anchor="w").pack(side="left")
        self.output_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.output_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Klasör...", command=self._browse_output_dir).pack(side="left")

        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(io, text="Alt klasörlere de in (-r)",
                        variable=self.recursive_var).pack(anchor="w", padx=8, pady=(0, 6))

        # ---- size --------------------------------------------------------
        sz = ttk.LabelFrame(self, text="Boyut")
        sz.pack(fill="x", **pad)
        row = ttk.Frame(sz); row.pack(fill="x", padx=8, pady=4)
        self.width_mm = LabeledEntry(row, "Genişlik (mm)", default="550"); self.width_mm.pack(side="left")
        self.height_mm = LabeledEntry(row, "Yükseklik (mm)", default=""); self.height_mm.pack(side="left", padx=(16, 0))
        self.native_size_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="SVG'nin kendi boyutu (--native-size)",
                        variable=self.native_size_var).pack(side="left", padx=(16, 0))

        row = ttk.Frame(sz); row.pack(fill="x", padx=8, pady=(0, 6))
        self.suggest_btn = ttk.Button(row, text="Min Güvenli Ölçüyü Hesapla",
                                      command=self._suggest_size)
        self.suggest_btn.pack(side="left")
        self.suggest_label = ttk.Label(row, text="", foreground="#2E6E8E")
        self.suggest_label.pack(side="left", padx=10)

        # ---- structural rules ---------------------------------------------
        st = ttk.LabelFrame(self, text="Yapısal Kurallar")
        st.pack(fill="x", **pad)
        row = ttk.Frame(st); row.pack(fill="x", padx=8, pady=4)
        self.min_thickness = LabeledEntry(row, "Min kalınlık (mm)", default="1.8"); self.min_thickness.pack(side="left")
        self.sharp_tip_deg = LabeledEntry(row, "Sivri uç açısı (°)", default="12"); self.sharp_tip_deg.pack(side="left", padx=(16, 0))

        row = ttk.Frame(st); row.pack(fill="x", padx=8, pady=4)
        self.kerf_mm = LabeledEntry(row, "Kerf (mm)", default="1.8"); self.kerf_mm.pack(side="left")
        self.gap_factor = LabeledEntry(row, "Boşluk çarpanı", default="1.0"); self.gap_factor.pack(side="left", padx=(16, 0))

        row = ttk.Frame(st); row.pack(fill="x", padx=8, pady=(0, 6))
        self.fill_gaps_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Dar boşlukları otomatik kapat (--fill-narrow-gaps)",
                        variable=self.fill_gaps_var).pack(side="left")

        thicken_row = ttk.Frame(st); thicken_row.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(thicken_row, text="İnce yer onarımı", width=18, anchor="w").pack(side="left")
        self.thicken_mode_var = tk.StringVar(value="taper")
        ttk.Combobox(thicken_row, textvariable=self.thicken_mode_var, width=10, state="readonly",
                    values=["taper", "uniform"]).pack(side="left")
        ttk.Label(thicken_row, foreground="#5B6266",
                  text="taper: çizginin kendi kalınlık profilini ölçekler, sivri uç sivri kalır  |  "
                       "uniform: sabit disk basar, uçları topak yapar").pack(side="left", padx=8)

        tip_row = ttk.Frame(st); tip_row.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(tip_row, text="Sivri uç politikası", width=18, anchor="w").pack(side="left")
        self.tip_policy_var = tk.StringVar(value="trim")
        ttk.Combobox(tip_row, textvariable=self.tip_policy_var, width=10, state="readonly",
                    values=["round", "trim"]).pack(side="left")
        ttk.Label(tip_row, foreground="#5B6266",
                  text="kesilemeyecek kadar incelen serbest uç: trim erken bitirir (düz kenarlar kalır), "
                       "round kalınlaştırıp yuvarlar").pack(side="left", padx=8)

        floating_row = ttk.Frame(st); floating_row.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(floating_row, text="Uçan parçalar", width=18, anchor="w").pack(side="left")
        self.floating_var = tk.StringVar(value="bridge")
        ttk.Combobox(floating_row, textvariable=self.floating_var, width=10, state="readonly",
                    values=["warn", "bridge", "remove", "keep"]).pack(side="left")

        # ---- vectorisation -------------------------------------------------
        vec = ttk.LabelFrame(self, text="Vektör")
        vec.pack(fill="x", **pad)
        row = ttk.Frame(vec); row.pack(fill="x", padx=8, pady=4)
        self.work_res = LabeledEntry(row, "Çözünürlük (px/mm)", default="8"); self.work_res.pack(side="left")
        self.simplify_mm = LabeledEntry(row, "Sadeleştirme (mm)", default="0.15"); self.simplify_mm.pack(side="left", padx=(16, 0))
        self.bezier_tension = LabeledEntry(row, "Bezier gerginliği", default="0.85"); self.bezier_tension.pack(side="left", padx=(16, 0))

        row = ttk.Frame(vec); row.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(row, foreground="#5B6266", justify="left",
                  text="\"kontur maskeden sapıyor (IoU)\" uyarısı alırsan çözünürlüğü yükselt (12-16). "
                       "Bu uyarı ince şeritli tasarımlarda kontur üzerindeki 1 piksellik yuvarlamadan "
                       "çıkar; sadeleştirme veya gerginlik düşürmek işe yaramaz, sadece düğüm sayısını "
                       "şişirir. Çözünürlük arttıkça raster büyür ve iş yavaşlar.").pack(side="left")

        # ---- output format -------------------------------------------------
        out = ttk.LabelFrame(self, text="Çıktı Formatı")
        out.pack(fill="x", **pad)

        row = ttk.Frame(out); row.pack(fill="x", padx=8, pady=4)
        self.no_svg_var = tk.BooleanVar(value=True)
        self.no_dxf_var = tk.BooleanVar(value=False)
        self.no_png_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="SVG yazma", variable=self.no_svg_var).pack(side="left")
        ttk.Checkbutton(row, text="DXF yazma", variable=self.no_dxf_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(row, text="PNG yazma", variable=self.no_png_var).pack(side="left", padx=(16, 0))
        self.preview_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Kontrol önizlemesi (--preview)",
                        variable=self.preview_var).pack(side="left", padx=(16, 0))

        row = ttk.Frame(out); row.pack(fill="x", padx=8, pady=(0, 6))
        self.canvas_px = LabeledEntry(row, "Kanvas (px)", default="4500x5100"); self.canvas_px.pack(side="left")
        self.png_dpi = LabeledEntry(row, "PNG DPI", default="300"); self.png_dpi.pack(side="left", padx=(16, 0))

        # ---- presets ---------------------------------------------------
        presets = ttk.Frame(self); presets.pack(fill="x", **pad)
        ttk.Button(presets, text="EazyDemand Ayarlarını Yükle",
                  command=self._apply_eazydemand_preset).pack(side="left")
        ttk.Button(presets, text="Varsayılanlara Dön",
                  command=self._reset_defaults).pack(side="left", padx=8)

        # ---- run controls -------------------------------------------------
        runbar = ttk.Frame(self); runbar.pack(fill="x", **pad)
        self.dry_run_btn = ttk.Button(runbar, text="Kuru Kontrol", command=lambda: self._run(dry_run=True))
        self.dry_run_btn.pack(side="left")
        self.run_btn = ttk.Button(runbar, text="Çalıştır", command=lambda: self._run(dry_run=False))
        self.run_btn.pack(side="left", padx=8)
        self.cancel_btn = ttk.Button(runbar, text="Durdur", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        ttk.Button(runbar, text="Çıktı Klasörünü Aç",
                  command=self._open_output).pack(side="left", padx=8)
        self.status_var = tk.StringVar(value="hazır")
        ttk.Label(runbar, textvariable=self.status_var, foreground="#5B6266").pack(side="right")

        # ---- result summary -----------------------------------------------
        # A batch (folder input) run prints one line per file in the log, but
        # that's prose, not something you can scan. This mirrors it as a
        # proper table straight from the JSON report, so "which files need a
        # look" is answerable at a glance instead of by re-reading the log.
        sumframe = ttk.LabelFrame(self, text="Sonuç Özeti")
        sumframe.pack(fill="x", **pad)
        self.summary_tree = ttk.Treeview(
            sumframe, columns=("status", "detail"), show="tree headings", height=4)
        self.summary_tree.heading("#0", text="Dosya")
        self.summary_tree.heading("status", text="Durum")
        self.summary_tree.heading("detail", text="Detay")
        self.summary_tree.column("#0", width=220, anchor="w")
        self.summary_tree.column("status", width=90, anchor="w")
        self.summary_tree.column("detail", width=420, anchor="w")
        self.summary_tree.tag_configure("ok", foreground="#2F7D4F")
        self.summary_tree.tag_configure("warn", foreground="#B8860B")
        self.summary_tree.tag_configure("fail", foreground="#B0311E")
        self.summary_tree.pack(side="left", fill="x", expand=True, padx=(6, 0), pady=6)
        sum_scroll = ttk.Scrollbar(sumframe, command=self.summary_tree.yview)
        sum_scroll.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.summary_tree.configure(yscrollcommand=sum_scroll.set)

        # ---- log --------------------------------------------------------
        logframe = ttk.LabelFrame(self, text="Kayıt")
        logframe.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(logframe, height=14, wrap="word", state="disabled",
                                font=("Menlo" if sys.platform == "darwin" else "Consolas", 11))
        self.log_text.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        scrollbar = ttk.Scrollbar(logframe, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.log_text.configure(yscrollcommand=scrollbar.set)

    # ------------------------------------------------------------ browse

    def _browse_input_file(self):
        p = filedialog.askopenfilename(
            title="Girdi dosyası seç",
            filetypes=[("Desteklenen", "*.png *.bmp *.tif *.tiff *.jpg *.jpeg *.webp *.svg"),
                      ("Tüm dosyalar", "*.*")])
        if p:
            self.input_var.set(p)

    def _browse_input_dir(self):
        p = filedialog.askdirectory(title="Girdi klasörü seç")
        if p:
            self.input_var.set(p)

    def _browse_output_dir(self):
        p = filedialog.askdirectory(title="Çıktı klasörü seç")
        if p:
            self.output_var.set(p)

    def _open_output(self):
        out = self.output_var.get().strip()
        if not out:
            messagebox.showinfo("Cut-Ready", "Önce bir çıktı klasörü seç.")
            return
        open_in_file_manager(Path(out))

    # ------------------------------------------------------------ presets

    def _apply_eazydemand_preset(self):
        self.min_thickness.var.set("1.8")
        self.sharp_tip_deg.var.set("12")
        self.kerf_mm.var.set("1.8")
        self.gap_factor.var.set("1.0")
        self.fill_gaps_var.set(True)
        self.canvas_px.var.set("4500x5100")
        self.png_dpi.var.set("300")
        self.no_svg_var.set(True)
        self.no_dxf_var.set(False)
        self.no_png_var.set(False)
        self._log("[preset] EazyDemand ayarları yüklendi (min 1.8mm, kerf 1.8mm, 4500x5100@300dpi, PNG+DXF)\n")

    def _reset_defaults(self):
        self.width_mm.var.set("550")
        self.height_mm.var.set("")
        self.native_size_var.set(False)
        self.min_thickness.var.set("2.0")
        self.sharp_tip_deg.var.set("60")
        self.kerf_mm.var.set("1.2")
        self.gap_factor.var.set("1.5")
        self.fill_gaps_var.set(False)
        self.floating_var.set("warn")
        self.no_svg_var.set(True)
        self.no_dxf_var.set(False)
        self.no_png_var.set(False)
        self.canvas_px.var.set("")
        self.png_dpi.var.set("300")
        self.preview_var.set(True)

    # ------------------------------------------------------------ settings persistence

    def _settings_vars(self) -> dict:
        """Every field worth remembering between sessions, name -> its Tk variable."""
        return {
            "input": self.input_var, "output": self.output_var,
            "recursive": self.recursive_var,
            "width_mm": self.width_mm.var, "height_mm": self.height_mm.var,
            "native_size": self.native_size_var,
            "min_thickness": self.min_thickness.var, "sharp_tip_deg": self.sharp_tip_deg.var,
            "kerf_mm": self.kerf_mm.var, "gap_factor": self.gap_factor.var,
            "fill_gaps": self.fill_gaps_var, "floating": self.floating_var,
            "thicken_mode": self.thicken_mode_var, "tip_policy": self.tip_policy_var,
            "no_svg": self.no_svg_var, "no_dxf": self.no_dxf_var, "no_png": self.no_png_var,
            "canvas_px": self.canvas_px.var, "png_dpi": self.png_dpi.var,
            "preview": self.preview_var,
        }

    def _save_settings(self):
        try:
            data = {name: var.get() for name, var in self._settings_vars().items()}
            SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass  # losing the remembered settings is not worth interrupting a close/run

    def _load_settings(self):
        if not SETTINGS_PATH.exists():
            return
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        for name, var in self._settings_vars().items():
            if name in data:
                try:
                    var.set(data[name])
                except tk.TclError:
                    pass  # a boolean field that used to be a string, etc. -- skip, don't crash

    def _on_close(self):
        self._save_settings()
        if self.proc is not None:
            self.proc.terminate()
        self.destroy()

    # ------------------------------------------------------------ args

    def _common_args(self) -> list[str] | None:
        inp = self.input_var.get()
        out = self.output_var.get()
        if not inp:
            messagebox.showerror("Cut-Ready", "Girdi dosyası ya da klasörü seçmelisin.")
            return None
        if not out:
            messagebox.showerror("Cut-Ready", "Çıktı klasörü seçmelisin.")
            return None

        args = ["-i", inp, "-o", out]
        if self.recursive_var.get():
            args.append("-r")

        if self.native_size_var.get():
            args.append("--native-size")
        else:
            if self.width_mm.get():
                args += ["--width-mm", self.width_mm.get()]
            if self.height_mm.get():
                args += ["--height-mm", self.height_mm.get()]

        if self.min_thickness.get():
            args += ["--min-thickness-mm", self.min_thickness.get()]
        if self.sharp_tip_deg.get():
            args += ["--sharp-tip-deg", self.sharp_tip_deg.get()]
        if self.kerf_mm.get():
            args += ["--kerf-mm", self.kerf_mm.get()]
        if self.gap_factor.get():
            args += ["--gap-factor", self.gap_factor.get()]
        if self.fill_gaps_var.get():
            args.append("--fill-narrow-gaps")
        args += ["--floating", self.floating_var.get()]
        args += ["--thicken-mode", self.thicken_mode_var.get()]
        args += ["--tip-policy", self.tip_policy_var.get()]

        if self.work_res.get():
            args += ["--work-res", self.work_res.get()]
        if self.simplify_mm.get():
            args += ["--simplify-mm", self.simplify_mm.get()]
        if self.bezier_tension.get():
            args += ["--bezier-tension", self.bezier_tension.get()]

        if self.no_svg_var.get():
            args.append("--no-svg")
        if self.no_dxf_var.get():
            args.append("--no-dxf")
        if self.no_png_var.get():
            args.append("--no-png")
        if self.canvas_px.get():
            args += ["--canvas-px", self.canvas_px.get()]
        if self.png_dpi.get():
            args += ["--png-dpi", self.png_dpi.get()]
        if self.preview_var.get():
            args.append("--preview")

        # Always name the report explicitly (not just when not --dry-run) so
        # a Kuru Kontrol pass can still populate the Sonuç Özeti table below --
        # mycode.py only writes the JSON report on its own for a real run.
        report_path = Path(out) / "cnc_prep_report.json"
        args += ["--report", str(report_path)]
        self._last_report_path = report_path
        return args

    # ------------------------------------------------------------ run

    def _run(self, dry_run: bool):
        if self.proc is not None:
            messagebox.showinfo("Cut-Ready", "Zaten bir işlem çalışıyor.")
            return
        args = self._common_args()
        if args is None:
            return
        if dry_run:
            args.append("--dry-run")
        self._last_job_kind = "run"

        cmd = [find_python(), str(SCRIPT)] + args
        self._log(f"\n$ {' '.join(cmd)}\n")
        self._launch(cmd, is_suggest=False)

    def _launch(self, cmd: list[str], is_suggest: bool):
        """Start any mycode.py subprocess (a real run or a size search) under
        the same self.proc handle, so one Durdur button and one running-state
        toggle cover both -- a long --suggest-size search used to be
        uncancellable because it ran outside this machinery entirely."""
        self._set_running(True)
        if is_suggest:
            self.suggest_label.configure(text="hesaplanıyor...")
        threading.Thread(target=self._run_subprocess, args=(cmd, is_suggest), daemon=True).start()

    def _run_subprocess(self, cmd: list[str], is_suggest: bool = False):
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=str(HERE),
            )
            assert self.proc.stdout is not None
            found_suggestion = False
            for line in self.proc.stdout:
                self.log_queue.put(line)
                if is_suggest and "size:" in line:
                    self.suggest_queue.put(line.split("size:", 1)[-1].strip())
                    found_suggestion = True
            code = self.proc.wait()
            self.log_queue.put(f"\n[bitti] çıkış kodu {code}\n")
            if is_suggest and not found_suggestion and code == 0:
                self.suggest_queue.put("bulunamadı (log'a bak)")
        except Exception as exc:
            self.log_queue.put(f"\n[hata] {type(exc).__name__}: {exc}\n")
            if is_suggest:
                self.suggest_queue.put(f"hata: {exc}")
        finally:
            self.proc = None
            self.log_queue.put("__DONE__")
            self.after(0, self._save_settings)  # after() is the thread-safe way to call back in from a worker thread

    def _cancel(self):
        if self.proc is not None:
            self.proc.terminate()
            self._log("\n[durduruldu]\n")
            self.suggest_label.configure(text="durduruldu")

    def _set_running(self, running: bool):
        state = "disabled" if running else "normal"
        self.run_btn.configure(state=state)
        self.dry_run_btn.configure(state=state)
        self.suggest_btn.configure(state=state)
        self.cancel_btn.configure(state=("normal" if running else "disabled"))
        self.status_var.set("çalışıyor..." if running else "hazır")

    # ------------------------------------------------------------ suggest

    def _suggest_size(self):
        if self.proc is not None:
            messagebox.showinfo("Cut-Ready", "Zaten bir işlem çalışıyor.")
            return
        inp = self.input_var.get()
        if not inp:
            messagebox.showerror("Cut-Ready", "Önce bir girdi dosyası seç.")
            return
        args = ["-i", inp, "-o", str(HERE / ".suggest_scratch"),
                "--min-thickness-mm", self.min_thickness.get() or "1.8",
                "--sharp-tip-deg", self.sharp_tip_deg.get() or "12",
                "--kerf-mm", self.kerf_mm.get() or "1.8",
                "--gap-factor", self.gap_factor.get() or "1.0"]
        # The search brackets itself off the width you are working at
        # (lo = 15% of it, hi = 4x). Leaving it out pins the reference at the
        # 400mm fallback, so on a large design the answer can fall outside the
        # range that was ever tried. The fallbacks above mirror the form's own
        # defaults for the same reason: the estimate has to measure the design
        # you are about to cut, not a differently-configured one.
        if self.width_mm.get():
            args += ["--width-mm", self.width_mm.get()]
        if self.work_res.get():
            args += ["--work-res", self.work_res.get()]
        if self.fill_gaps_var.get():
            args.append("--fill-narrow-gaps")
        args += ["--suggest-size", "--dry-run", "-q"]
        self._last_job_kind = "suggest"
        cmd = [find_python(), str(SCRIPT)] + args
        self._launch(cmd, is_suggest=True)

    # ------------------------------------------------------------ summary table

    def _refresh_summary(self):
        """Rebuild the Sonuç Özeti table from the JSON report of the last run."""
        for row in self.summary_tree.get_children():
            self.summary_tree.delete(row)
        path = self._last_report_path
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        status_tr = {"ok": ("temiz", "ok"), "ok_with_warnings": ("uyarı", "warn"),
                    "failed": ("hata", "fail")}
        for d in data.get("designs", []):
            name = Path(d.get("source", "?")).name
            status = d.get("status", "?")
            label, tag = status_tr.get(status, (status, ""))
            if status == "failed":
                detail = (d.get("errors") or ["?"])[0]
            else:
                detail = "; ".join(d.get("warnings", [])) or "—"
            self.summary_tree.insert("", "end", text=name, values=(label, detail), tags=(tag,))

    # ------------------------------------------------------------ log / poll

    def _log(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_queues(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                if line == "__DONE__":
                    self._set_running(False)
                    if self._last_job_kind == "run":
                        self._refresh_summary()
                else:
                    self._log(line)
        except queue.Empty:
            pass
        try:
            while True:
                text = self.suggest_queue.get_nowait()
                self.suggest_label.configure(text=text)
        except queue.Empty:
            pass
        self.after(150, self._poll_queues)


if __name__ == "__main__":
    if not SCRIPT.exists():
        print(f"FATAL: {SCRIPT} not found next to this GUI script.", file=sys.stderr)
        sys.exit(1)
    app = CncPrepGUI()
    app.mainloop()
