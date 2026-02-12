#!/usr/bin/env python3

import ctypes
import os
import platform
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import dearpygui.dearpygui as dpg

PROJECT_ROOT = Path(__file__).resolve().parent
CLI_SCRIPT = PROJECT_ROOT / "videodiff.py"

DITHERING_METHODS = ["r", "g", "b", "a", "m", "n"]
IMAGE_METHODS = ["r", "g", "b", "a", "m"]
BACKENDS = ["any", "msmf", "dshow", "ffmpeg", "gstreamer", "v4l2"]

VIDEO_FILE_FILTER = (
    "Video files (*.avi *.mp4 *.mkv *.mov *.ts)"
    "{.avi,.mp4,.mkv,.mov,.wmv,.flv,.webm,.m4v,.mpg,.mpeg,.ts}"
)
IMAGE_FILE_FILTER = (
    "Image files (*.png *.jpg *.tiff *.bmp)"
    "{.png,.jpg,.jpeg,.tiff,.tif,.bmp,.webp,.ppm}"
)

IS_WINDOWS = platform.system() == "Windows"
_dpi_scale = 1.0


def _setup_dpi_awareness():
    """Enable per-monitor DPI awareness on Windows.

    Without this, Windows stretches the rendered bitmap to match the display
    scaling, which makes fonts look blurry.
    """
    global _dpi_scale
    if not IS_WINDOWS:
        return

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        _dpi_scale = dpi / 96.0
    except Exception:
        _dpi_scale = 1.0


def _s(v):
    """Scale a pixel size by the DPI factor."""
    return int(v * _dpi_scale)


# --- Widget / group tags ---
TAG_MODE = "mode"
TAG_SOURCE_TYPE = "source_type"
TAG_METHOD = "method"
TAG_FILL_VALUE = "fill_value"
TAG_DISPLAY = "display"
TAG_PAUSE = "pause"
TAG_OUTPUT_DIR = "output_dir"
TAG_CAP_INDEX = "cap_index"
TAG_VIDEO_FILE = "video_file"
TAG_IMAGE_FILE_A = "image_file_a"
TAG_IMAGE_FILE_B = "image_file_b"
TAG_BACKEND = "backend"
TAG_FOURCC = "fourcc"
TAG_WIDTH = "width"
TAG_HEIGHT = "height"
TAG_FPS = "fps"
TAG_LOG = "log_output"
TAG_STATUS = "status_text"
TAG_ELAPSED = "elapsed_text"
TAG_START = "start_btn"
TAG_STOP = "stop_btn"

GROUP_DITHERING = "group_dithering"
GROUP_DITHERING_CAP = "group_dithering_cap"
GROUP_DITHERING_FILE = "group_dithering_file"
GROUP_IMAGE = "group_image"
GROUP_FILL_VALUE = "group_fill_value"

EVENT_LOG = "log"
EVENT_STATE = "state"
EVENT_CLEAR = "clear"

_events = queue.Queue()
_process_lock = threading.Lock()
_process = None
_start_time = None


# ---------------------------------------------------------------------------
# Font & theme
# ---------------------------------------------------------------------------


def _load_cjk_font():
    if IS_WINDOWS:
        fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        candidates = [
            fonts_dir / "msyh.ttc",
            fonts_dir / "msyhbd.ttc",
            fonts_dir / "simsun.ttc",
            fonts_dir / "simhei.ttf",
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
        ]
    for path in candidates:
        if path.exists():
            with dpg.font_registry():
                with dpg.font(str(path), _s(16)) as fid:
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Chinese_Full)
                    dpg.add_font_range_hint(
                        dpg.mvFontRangeHint_Chinese_Simplified_Common
                    )
            dpg.bind_font(fid)
            return


def _create_theme():
    colors = {
        dpg.mvThemeCol_WindowBg: (30, 30, 30),
        dpg.mvThemeCol_TitleBg: (20, 20, 20),
        dpg.mvThemeCol_TitleBgActive: (40, 60, 80),
        dpg.mvThemeCol_FrameBg: (50, 50, 55),
        dpg.mvThemeCol_FrameBgHovered: (65, 65, 70),
        dpg.mvThemeCol_FrameBgActive: (75, 75, 80),
        dpg.mvThemeCol_Button: (55, 75, 110),
        dpg.mvThemeCol_ButtonHovered: (70, 95, 135),
        dpg.mvThemeCol_ButtonActive: (85, 110, 155),
        dpg.mvThemeCol_Header: (45, 65, 95),
        dpg.mvThemeCol_HeaderHovered: (55, 80, 115),
        dpg.mvThemeCol_HeaderActive: (65, 90, 130),
        dpg.mvThemeCol_Separator: (60, 60, 65),
        dpg.mvThemeCol_Text: (220, 220, 220),
        dpg.mvThemeCol_CheckMark: (120, 175, 240),
        dpg.mvThemeCol_SliderGrab: (90, 130, 185),
        dpg.mvThemeCol_SliderGrabActive: (110, 150, 210),
        dpg.mvThemeCol_ScrollbarBg: (25, 25, 28),
        dpg.mvThemeCol_ScrollbarGrab: (60, 60, 65),
        dpg.mvThemeCol_Tab: (40, 55, 75),
        dpg.mvThemeCol_TabHovered: (55, 80, 115),
        dpg.mvThemeCol_TabActive: (50, 70, 100),
    }
    styles = [
        (dpg.mvStyleVar_FrameRounding, 4),
        (dpg.mvStyleVar_WindowRounding, 6),
        (dpg.mvStyleVar_GrabRounding, 4),
        (dpg.mvStyleVar_FramePadding, 6, 4),
    ]
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            for key, val in colors.items():
                dpg.add_theme_color(key, val)
            for key, *vals in styles:
                dpg.add_theme_style(key, *vals)
    return theme


# ---------------------------------------------------------------------------
# Thread-safe event helpers
# ---------------------------------------------------------------------------


def _queue_log(msg):
    _events.put((EVENT_LOG, msg))


def _queue_state(running, code=None):
    _events.put((EVENT_STATE, running, code))


def _clear_log(sender=None, app_data=None, user_data=None):
    _events.put((EVENT_CLEAR,))


def _append_log(line):
    cur = dpg.get_value(TAG_LOG) or ""
    cur = f"{cur}\n{line}" if cur else line
    if len(cur) > 60_000:
        cut = cur.find("\n", len(cur) - 60_000)
        cur = cur[cut + 1 :] if cut != -1 else cur[-60_000:]
    dpg.set_value(TAG_LOG, cur)


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


def _parse_fourcc():
    raw = dpg.get_value(TAG_FOURCC).strip()
    if not raw:
        return None
    if len(raw) != 4:
        raise ValueError("FOURCC must be exactly 4 characters")
    return raw.upper()


def _require_file(path, label):
    if not Path(path).is_file():
        raise ValueError(f"{label} does not exist: {path}")


def _build_command():
    mode = dpg.get_value(TAG_MODE)
    output_dir = dpg.get_value(TAG_OUTPUT_DIR).strip()
    method = dpg.get_value(TAG_METHOD)

    args = [
        sys.executable,
        str(CLI_SCRIPT),
        "--mode",
        mode,
        "--dither-method",
        method,
    ]

    if method == "m":
        args.extend(["--fill-value", str(dpg.get_value(TAG_FILL_VALUE))])

    if dpg.get_value(TAG_DISPLAY):
        args.append("--display")
    if dpg.get_value(TAG_PAUSE) and mode == "dithering":
        args.append("--pause")
    if output_dir:
        args.extend(["--output", output_dir])

    if mode == "dithering":
        if dpg.get_value(TAG_SOURCE_TYPE) == "capture":
            args.extend(["--cap", str(dpg.get_value(TAG_CAP_INDEX))])
            args.extend(["--backend", dpg.get_value(TAG_BACKEND) or "any"])

            for flag, tag in [("--width", TAG_WIDTH), ("--height", TAG_HEIGHT)]:
                val = dpg.get_value(tag)
                if val > 0:
                    args.extend([flag, str(val)])
            fps = dpg.get_value(TAG_FPS)
            if fps > 0:
                args.extend(["--fps", str(fps)])
            fourcc = _parse_fourcc()
            if fourcc:
                args.extend(["--fourcc", fourcc])
        else:
            video_file = dpg.get_value(TAG_VIDEO_FILE).strip()
            if not video_file:
                raise ValueError("Please choose a video file first")
            _require_file(video_file, "Video file")
            args.extend(["--file", video_file])

    elif mode == "image":
        file_a = dpg.get_value(TAG_IMAGE_FILE_A).strip()
        file_b = dpg.get_value(TAG_IMAGE_FILE_B).strip()
        if not file_a or not file_b:
            raise ValueError("Image mode requires two image files")
        _require_file(file_a, "Image A")
        _require_file(file_b, "Image B")
        args.extend(["--file", file_a, file_b])

    return args


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------


def _kill_tree(proc):
    """Kill a process and all its children (cross-platform)."""
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError, PermissionError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _monitor(proc):
    try:
        if proc.stdout:
            for raw in proc.stdout:
                line = raw.rstrip()
                if line:
                    _queue_log(line)
    finally:
        code = proc.wait()
        with _process_lock:
            global _process, _start_time
            if _process is proc:
                _process = None
                _start_time = None
        _queue_state(False, code)


def _start(sender=None, app_data=None, user_data=None):
    global _process, _start_time
    with _process_lock:
        if _process is not None and _process.poll() is None:
            _queue_log("[Warning] A task is already running")
            return

    try:
        command = _build_command()
    except ValueError as exc:
        _queue_log(f"[Input Error] {exc}")
        return

    _events.put((EVENT_CLEAR,))
    _queue_log("[CMD] " + " ".join(shlex.quote(str(a)) for a in command))

    kw = dict(
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if IS_WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["preexec_fn"] = os.setsid

    try:
        proc = subprocess.Popen(command, **kw)
    except Exception as exc:
        _queue_log(f"[Start Failed] {exc}")
        return

    with _process_lock:
        _process = proc
        _start_time = time.monotonic()

    _queue_state(True)
    _queue_log(f"[Running] PID {proc.pid}")
    threading.Thread(target=_monitor, args=(proc,), daemon=True).start()


def _stop(sender=None, app_data=None, user_data=None):
    with _process_lock:
        proc = _process
    if proc is None or proc.poll() is not None:
        _queue_log("[Info] No running process")
        return
    _queue_log(f"[Stop] Terminating PID {proc.pid}")
    _kill_tree(proc)


def _on_exit():
    with _process_lock:
        proc = _process
    if proc is None or proc.poll() is not None:
        return
    _kill_tree(proc)
    try:
        proc.wait(timeout=3.0)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Layout callbacks
# ---------------------------------------------------------------------------


def _update_method_items():
    mode = dpg.get_value(TAG_MODE)
    items = DITHERING_METHODS if mode == "dithering" else IMAGE_METHODS
    dpg.configure_item(TAG_METHOD, items=items)
    if dpg.get_value(TAG_METHOD) not in items:
        dpg.set_value(TAG_METHOD, "g")


def _refresh_layout(sender=None, app_data=None, user_data=None):
    mode = dpg.get_value(TAG_MODE)
    src = dpg.get_value(TAG_SOURCE_TYPE)

    dpg.configure_item(GROUP_DITHERING, show=(mode == "dithering"))
    dpg.configure_item(GROUP_IMAGE, show=(mode == "image"))
    dpg.configure_item(
        GROUP_DITHERING_CAP, show=(mode == "dithering" and src == "capture")
    )
    dpg.configure_item(
        GROUP_DITHERING_FILE, show=(mode == "dithering" and src == "file")
    )
    _update_method_items()
    dpg.configure_item(GROUP_FILL_VALUE, show=(dpg.get_value(TAG_METHOD) == "m"))


def _on_method_change(sender=None, app_data=None, user_data=None):
    dpg.configure_item(GROUP_FILL_VALUE, show=(app_data == "m"))


def _file_dialog_cb(sender, app_data, user_data):
    path = app_data.get("file_path_name")
    if path:
        dpg.set_value(user_data, path)


def _show_dialog(sender, app_data, user_data):
    dpg.show_item(user_data)


def _poll_events():
    while True:
        try:
            event = _events.get_nowait()
        except queue.Empty:
            break

        kind = event[0]
        if kind == EVENT_LOG:
            _append_log(event[1])
        elif kind == EVENT_CLEAR:
            dpg.set_value(TAG_LOG, "")
        elif kind == EVENT_STATE:
            _, running, code = event
            dpg.configure_item(TAG_START, enabled=not running)
            dpg.configure_item(TAG_STOP, enabled=running)
            if running:
                dpg.set_value(TAG_STATUS, "Status: Running")
            else:
                dpg.set_value(TAG_STATUS, f"Status: Stopped (exit={code})")
                _append_log(f"[Finished] Exit code: {code}")

    with _process_lock:
        st = _start_time
    if st is not None:
        elapsed = int(time.monotonic() - st)
        m, s = divmod(elapsed, 60)
        h, m = divmod(m, 60)
        fmt = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        dpg.set_value(TAG_ELAPSED, f"Elapsed: {fmt}")
    else:
        dpg.set_value(TAG_ELAPSED, "")


# ---------------------------------------------------------------------------
# UI construction
# ---------------------------------------------------------------------------


def _tooltip(parent, text):
    with dpg.tooltip(parent):
        dpg.add_text(text)


def _file_dialog(tag, target_tag, ext_filter=None, directory=False):
    with dpg.file_dialog(
        tag=tag,
        show=False,
        callback=_file_dialog_cb,
        user_data=target_tag,
        directory_selector=directory,
        width=_s(800),
        height=_s(500),
    ):
        if ext_filter:
            dpg.add_file_extension(ext_filter, color=(0, 255, 0))
        dpg.add_file_extension(".*")


def _build_ui():
    with dpg.window(tag="main_window", label="VideoDiff GUI"):
        dpg.add_text("VideoDiff", color=(120, 175, 240))
        dpg.add_text("Video frame difference analysis tool", color=(150, 150, 150))
        dpg.add_separator()
        dpg.add_spacer(height=4)

        # Mode
        with dpg.group(horizontal=True):
            t = dpg.add_text("Mode:")
            _tooltip(
                t,
                "dithering: compare consecutive video frames\n"
                "image: compare two static images",
            )
            dpg.add_radio_button(
                items=["dithering", "image"],
                default_value="dithering",
                tag=TAG_MODE,
                callback=_refresh_layout,
                horizontal=True,
            )

        # Method
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=DITHERING_METHODS,
                default_value="g",
                tag=TAG_METHOD,
                label="Method",
                width=_s(100),
                callback=_on_method_change,
            )
            t = dpg.add_text("(?)", color=(150, 150, 150))
            _tooltip(
                t,
                "r = Red channel only\n"
                "g = Green channel only\n"
                "b = Blue channel only\n"
                "a = Absolute RGB difference\n"
                "m = Binary mask (fill changed pixels)\n"
                "n = Normal (dithering mode only)",
            )

        # Fill value (only visible when method == m)
        with dpg.group(tag=GROUP_FILL_VALUE, show=False):
            t = dpg.add_input_int(
                label="Fill Value",
                default_value=255,
                min_value=0,
                max_value=255,
                min_clamped=True,
                max_clamped=True,
                tag=TAG_FILL_VALUE,
                width=_s(120),
                step=0,
            )
            _tooltip(
                t,
                "Fill value (0-255) for the Mask method.\n"
                "Changed pixels are set to this value.",
            )

        # Options
        dpg.add_spacer(height=2)
        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="Display", default_value=True, tag=TAG_DISPLAY)
            t = dpg.add_checkbox(
                label="Pause at Start", default_value=False, tag=TAG_PAUSE
            )
            _tooltip(
                t, "Start in frame-by-frame mode.\n" "Press 'p' to step, 'c' to resume."
            )

        with dpg.group(horizontal=True):
            dpg.add_input_text(
                label="Output Dir", default_value="", width=_s(580), tag=TAG_OUTPUT_DIR
            )
            dpg.add_button(
                label="Browse##out",
                callback=_show_dialog,
                user_data="dialog_output_dir",
            )

        dpg.add_separator()
        dpg.add_spacer(height=4)

        # Dithering source
        with dpg.group(tag=GROUP_DITHERING):
            dpg.add_text("Video Source", color=(120, 175, 240))
            with dpg.group(horizontal=True):
                dpg.add_text("Source:")
                dpg.add_radio_button(
                    items=["capture", "file"],
                    default_value="capture",
                    tag=TAG_SOURCE_TYPE,
                    callback=_refresh_layout,
                    horizontal=True,
                )

            with dpg.group(tag=GROUP_DITHERING_CAP):
                dpg.add_input_int(
                    label="Capture Index",
                    default_value=0,
                    min_value=0,
                    min_clamped=True,
                    width=_s(120),
                    tag=TAG_CAP_INDEX,
                    step=0,
                )
                dpg.add_combo(
                    label="Backend",
                    items=BACKENDS,
                    default_value="any",
                    tag=TAG_BACKEND,
                    width=_s(180),
                )
                dpg.add_input_text(
                    label="FOURCC",
                    default_value="",
                    width=_s(160),
                    tag=TAG_FOURCC,
                    hint="e.g. MJPG, YUY2, H264",
                )
                dpg.add_input_int(
                    label="Width",
                    default_value=0,
                    width=_s(120),
                    tag=TAG_WIDTH,
                    min_value=0,
                    min_clamped=True,
                    step=0,
                )
                dpg.add_input_int(
                    label="Height",
                    default_value=0,
                    width=_s(120),
                    tag=TAG_HEIGHT,
                    min_value=0,
                    min_clamped=True,
                    step=0,
                )
                dpg.add_input_float(
                    label="FPS",
                    default_value=0.0,
                    width=_s(120),
                    tag=TAG_FPS,
                    min_value=0.0,
                    min_clamped=True,
                    format="%.1f",
                    step=0,
                )

            with dpg.group(tag=GROUP_DITHERING_FILE):
                with dpg.group(horizontal=True):
                    dpg.add_input_text(
                        label="Video File",
                        default_value="",
                        width=_s(580),
                        tag=TAG_VIDEO_FILE,
                    )
                    dpg.add_button(
                        label="Browse##vid",
                        callback=_show_dialog,
                        user_data="dialog_video_file",
                    )

        # Image source
        with dpg.group(tag=GROUP_IMAGE):
            dpg.add_text("Image Comparison", color=(120, 175, 240))
            for label, tag, dlg in [
                ("Image A", TAG_IMAGE_FILE_A, "dialog_image_a"),
                ("Image B", TAG_IMAGE_FILE_B, "dialog_image_b"),
            ]:
                with dpg.group(horizontal=True):
                    dpg.add_input_text(
                        label=label, default_value="", width=_s(580), tag=tag
                    )
                    dpg.add_button(
                        label=f"Browse##{tag}", callback=_show_dialog, user_data=dlg
                    )

        dpg.add_separator()
        dpg.add_spacer(height=4)

        # Controls
        with dpg.group(horizontal=True):
            dpg.add_button(label="  Start  ", tag=TAG_START, callback=_start)
            dpg.add_button(
                label="  Stop  ", tag=TAG_STOP, callback=_stop, enabled=False
            )
            dpg.add_button(label="Clear Log", callback=_clear_log)
            dpg.add_spacer(width=20)
            dpg.add_text("Status: Idle", tag=TAG_STATUS)
            dpg.add_spacer(width=10)
            dpg.add_text("", tag=TAG_ELAPSED, color=(150, 150, 150))

        dpg.add_spacer(height=4)

        with dpg.collapsing_header(
            label="Keyboard Shortcuts (in OpenCV window)",
            default_open=False,
        ):
            dpg.add_text(
                "r/g/b  - Red / Green / Blue channel\n"
                "a      - Absolute subtraction\n"
                "m      - Mask mode\n"
                "n      - Normal playback (dithering only)\n"
                "p      - Frame-by-frame mode\n"
                "c      - Continue playback\n"
                "i      - Invert image pair (image mode)\n"
                "1 / 2  - Show Image A / B only (image mode)\n"
                "q      - Quit",
                color=(180, 180, 180),
            )

        dpg.add_spacer(height=4)
        dpg.add_input_text(
            tag=TAG_LOG, multiline=True, readonly=True, width=-1, height=_s(200)
        )

    # File dialogs
    _file_dialog("dialog_video_file", TAG_VIDEO_FILE, VIDEO_FILE_FILTER)
    _file_dialog("dialog_image_a", TAG_IMAGE_FILE_A, IMAGE_FILE_FILTER)
    _file_dialog("dialog_image_b", TAG_IMAGE_FILE_B, IMAGE_FILE_FILTER)
    _file_dialog("dialog_output_dir", TAG_OUTPUT_DIR, directory=True)

    dpg.set_primary_window("main_window", True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    if not CLI_SCRIPT.exists():
        print(f"Cannot find CLI script: {CLI_SCRIPT}")
        raise SystemExit(1)

    _setup_dpi_awareness()
    dpg.create_context()
    _load_cjk_font()
    _build_ui()
    dpg.bind_theme(_create_theme())
    dpg.create_viewport(title="VideoDiff", width=_s(960), height=_s(640))
    dpg.setup_dearpygui()
    dpg.set_exit_callback(_on_exit)
    _refresh_layout()
    dpg.show_viewport()

    while dpg.is_dearpygui_running():
        _poll_events()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    main()
