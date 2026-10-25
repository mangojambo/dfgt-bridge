# SPDX-License-Identifier: GPL-2.0-only
"""Renderizador pequeno dos SVGs próprios em Canvas Tk, sem dependências gráficas extras."""
from pathlib import Path
import xml.etree.ElementTree as ET

ASSETS = Path(__file__).with_name('assets')


def controls(model):
    return {e.attrib['data-control'] for e in ET.parse(ASSETS/(model+'-front.svg')).iter() if 'data-control' in e.attrib}


class WheelArt:
    def __init__(self, canvas, model, on_select):
        self.canvas, self.model, self.on_select = canvas, model, on_select
        self.active, self.selected, self.detected = set(), None, None
        self.canvas.bind('<Configure>', lambda event: self.draw(event.width, event.height))
        self.draw(430, 430)

    def draw(self, width, height):
        self.canvas.delete('all')
        self.parts = {}
        scale = min(width, height)/500
        dx, dy = (width-500*scale)/2, (height-500*scale)/2
        for e in ET.parse(ASSETS/(self.model+'-front.svg')).getroot():
            kind = e.tag.rsplit('}', 1)[-1]
            a = e.attrib
            key = a.get('data-control')
            opts = dict(fill=a.get('fill', ''), outline=a.get('stroke', ''), width=float(a.get('stroke-width', 1))*scale)
            if opts['fill'] == 'none':
                opts['fill'] = ''
            def xy(x, y):
                return dx+float(x)*scale, dy+float(y)*scale
            if kind == 'circle':
                x, y, r = float(a['cx']), float(a['cy']), float(a['r'])
                item = self.canvas.create_oval(*xy(x-r, y-r), *xy(x+r, y+r), **opts)
            elif kind == 'rect':
                x, y = float(a['x']), float(a['y'])
                item = self.canvas.create_rectangle(*xy(x, y), *xy(x+float(a['width']), y+float(a['height'])), **opts)
            elif kind == 'polygon':
                points = [xy(*p.split(',')) for p in a['points'].split()]
                item = self.canvas.create_polygon(*[v for p in points for v in p], **opts)
            elif kind == 'text':
                item = self.canvas.create_text(*xy(a['x'], a['y']), text=e.text,
                    fill=a.get('fill', 'white'), font=('DejaVu Sans', max(6, round(float(a.get('font-size', 12))*scale)) ))
            else:
                continue
            if key:
                tag = 'control:'+key
                self.canvas.addtag_withtag(tag, item)
                self.canvas.tag_bind(tag, '<Button-1>', lambda event, key=key: self.on_select(key))
                self.parts.setdefault(key, []).append((item, kind, opts))
        self.highlight(self.active, self.selected, self.detected)

    def highlight(self, active=(), selected=None, detected=None):
        self.active, self.selected, self.detected = set(active), selected, detected
        for key, parts in self.parts.items():
            for item, kind, original in parts:
                if kind == 'text':
                    continue
                self.canvas.itemconfigure(item, fill='#15803d' if key in self.active else original['fill'],
                    outline='#fbbf24' if key == detected else '#38bdf8' if key == selected else original['outline'],
                    width=3 if key in (selected, detected) else original['width'])
