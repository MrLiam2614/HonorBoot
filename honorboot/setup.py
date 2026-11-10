"""Local first-run configuration. Opening a dashboard never starts attempts."""
import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import Store, default_data_dir, validate_imei


def setup():
    root = tk.Tk()
    root.title('HonorBoot - Open controller')
    root.geometry('760x440')
    root.resizable(False, False)
    root.configure(bg='#10151b')
    config = default_data_dir() / 'launcher.json'
    try:
        saved = json.loads(config.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    frame = tk.Frame(root, bg='#10151b', padx=24, pady=24)
    frame.pack(fill='both', expand=True)
    tk.Label(frame, text='HONORBOOT / LOCAL CONTROLLER', bg='#10151b', fg='#63e6be', font=('Consolas', 18, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w')
    tk.Label(frame, text='Configure your device. No attempts start from this screen.', bg='#10151b', fg='#69c8ff', font=('Consolas', 10)).grid(row=1, column=0, columnspan=3, pady=(8, 16))
    fields = {}
    results = queue.Queue()
    detecting = False
    for row, (key, label) in enumerate([('tools', 'Platform-tools folder'), ('serial', 'Device serial'), ('imei', 'IMEI sequence seed')], 2):
        tk.Label(frame, text=label, bg='#10151b', fg='#d7e0e9', font=('Consolas', 10)).grid(row=row, column=0, sticky='w', pady=8)
        value = tk.StringVar(value=saved.get(key, ''))
        fields[key] = value
        ttk.Combobox(frame, textvariable=value, width=38).grid(row=row, column=1, padx=8) if key == 'imei' else tk.Entry(frame, textvariable=value, width=38, bg='#1c2632', fg='#63e6be', insertbackground='#63e6be', relief='flat', font=('Consolas', 11)).grid(row=row, column=1, padx=8)
    def browse():
        path = filedialog.askdirectory(parent=root, title='Select official Android platform-tools folder')
        if path:
            fields['tools'].set(path)
    tk.Button(frame, text='Browse', command=browse, bg='#1c2632', fg='#69c8ff', relief='flat', padx=10, pady=5).grid(row=2, column=2)
    info = tk.StringVar(value='Detecting tools and connected device...')
    tk.Label(frame, textvariable=info, bg='#10151b', fg='#ffd166', wraplength=690,
             justify='left', font=('Consolas', 10)).grid(row=6, column=0, columnspan=3, sticky='w')
    def detect():
        nonlocal detecting
        if detecting or transitioning: return
        detecting = True
        snapshot = {key: value.get().strip() for key, value in fields.items()}
        info.set('Detecting in background... You can still edit the fields.')
        def work():
            from .discovery import detect_configuration
            try: results.put((snapshot, detect_configuration(snapshot, default_data_dir()), None))
            except Exception as exc: results.put((snapshot, None, str(exc)))
        threading.Thread(target=work, daemon=True).start()
    def receive():
        nonlocal detecting
        if transitioning: return
        try:
            snapshot, found, error = results.get_nowait()
            detecting = False
            if error: info.set(error)
            else:
                for key in fields:
                    if fields[key].get().strip() == snapshot[key] and key in found:
                        fields[key].set(found[key])
                for widget in frame.grid_slaves(row=4, column=1):
                    widget.configure(values=found.get('imeis', []))
                info.set(found['message'])
        except queue.Empty: pass
        root.after(100, receive)
    tk.Button(frame, text='DETECT DEVICE', command=detect, bg='#1c2632', fg='#69c8ff',
              relief='flat', padx=12, pady=10).grid(row=5, column=0, pady=18)
    root.after(100, detect)
    root.after(100, receive)
    transitioning = False
    def open_controller():
        nonlocal transitioning
        try:
            values = {key: value.get().strip() for key, value in fields.items()}
            tools = Path(values['tools']).resolve()
            if not all((tools / name).is_file() for name in ('adb.exe', 'fastboot.exe')):
                raise ValueError('Select a folder containing official adb.exe and fastboot.exe.')
            if not values['serial']:
                raise ValueError('Enter the device serial shown by adb devices or fastboot devices.')
            validate_imei(values['imei'])
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps(values, indent=2), encoding='utf-8')
            args = SimpleNamespace(tools=tools, data=default_data_dir(), serial=values['serial'], imei=values['imei'])
            store = Store(args.data, args.serial, args.imei, reconcile=False)
            if not store.file.exists():
                store.save()
            transitioning = True
            frame.destroy()
            root.resizable(True, True)
            from .dashboard import open_dashboard
            open_dashboard(args, store, root=root)
        except (OSError, ValueError) as exc:
            messagebox.showerror('Cannot open controller', str(exc), parent=root)
    tk.Button(frame, text='OPEN DASHBOARD >', command=open_controller, bg='#1c2632', fg='#63e6be', relief='flat', font=('Consolas', 12, 'bold'), padx=16, pady=10).grid(row=5, column=1, pady=18, sticky='e')
    root.mainloop()

