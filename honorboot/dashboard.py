"""Native Windows-friendly dashboard with local command entry, no AI calls."""
import json
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk


def open_dashboard(args, store):
    from .__main__ import CONSENT
    root = tk.Tk(); root.title('HonorBoot - local controller'); root.geometry('1000x650')
    root.configure(bg='#10151b')
    root.minsize(760, 520)
    tk.Label(root, text='HONORBOOT  /  LOCAL CONTROLLER', bg='#10151b', fg='#69c8ff',
             font=('Consolas', 17, 'bold'), anchor='w').pack(fill='x', padx=16, pady=(16, 0))
    progress = ttk.Progressbar(root, maximum=100)
    progress.pack(fill='x', padx=16, pady=(10, 0))
    notice = tk.StringVar(value='Waiting for a saved checkpoint')
    tk.Label(root, textvariable=notice, bg='#10151b', fg='#ffd166', anchor='w',
             font=('Consolas', 11)).pack(fill='x', padx=16, pady=(8, 0))
    summary = tk.StringVar(value='Loading local progress...')
    tk.Label(root, textvariable=summary, bg='#10151b', fg='#63e6be', font=('Consolas', 12), justify='left', anchor='w').pack(fill='x', padx=16, pady=12)
    logs = scrolledtext.ScrolledText(root, bg='#10151b', fg='#d7e0e9', font=('Consolas', 10), state='disabled')
    logs.pack(fill='both', expand=True, padx=16)
    for name, color in [('info', '#69c8ff'), ('success', '#63e6be'), ('warning', '#ffd166'), ('error', '#ff7b86')]:
        logs.tag_configure(name, foreground=color)
    messages = queue.Queue()
    history = []
    history_position = 0
    tk.Label(root, text='status | pause | resume | reboot | fastboot | checkbattery | help', bg='#10151b', fg='#69c8ff').pack(pady=8)
    entry = tk.Entry(root, bg='#1c2632', fg='white', insertbackground='white', font=('Consolas', 13)); entry.pack(fill='x', padx=16, pady=8)
    busy = False
    log_offset = 0
    def append(text):
        follow = logs.yview()[1] >= 0.99
        logs.configure(state='normal')
        for line in text.splitlines():
            upper = line.upper()
            tag = ('error' if any(word in upper for word in ('ERROR', 'STOPPED', 'LOST', 'REVIEW_REQUIRED'))
                   else 'warning' if any(word in upper for word in ('PAUSED', 'CHARGING', 'WAITING', 'WARNING'))
                   else 'success' if any(word in upper for word in ('ACCEPTED', 'READY', 'TRY ')) else 'info')
            logs.insert('end', line + '\n', tag)
        if int(logs.index('end-1c').split('.')[0]) > 3000:
            logs.delete('1.0', '501.0')
        if follow: logs.see('end')
        logs.configure(state='disabled')
    def refresh():
        nonlocal log_offset
        try:
            s = json.loads(store.file.read_text(encoding='utf-8'))
            battery = s.get('battery_percent')
            age = (time.time() - s.get('battery_checked_at', 0)) / 3600
            status = s['status'].upper()
            progress['value'] = s.get('percent', 0)
            stale = time.time() - s.get('updated_at', 0) > 300
            inactive = status in ('PAUSED', 'ACCEPTED', 'EXHAUSTED', 'REVIEW_REQUIRED', 'STORAGE_OR_INTERNAL_ERROR')
            notice.set('ATTEMPTS STOPPED — ' + status if inactive else
                       'STATUS STALE — process may have stopped' if stale else
                       'ATTEMPTS PAUSED — charging' if status == 'CHARGING' else
                       'RECOVERY / TRANSITION — ' + status if status != 'PROBING' and status != 'RUNNING' else
                       'ATTEMPTS ACTIVE — ' + status)
            eta = s.get('eta_hours')
            summary.set(f"{status} | {s['next_index']:,}/{s['total']:,} | {s.get('percent',0):.3f}%\n"
                        f"Speed: {s.get('rate_per_hour',0):.0f}/hour | ETA: {f'{eta:.1f} hours' if eta else 'unavailable'}\n"
                        f"Battery: {str(battery)+'% reported '+f'{age:.2f}h ago' if battery is not None else 'unknown'}\n"
                        f"Serial: {s['serial']} | IMEI seed: {s['imei']}\n"
                        f"Log age: {time.time()-s.get('updated_at',0):.0f}s | Data: {store.path}")
            path = store.path / 'events.log'
            if path.exists():
                with path.open(encoding='utf-8') as f:
                    f.seek(log_offset); text = f.read(); log_offset = f.tell()
                if text: append(text.rstrip())
        except (OSError, ValueError, KeyError, TypeError) as exc:
            notice.set('CHECKPOINT UNAVAILABLE — ' + str(exc))
        while not messages.empty():
            finish(messages.get_nowait())
        root.after(1000, refresh)
    def finish(message):
        nonlocal busy
        busy = False; append(message)
    def submit(event=None):
        nonlocal busy, history_position
        command = entry.get().strip().lower(); entry.delete(0,'end')
        if not command: return
        history.append(command); history_position = len(history)
        command = {'start': 'resume', 'stop': 'pause', 'battery': 'checkbattery', 'android': 'reboot', '?': 'help'}.get(command, command)
        if command == 'help':
            append('COMMANDS\n  status       Show saved checkpoint\n  pause / stop Stop attempts and preserve progress\n  resume / start Start with explicit data-erasure consent\n  reboot / android Return to Android (pause first)\n  fastboot     Enter bootloader (pause first)\n  checkbattery / battery Read battery (pause first)\n  clear        Clear display only; saved logs remain\n  help / ?     Show this reference\nUp/Down: command history. Closing the window does not stop the worker.')
            return
        if command == 'clear':
            logs.configure(state='normal'); logs.delete('1.0', 'end'); logs.configure(state='disabled'); return
        if command not in ('status','pause','resume','reboot','fastboot','checkbattery'): append('Unknown command'); return
        if busy: append('A controller command is still running'); return
        approved = command != 'resume' or messagebox.askyesno('Possible data erasure', 'A valid code can immediately unlock and erase all data. No guaranteed second PC confirmation. Start attempts?', parent=root)
        if not approved: return
        busy = True
        def work():
            try:
                prefix=([sys.executable] if getattr(sys, 'frozen', False) else [sys.executable,'-m','honorboot']) + ['--tools',str(args.tools),'--data',str(args.data),'--serial',args.serial,'--imei',args.imei]
                if command == 'resume':
                    out=(store.path/'worker-output.log').open('a',encoding='utf-8')
                    try:
                        p=subprocess.Popen(prefix+['run'], stdin=subprocess.PIPE, stdout=out, stderr=out, text=True, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        p.stdin.write(CONSENT+'\n'); p.stdin.close()
                    finally: out.close()
                    message='Runner launched. Check STATUS for actual progress; a launch is not proof of successful connection.'
                else:
                    result=subprocess.run(prefix+[command],capture_output=True,text=True,timeout=400,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    message=result.stdout+result.stderr
                messages.put(message)
            except Exception as exc: messages.put('ERROR: '+str(exc))
        threading.Thread(target=work,daemon=True).start()
    def navigate_history(direction):
        nonlocal history_position
        history_position = max(0, min(len(history), history_position + direction))
        entry.delete(0, 'end')
        if history_position < len(history): entry.insert(0, history[history_position])
        return 'break'
    entry.bind('<Up>', lambda event: navigate_history(-1))
    entry.bind('<Down>', lambda event: navigate_history(1))
    entry.bind('<Return>',submit)
    actions = tk.Frame(root, bg='#10151b'); actions.pack(fill='x', padx=16)
    def quick(command):
        entry.delete(0, 'end'); entry.insert(0, command); submit()
    for label, command in [('Status', 'status'), ('Pause', 'pause'), ('Resume…', 'resume'), ('Battery', 'battery'), ('Help', 'help')]:
        tk.Button(actions, text=label, command=lambda c=command: quick(c), bg='#1c2632', fg='#d7e0e9',
                  relief='flat', padx=14, pady=6).pack(side='left', padx=(0, 6))
    tk.Button(root,text='Execute',command=submit).pack(pady=5)
    append('Local controller only. Closing this dashboard does not stop the runner. Use pause to stop attempts.')
    refresh(); entry.focus_set(); root.mainloop()
