"""Exercise real Tk widget construction when a graphical display is available."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

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

            for locale, language_label in (('en', 'English'), ('pt-BR', 'Português (Brasil)')):
                if app.locale != locale:
                    app.language.set(language_label)
                    app.change_language()
                for geometry in ('1000x700', '1280x800', '1600x900'):
                    root.geometry(geometry)
                    app.tabs.select(app.mapping)
                    root.update_idletasks()
                    root.update()
                    layout_width = app.mapping_layout.winfo_width()
                    self.assertGreater(layout_width, 0)
                    self.assertEqual(app.mapping_wide, layout_width >= 1180)
                    self.assertGreater(app.dfgt_canvas.winfo_width(), 0)
                    self.assertGreater(app.g29_canvas.winfo_height(), 0)
                    for card, canvas in ((app.dfgt_card, app.dfgt_canvas), (app.g29_card, app.g29_canvas)):
                        self.assertLessEqual(card.winfo_width()-canvas.winfo_width(), 24)
                        if app.mapping_wide:
                            self.assertGreater(canvas.winfo_width(), 200)
                    for art in (app.dfgt_art, app.g29_art):
                        for item in art.canvas.find_all():
                            bounds = art.canvas.bbox(item)
                            if bounds:
                                self.assertGreaterEqual(bounds[0], 0)
                                self.assertGreaterEqual(bounds[1], 0)
                                self.assertLessEqual(bounds[2], art.canvas.winfo_width())
                                self.assertLessEqual(bounds[3], art.canvas.winfo_height())
                    self.assertGreaterEqual(app.mapping_inspector.winfo_width(), 340)
                    self.assertGreater(app.source_x_scrollbar.winfo_width(), 10)
                    self.assertNotEqual(str(app.target_box.cget('takefocus')), '0')
                    scroll_view = app._scroll_views[app.mapping][0]
                    self.assertEqual(len(scroll_view.cget('scrollregion').split()), 4)
                    if app.mapping_wide:
                        self.assertGreaterEqual(app.mapping_inspector.winfo_rootx(),
                                                app.g29_card.winfo_rootx()+app.g29_card.winfo_width()-2)
                    else:
                        wheel_bottom = max(app.dfgt_card.winfo_rooty()+app.dfgt_card.winfo_height(),
                                           app.g29_card.winfo_rooty()+app.g29_card.winfo_height())
                        self.assertGreater(app.mapping_inspector.winfo_rooty(), wheel_bottom-2)
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

            style = ttk.Style(root)
            self.assertTrue(style.lookup('TButton', 'focuscolor'))
            self.assertEqual(str(app.passive_start_button.cget('style')), 'Passive.TButton')
            self.assertEqual(str(app.physical_start_button.cget('style')), 'Physical.TButton')
            self.assertEqual(str(app.stop_button.cget('style')), 'Stop.TButton')
            self.assertEqual(app.locale_store.load(), 'pt-BR')
            app.language.set('English')
            app.change_language()
            self.assertEqual(app.locale, 'en')
            self.assertEqual(app.locale_store.load(), 'en')

            app.close()
            self.assertFalse(root.winfo_exists())


if __name__ == '__main__':
    unittest.main()
