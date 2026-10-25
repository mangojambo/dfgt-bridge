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

            for geometry in ('1280x800', '1600x900'):
                root.geometry(geometry)
                app.tabs.select(app.mapping)
                root.update_idletasks()
                self.assertGreater(app.dfgt_canvas.winfo_width(), 200)
                self.assertGreater(app.g29_canvas.winfo_height(), 200)
                for art in (app.dfgt_art, app.g29_art):
                    for item in art.canvas.find_all():
                        bounds = art.canvas.bbox(item)
                        if bounds:
                            self.assertGreaterEqual(bounds[0], 0)
                            self.assertGreaterEqual(bounds[1], 0)
                            self.assertLessEqual(bounds[2], art.canvas.winfo_width())
                            self.assertLessEqual(bounds[3], art.canvas.winfo_height())
                self.assertGreaterEqual(app.mapping_inspector.winfo_width(), 340)
                app.tabs.select(app.settings)
                root.update_idletasks()
                self.assertFalse(app.settings_advanced.winfo_ismapped())
                self.assertFalse(app.input_details.winfo_ismapped())
                app.toggle_advanced()
                app.toggle_input_details()
                root.update_idletasks()
                self.assertTrue(app.settings_advanced.winfo_ismapped())
                self.assertTrue(app.input_details.winfo_ismapped())
                app.toggle_advanced()
                app.toggle_input_details()

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
