"""Exercise real Tk widget construction when a graphical display is available."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from test_ffb_audit import ROOT
from dfgt_bridge.config import ProfileStore
from dfgt_bridge.gui import BridgeGUI


class NativeTkStartupTests(unittest.TestCase):
    def test_all_panels_locale_switch_and_clean_window_destruction(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Native Tk display is unavailable: {exc}')
        self.addCleanup(lambda: root.winfo_exists() and root.destroy())
        root.geometry('1280x800')
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory)/'profiles.json')
            app = BridgeGUI(root, store)
            self.assertEqual(root.title(), app.t('app.title'))
            for page in (app.home, app.mapping, app.settings, app.diagnostics):
                self.assertIs(page.master, app.tabs)
                self.assertTrue(page.winfo_exists())
            self.assertEqual(app.profile_name.get(), 'dirt5')

            app.language.set('Português (Brasil)')
            app.change_language()
            self.assertEqual(app.locale, 'pt-BR')
            self.assertEqual(root.title(), app.t('app.title'))
            self.assertEqual(app.locale_store.load(), 'pt-BR')
            app.language.set('English')
            app.change_language()
            self.assertEqual(app.locale, 'en')
            self.assertEqual(app.locale_store.load(), 'en')

            app.close()
            self.assertFalse(root.winfo_exists())


if __name__ == '__main__':
    unittest.main()
