#!/usr/bin/env python3
"""
Zip Resource Extractor / Renamer / Re-orderer
=============================================

What it does
------------
1. You pick a SOURCE folder that contains .zip files.
2. You pick an OUTPUT folder.
3. For every .zip in the source folder, the tool:
     - recurses into nested zips (zip-in-zip-in-zip),
     - finds EVERY folder named `resources` (the "anchor", editable in the UI),
     - extracts ALL files located beneath each `resources` folder,
     - renames each file to:  "<outer-zip-name> - <original name>.<original ext>"
       (the prefix is always the OUTERMOST zip's name, original case kept),
     - sorts each file into an output sub-folder named after its extension,
       Title-cased, merging case variants (JSON and json -> "Json").
4. If two files would land in the same folder with the same final name,
   both are kept by appending -1, -2, ... (no overwriting, no concatenation).
5. A zip with no `resources` folder anywhere inside it is skipped.

Run on Linux Mint:
    sudo apt install python3-tk        # if Tkinter is not already present
    python3 zip_extractor.py
"""

import io
import os
import queue
import threading
import zipfile


# --------------------------------------------------------------------------- #
#  Core logic (no GUI dependency, so it can be tested on its own)
# --------------------------------------------------------------------------- #

def _path_parts(name):
    """Split a zip entry path into clean components."""
    return [p for p in name.replace("\\", "/").split("/") if p]


def _under_anchor(name, anchor):
    """True if `anchor` appears as an ANCESTOR folder of this file.

    i.e. the anchor must be somewhere before the final (file-name) component.
    Matching is case-insensitive.
    """
    parts = _path_parts(name)
    anchor = anchor.lower()
    return any(p.lower() == anchor for p in parts[:-1])


# Local-file-header signatures used by zip-format archives (PK\x03\x04),
# plus the empty-archive and spanned-archive variants. Any file beginning
# with one of these is a zip container regardless of its name/extension
# (this also covers .jar and SAP "..._content" files, which are zips).
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _looks_like_zip(data):
    """True if the given bytes start with a zip-format signature."""
    return data[:4] in _ZIP_SIGNATURES


def collect_from_zip(source, outer_name, anchor, results, log):
    """Recursively read a zip (a path string OR a file-like object).

    Appends (basename, data_bytes) to `results` for every file located
    beneath an `anchor` folder, at any nesting depth, including inside
    nested zips. The prefix name passed down is always the OUTERMOST zip.
    """
    try:
        zf = zipfile.ZipFile(source)
    except zipfile.BadZipFile:
        log(f"  ! Not a valid zip, skipping this part.")
        return

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename

            try:
                data = zf.read(info)
            except Exception as e:
                log(f"  ! Could not read '{name}': {e}")
                continue

            # A nested archive: detected by its bytes (PK signature), so it
            # works even when the inner file has no .zip extension (e.g. a
            # SAP '..._content' file or a .jar). Recurse into it.
            if _looks_like_zip(data):
                try:
                    collect_from_zip(
                        io.BytesIO(data), outer_name, anchor, results, log)
                except Exception as e:
                    log(f"  ! Could not read nested archive '{name}': {e}")
                continue

            # A regular file: keep it only if it lives under the anchor folder.
            if _under_anchor(name, anchor):
                results.append((_path_parts(name)[-1], data))


def target_folder_name(basename):
    """Output sub-folder for a file, based on its extension.

    Title-cased and case-merged:  .XSLT / .xslt  ->  'Xslt'.
    Files with no extension go into 'Other'.
    """
    ext = os.path.splitext(basename)[1]   # includes the leading dot, or ''
    if not ext:
        return "Other"
    return ext[1:].lower().title()


def unique_destination(folder, filename):
    """Return a path in `folder` that does not yet exist, appending
    -1, -2, ... before the extension on collision."""
    dest = os.path.join(folder, filename)
    if not os.path.exists(dest):
        return dest
    stem, ext = os.path.splitext(filename)
    i = 1
    while True:
        candidate = os.path.join(folder, f"{stem}-{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def process_source_folder(src, out, anchor, log):
    """Process every .zip in `src`, writing results into `out`.

    Returns (processed, skipped, files_written).
    """
    if not os.path.isdir(src):
        log("Source folder does not exist.")
        return 0, 0, 0

    # Pick candidate archives by CONTENT (PK signature), not by extension,
    # so files named '..._content', '.jar', or with no extension are caught.
    candidates = []
    for f in sorted(os.listdir(src)):
        fpath = os.path.join(src, f)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, "rb") as fh:
                head = fh.read(4)
        except Exception:
            continue
        if _looks_like_zip(head):
            candidates.append(f)

    if not candidates:
        log("No zip-format archives found in the source folder.")
        return 0, 0, 0

    os.makedirs(out, exist_ok=True)

    processed = skipped = files_written = 0

    for zname in candidates:
        zpath = os.path.join(src, zname)
        # Outermost prefix = file name with any extension stripped.
        # For names with no extension (e.g. '..._content'), keep as-is.
        outer_name = os.path.splitext(zname)[0]
        log(f"Processing: {zname}")

        results = []
        collect_from_zip(zpath, outer_name, anchor, results, log)

        if not results:
            log(f"  - No '{anchor}' folder found inside; skipped.")
            skipped += 1
            continue

        for basename, data in results:
            final_name = f"{outer_name} - {basename}"
            folder = os.path.join(out, target_folder_name(basename))
            os.makedirs(folder, exist_ok=True)
            dest = unique_destination(folder, final_name)
            with open(dest, "wb") as fh:
                fh.write(data)
            files_written += 1

        log(f"  + Extracted {len(results)} file(s).")
        processed += 1

    log("")
    log(f"Done. {processed} zip(s) processed, {skipped} skipped, "
        f"{files_written} file(s) written.")
    return processed, skipped, files_written


# --------------------------------------------------------------------------- #
#  GUI
# --------------------------------------------------------------------------- #

class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk, scrolledtext
        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self._scrolledtext = scrolledtext

        self.root = root
        root.title("Zip Resource Extractor")
        root.minsize(640, 460)

        self.src = tk.StringVar()
        self.out = tk.StringVar()
        self.anchor = tk.StringVar(value="resources")
        self.log_queue = queue.Queue()
        self.worker = None

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(root, padding=12)
        frm.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        # Source folder
        ttk.Label(frm, text="Source folder (with .zip files):").grid(
            row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.src).grid(
            row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse…", command=self.pick_src).grid(
            row=0, column=2, **pad)

        # Output folder
        ttk.Label(frm, text="Output folder:").grid(
            row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.out).grid(
            row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse…", command=self.pick_out).grid(
            row=1, column=2, **pad)

        # Anchor folder name
        ttk.Label(frm, text="Anchor folder name:").grid(
            row=2, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.anchor, width=20).grid(
            row=2, column=1, sticky="w", **pad)

        # Run button
        self.run_btn = ttk.Button(frm, text="Extract", command=self.run)
        self.run_btn.grid(row=3, column=0, columnspan=3, pady=(10, 6))

        # Log area
        self.text = scrolledtext.ScrolledText(
            frm, height=16, state="disabled", wrap="word")
        self.text.grid(row=4, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(4, weight=1)

        self.root.after(100, self._drain_log)

    # --- folder pickers ---------------------------------------------------- #
    def pick_src(self):
        d = self._filedialog.askdirectory(title="Select the folder containing your .zip files")
        if d:
            self.src.set(d)

    def pick_out(self):
        d = self._filedialog.askdirectory(title="Select the output folder")
        if d:
            self.out.set(d)

    # --- logging (thread-safe) -------------------------------------------- #
    def log(self, msg):
        self.log_queue.put(msg)

    def _drain_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.text.configure(state="normal")
                self.text.insert("end", msg + "\n")
                self.text.see("end")
                self.text.configure(state="disabled")
        except queue.Empty:
            pass

        # Re-enable the button once the worker has finished.
        if self.worker is not None and not self.worker.is_alive():
            if str(self.run_btn["state"]) == "disabled":
                self.run_btn.configure(state="normal")

        self.root.after(100, self._drain_log)

    # --- run --------------------------------------------------------------- #
    def run(self):
        src = self.src.get().strip()
        out = self.out.get().strip()
        anchor = self.anchor.get().strip() or "resources"

        if not src or not out:
            self._messagebox.showwarning(
                "Missing folder",
                "Please choose both a source folder and an output folder.")
            return
        if os.path.abspath(src) == os.path.abspath(out):
            self._messagebox.showwarning(
                "Same folder",
                "Source and output folders should be different.")
            return
        if self.worker is not None and self.worker.is_alive():
            return

        self.run_btn.configure(state="disabled")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

        def task():
            try:
                process_source_folder(src, out, anchor, self.log)
            except Exception as e:
                self.log(f"ERROR: {e}")

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()


def main():
    import tkinter as tk
    from tkinter import ttk, scrolledtext  # noqa: F401
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()