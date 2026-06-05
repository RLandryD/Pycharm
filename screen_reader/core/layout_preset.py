"""core/layout_preset.py"""
import json, os, tkinter as tk

PRESET_FILE = os.path.join(os.path.dirname(__file__), "..", "presets.json")

def _screen():
    r = tk.Tk(); r.withdraw()
    w, h = r.winfo_screenwidth(), r.winfo_screenheight()
    r.destroy(); return w, h

def _rel(b, sw, sh): return {"lp":b["left"]/sw,"tp":b["top"]/sh,"wp":b["width"]/sw,"hp":b["height"]/sh}
def _abs(r, sw, sh): return {"left":int(r["lp"]*sw),"top":int(r["tp"]*sh),"width":int(r["wp"]*sw),"height":int(r["hp"]*sh)}

def save_preset(name, value_bbox, lottery_bbox=None):
    sw, sh = _screen()
    p = _load(); p[name] = {"sw":sw,"sh":sh,"value":_rel(value_bbox,sw,sh),
                             "lottery":_rel(lottery_bbox,sw,sh) if lottery_bbox else None}
    _save(p)

def load_preset(name):
    p = _load()
    if name not in p: return None
    sw, sh = _screen(); d = p[name]
    return {"value":_abs(d["value"],sw,sh),
            "lottery":_abs(d["lottery"],sw,sh) if d.get("lottery") else None}

def preset_names(): return list(_load().keys())

def delete_preset(name):
    p = _load(); p.pop(name, None); _save(p)

def _load():
    if os.path.exists(PRESET_FILE):
        with open(PRESET_FILE) as f: return json.load(f)
    return {}

def _save(p):
    with open(PRESET_FILE,"w") as f: json.dump(p,f,indent=2)
