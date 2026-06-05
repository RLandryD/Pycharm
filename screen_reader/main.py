"""
Melate Screen Reader — main.py
Run this file from the screen_reader/ directory.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.app_window import AppWindow
import tkinter as tk

def main():
    root = tk.Tk()
    app  = AppWindow(root)
    root.mainloop()

if __name__ == "__main__":
    main()
