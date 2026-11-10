"""Local first-run configuration. Opening a dashboard never starts attempts."""
import json
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import Store, default_data_dir, validate_imei


def setup():
    root = tk.Tk()
    root.title('HonorBoot - Open controller')
    root.geometry('640x340')
    root.resizable(False, False)
    config = default_data_dir() / 'launcher.json'
    try:
        saved = json.loads(config.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='HONORBOOT', font=('Consolas', 20, 'bold')).grid(row=0, column=0, sticky='w')
    ttk.Label(frame, text='Open your local controller. Attempts remain paused until you start them.').grid(row=1, column=0, columnspan=3, pady=(8, 16))
    fields = {}
    for row, (key, label) in enumerate([('tools', 'Platform-tools folder'), ('serial', 'Device serial'), ('imei', 'IMEI sequence seed')], 2):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=8)
        value = tk.StringVar(value=saved.get(key, ''))
        fields[key] = value
        ttk.Entry(frame, textvariable=value, width=42).grid(row=row, column=1, padx=8)
    def browse():
        path = filedialog.askdirectory(parent=root, title='Select official Android platform-tools folder')
        if path:
            fields['tools'].set(path)
    ttk.Button(frame, text='Browse', command=browse).grid(row=2, column=2)
    selected = []
    def open_controller():
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
            selected.append((args, store))
            root.destroy()
        except (OSError, ValueError) as exc:
            messagebox.showerror('Cannot open controller', str(exc), parent=root)
    ttk.Button(frame, text='Open dashboard', command=open_controller).grid(row=5, column=1, pady=18, sticky='e')
    root.mainloop()
    if selected:
        from .dashboard import open_dashboard
        open_dashboard(*selected[0])
