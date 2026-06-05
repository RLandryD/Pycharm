"""
core/screen_capture.py
- Visible screenshot overlay for ROI selection
- Single-frame capture + OCR
- Lottery OCR: contour-based blob detection with multi-preprocessing fallback
"""

import tkinter as tk
from PIL import Image, ImageEnhance, ImageFilter, ImageTk, ImageOps
import pytesseract
import mss
import re
import cv2
import numpy as np


# ── ROI Selector ──────────────────────────────────────────────────────────────

class RegionSelector:
    def __init__(self):
        self.result = None

    def select(self, title="Draw a box around the area") -> dict | None:
        self.result = None

        with mss.mss() as sct:
            monitor    = sct.monitors[0]
            raw        = sct.grab(monitor)
            screenshot = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            screen_w   = monitor["width"]
            screen_h   = monitor["height"]
            screen_x   = monitor["left"]
            screen_y   = monitor["top"]

        root = tk.Toplevel()
        root.title(title)
        root.geometry(f"{screen_w}x{screen_h}+{screen_x}+{screen_y}")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.config(cursor="crosshair")

        tk_img = ImageTk.PhotoImage(screenshot)
        canvas = tk.Canvas(root, width=screen_w, height=screen_h,
                           highlightthickness=0, cursor="crosshair")
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_image(0, 0, anchor="nw", image=tk_img)
        canvas.create_rectangle(0, 0, screen_w, screen_h,
                                 fill="#000000", stipple="gray25", outline="")

        canvas.create_rectangle(screen_w//2 - 300, 14, screen_w//2 + 300, 50,
                                 fill="#111111", outline="#00C896", width=1)
        canvas.create_text(screen_w//2, 32,
                           text=f"{title}   —   drag to select  |  ESC to cancel",
                           fill="#00C896", font=("Arial", 13, "bold"))

        state = {"x0": None, "y0": None, "rect": None, "shade": None}

        def on_press(e):
            state["x0"], state["y0"] = e.x, e.y
            for k in ("rect", "shade"):
                if state[k]: canvas.delete(state[k])

        def on_drag(e):
            for k in ("rect", "shade"):
                if state[k]: canvas.delete(state[k])
            x0, y0 = state["x0"], state["y0"]
            state["shade"] = canvas.create_rectangle(x0, y0, e.x, e.y,
                fill="#FFFFFF", stipple="gray12", outline="")
            state["rect"]  = canvas.create_rectangle(x0, y0, e.x, e.y,
                outline="#00FF88", width=2, dash=(5, 3))

        def on_release(e):
            x1 = min(state["x0"], e.x); y1 = min(state["y0"], e.y)
            x2 = max(state["x0"], e.x); y2 = max(state["y0"], e.y)
            if (x2-x1) > 8 and (y2-y1) > 8:
                self.result = {"left": screen_x+x1, "top": screen_y+y1,
                               "width": x2-x1, "height": y2-y1}
            root.destroy()

        def on_esc(e): root.destroy()

        canvas.bind("<ButtonPress-1>",   on_press)
        canvas.bind("<B1-Motion>",       on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.bind("<Escape>",            on_esc)
        root.focus_force()
        root.wait_window()
        return self.result


# ── Frame capture ─────────────────────────────────────────────────────────────

def capture_region(bbox: dict) -> Image.Image:
    with mss.mss() as sct:
        raw = sct.grab({"left": bbox["left"],  "top": bbox["top"],
                        "width": bbox["width"], "height": bbox["height"]})
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    w, h = img.size
    img  = img.resize((w*4, h*4), Image.LANCZOS)
    img  = img.convert("L")
    img  = ImageEnhance.Contrast(img).enhance(3.0)
    img  = img.filter(ImageFilter.SHARPEN)
    img  = img.filter(ImageFilter.SHARPEN)
    return img


def ocr_single_number(img: Image.Image) -> float | None:
    img    = preprocess_for_ocr(img)
    config = "--psm 7 -c tessedit_char_whitelist=0123456789."
    raw    = pytesseract.image_to_string(img, config=config).strip()
    m = re.search(r"\d+\.\d+", raw)
    if m:
        try: return float(m.group())
        except ValueError: pass
    m = re.search(r"\d+", raw)
    if m:
        try: return float(m.group())
        except ValueError: pass
    return None


# ── Lottery OCR ───────────────────────────────────────────────────────────────

def ocr_lottery_numbers(bbox: dict, expected_count: int = 6) -> list[int]:
    """
    Capture a lottery region and extract exactly `expected_count` integers.

    Strategy:
    1. Use OpenCV contour detection to find each heart/blob and crop it individually
    2. OCR each crop separately with multiple preprocessing variants
    3. If contour count doesn't match expected_count, fall back to full-row OCR
       with multiple PSM modes and preprocessing variants
    4. Return best result (closest to expected_count valid integers in 1-99 range)
    """
    img_pil = capture_region(bbox)

    # ── Method 1: Contour-based blob detection ────────────────────────────────
    result = _ocr_via_contours(img_pil, expected_count)
    if len(result) == expected_count:
        return result

    # ── Method 2: Full-row OCR with multiple variants ─────────────────────────
    result2 = _ocr_via_fullrow(img_pil, expected_count)
    if len(result2) == expected_count:
        return result2

    # Return whichever got closer to expected_count
    return result if len(result) >= len(result2) else result2


def _ocr_via_contours(img_pil: Image.Image, expected_count: int) -> list[int]:
    """
    Find blobs (hearts) via contour detection, crop each one, OCR individually.
    """
    # Convert to OpenCV
    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    h, w   = img_cv.shape[:2]

    # Scale up for better detection
    scale  = 3
    img_cv = cv2.resize(img_cv, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
    h, w   = img_cv.shape[:2]

    # Try multiple thresholding approaches to find blobs
    gray   = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    blobs  = []

    for method in ["otsu", "adaptive", "inv_otsu"]:
        if method == "otsu":
            _, thresh = cv2.threshold(gray, 0, 255,
                                      cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        elif method == "adaptive":
            thresh = cv2.adaptiveThreshold(gray, 255,
                                           cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY, 21, 5)
        else:
            _, thresh = cv2.threshold(gray, 0, 255,
                                      cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Find contours
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        # Filter by size: blobs should be roughly similar area
        areas = [cv2.contourArea(c) for c in contours]
        if not areas:
            continue

        # Expected blob area: spread evenly across the width
        min_area = (w * h) / (expected_count * 20)  # at least 1/20th of a slot
        max_area = (w * h) / (expected_count * 0.3)  # at most 3x a slot

        candidates = [c for c, a in zip(contours, areas)
                      if min_area < a < max_area]

        if abs(len(candidates) - expected_count) <= 2:
            blobs = candidates
            break

    if not blobs:
        return []

    # Sort left-to-right by centroid x
    def cx(c):
        M = cv2.moments(c)
        return int(M["m10"] / M["m00"]) if M["m00"] else 0

    blobs.sort(key=cx)

    # Take closest to expected_count (leftmost N)
    blobs = blobs[:expected_count]

    # OCR each blob crop
    results = []
    pil_big = img_pil.resize(
        (img_pil.width * scale, img_pil.height * scale), Image.LANCZOS)

    for contour in blobs:
        x, y, bw, bh = cv2.boundingRect(contour)
        # Add padding
        pad = max(4, bw // 6)
        x1  = max(0, x - pad);  y1 = max(0, y - pad)
        x2  = min(w, x + bw + pad); y2 = min(h, y + bh + pad)

        crop = pil_big.crop((x1, y1, x2, y2))
        num  = _ocr_crop_number(crop)
        if num is not None:
            results.append(num)

    return results


def _ocr_crop_number(crop: Image.Image) -> int | None:
    """
    Try multiple preprocessing variants on a single blob crop.
    Returns the first valid integer found (1–99), or None.
    """
    variants = _make_variants(crop)
    psm_modes = ["--psm 8", "--psm 7", "--psm 13", "--psm 10"]
    cfg_base  = " -c tessedit_char_whitelist=0123456789"

    for variant in variants:
        for psm in psm_modes:
            config = psm + cfg_base
            raw    = pytesseract.image_to_string(variant, config=config).strip()
            nums   = re.findall(r"\d+", raw)
            for n in nums:
                try:
                    v = int(n)
                    if 1 <= v <= 99:
                        return v
                except ValueError:
                    pass
    return None


def _ocr_via_fullrow(img_pil: Image.Image, expected_count: int) -> list[int]:
    """
    OCR the full row with multiple preprocessing and PSM modes.
    Return list closest in length to expected_count.
    """
    variants  = _make_variants(img_pil)
    psm_modes = ["--psm 7", "--psm 6", "--psm 13", "--psm 11"]
    cfg_base  = " -c tessedit_char_whitelist=0123456789 "

    best: list[int] = []
    for variant in variants:
        for psm in psm_modes:
            raw  = pytesseract.image_to_string(variant,
                   config=psm + cfg_base).strip()
            nums = [int(n) for n in re.findall(r"\d+", raw)
                    if 1 <= int(n) <= 99]
            if len(nums) == expected_count:
                return nums
            if abs(len(nums) - expected_count) < abs(len(best) - expected_count):
                best = nums

    return best


def _make_variants(img: Image.Image) -> list[Image.Image]:
    """
    Return several preprocessed versions of an image for OCR attempts.
    Covers: normal, inverted, high-contrast, otsu-threshold variants.
    """
    variants = []
    # Scale up
    w, h  = img.size
    scale = max(1, 200 // max(w, 1))  # ensure at least 200px wide
    scale = max(scale, 3)
    big   = img.resize((w * scale, h * scale), Image.LANCZOS)

    gray  = big.convert("L")

    # 1. High contrast grayscale
    v1 = ImageEnhance.Contrast(gray).enhance(3.0)
    v1 = v1.filter(ImageFilter.SHARPEN)
    variants.append(v1)

    # 2. Inverted high contrast
    variants.append(ImageOps.invert(v1))

    # 3. Otsu threshold (via numpy)
    arr    = np.array(gray)
    _, thr = cv2.threshold(arr, 0, 255,
                           cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(Image.fromarray(thr))

    # 4. Inverted Otsu
    variants.append(Image.fromarray(255 - thr))

    # 5. Adaptive threshold
    thr2 = cv2.adaptiveThreshold(arr, 255,
                                  cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY, 15, 4)
    variants.append(Image.fromarray(thr2))

    return variants
