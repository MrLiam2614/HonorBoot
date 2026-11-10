import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from honorboot.core import Store
from honorboot.dashboard import open_dashboard


class DashboardTests(unittest.TestCase):
    def test_reuses_window_and_keeps_manual_controls_collapsed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), 'TEST', '490154203237518')
            store.save()
            root = tk.Tk()
            root.withdraw()
            try:
                with patch('honorboot.dashboard.tk.Tk', side_effect=AssertionError('Second window created')):
                    open_dashboard(SimpleNamespace(), store, root=root)
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)
                widgets = list(descendants(root))
                buttons = {w.cget('text'): w for w in widgets if isinstance(w, tk.Button)}
                self.assertIn('▶  PLAY', buttons)
                self.assertIn('■  STOP', buttons)
                entry = next(w for w in widgets if isinstance(w, tk.Entry))
                self.assertFalse(entry.master.winfo_manager())
                buttons['Manual controls ▸'].invoke()
                self.assertEqual(entry.master.winfo_manager(), 'pack')
                buttons['■  STOP'].invoke()
                self.assertTrue((store.path / 'STOP').exists())
                self.assertEqual(store.state['next_index'], 0)
            finally:
                root.destroy()
