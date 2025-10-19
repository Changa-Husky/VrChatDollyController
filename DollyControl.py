#!/usr/bin/env python3
# DollyControlPyQt.py
# -*- coding: utf-8 -*-
import sys
import json
import math
import time
import threading
import os
import shutil
import copy
import numpy as np
from pythonosc.udp_client import SimpleUDPClient
from pythonosc.dispatcher import Dispatcher
from pythonosc import osc_server
from scipy.spatial.transform import Rotation as R
import ctypes
from ctypes import wintypes
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QLineEdit, QSlider, QCheckBox, QFileDialog,
                             QScrollArea, QButtonGroup, QMessageBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QGuiApplication, QIcon, QPixmap
from PyQt6.QtWidgets import QDialog, QLabel, QProgressBar, QTextEdit
from PyQt6.QtCore import QTimer, QUrl, QObject, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
import base64


# Thread-safety for camera pose updates from OSC
pose_lock = threading.Lock()

# ----------------------------------------------------
#  DollyControl V2.61 Changa Husky
#  
#  There are bugs I'm sure this was made for my own
#  Filmmaking use but if others find it useful cool.
# ----------------------------------------------------

# --------------------------
# Helper: Compute Unity LookRotation as Euler Angles
# --------------------------
def compute_look_at_unity(camera_pos, target_pos, vertical_mode=False):
    forward = target_pos - camera_pos
    norm_fwd = np.linalg.norm(forward)
    if norm_fwd < 1e-6:
        return [0, 0, 0]
    forward /= norm_fwd
    world_up = np.array([0, 1, 0])
    if abs(np.dot(forward, world_up)) > 0.99:
        fallback_up = np.array([0, 0, 1])
        if abs(np.dot(forward, fallback_up)) > 0.99:
            fallback_up = np.array([1, 0, 0])
        up_vec = fallback_up
    else:
        up_vec = world_up
    right = np.cross(up_vec, forward)
    norm_r = np.linalg.norm(right)
    if norm_r < 1e-6:
        norm_r = 1
    right /= norm_r
    up_corrected = np.cross(forward, right)
    rot_matrix = np.column_stack((right, up_corrected, forward))
    rot = R.from_matrix(rot_matrix)
    euler = rot.as_euler('YXZ', degrees=True)
    if vertical_mode:
        vertical_adjust = R.from_euler('Z', 90, degrees=True)
        final_rot = rot * vertical_adjust
        euler = final_rot.as_euler('YXZ', degrees=True)
    return euler.tolist()

# --------------------------
# Determine Export Path from Documents
# --------------------------
def get_documents_folder():
    CSIDL_PERSONAL = 5
    SHGFP_TYPE_CURRENT = 0
    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
    return buf.value

documents_folder = get_documents_folder()
EXPORT_PATH = os.path.join(documents_folder, "VRChat", "CameraPaths") + os.sep
USED_LOCATIONS_PATH = os.path.join(EXPORT_PATH, "Used_Locations")
os.makedirs(USED_LOCATIONS_PATH, exist_ok=True)

PINS_PATH = os.path.join(EXPORT_PATH, "Bookmarks")
os.makedirs(PINS_PATH, exist_ok=True)

# --------------------------
# OSC Settings
# --------------------------
OSC_IP = "127.0.0.1"
OSC_PORT = 9000
OSC_PORT_RECEIVE = 9001
client = SimpleUDPClient(OSC_IP, OSC_PORT)

# --------------------------
# Dolly Settings & Globals
# --------------------------
MAX_RADIUS = 10.0

dolly_settings = {
    "radius": 2.0,
    "points": 12,
    "duration": 2.0
}
dolly_zoom = 45.0
dolly_speed = 3.0
aperture = 15.0
focal_distance = 2
dolly_zoom_exaggeration = 2.0   # Range: 1.0 to 5.0
user_points_limit = 15          # Range: 5 to 50

# --- Dolly Mode Constants (single source of truth) ---
MODE_CIRCLE = 1
MODE_ARC = 2
MODE_LINE = 3
MODE_ELLIPSE = 4
MODE_FILE = 5
MODE_DOLLY_ZOOM = 6
# -----------------------------------------------------
dolly_mode = MODE_CIRCLE  # 1=Circle, 2=Arc, 3=Line, 4=Ellipse, 5=File, 6=Dolly Zoom

start_position = {"X": 0.0, "Y": 0.0, "Z": 0.0}
exported_center = None
current_path_data = None
dolly_vertical = False
dolly_pause = False
PAUSE_DURATION_DEFAULT = 60.0
lookat_x_offset = 0.0
lookat_y_offset = 0.0
view_target = None
use_view_target = True
arc_angle = 180.0

# These will be set by the UI:
use_view_target_checkbox = None
camera_offset = {"X": 0.0, "Y": 0.0, "Z": 0.0}
camera_rotation_offset = R.from_euler('XYZ', [0, 0, 0], degrees=True)
initial_dolly_distance = None
initial_dolly_zoom = None
reverse_dolly_zoom = False
loaded_path_data_original = []
loaded_file_label = None
dolly_zoom_btn = None

# Global UI widget references (set by the PyQt UI)
radius_slider = None
radius_entry = None
duration_slider = None
duration_entry = None
zoom_slider = None
zoom_entry = None
speed_slider = None
speed_entry = None
aperture_slider = None
aperture_entry = None
focal_distance_slider = None
focal_distance_entry = None
dz_exag_slider = None
dz_exag_entry = None
points_count_slider = None
points_count_entry = None
lookat_x_slider = None
lookat_x_entry = None
lookat_y_slider = None
lookat_y_entry = None
translation_step_slider = None
translation_step_entry = None
rotation_step_slider = None
rotation_step_entry = None
vertical_toggle = None
pause_toggle = None
reverse_zoom_checkbox = None

translation_step_value = 0.5
rotation_step_value = 1.0

# Global flag to disable export processing during target move.
target_move_mode = False

# Latest camera pose (world space) from VRChat OSC
current_camera_pos = {"X": 0.0, "Y": 0.0, "Z": 0.0}
current_camera_rot = {"X": 0.0, "Y": 0.0, "Z": 0.0}  # Euler, degrees
last_pose_timestamp = 0.0

# Thread-safe bridge so OSC thread can "click" UI buttons
class ActionBus(QObject):
    setTargetFromCam = pyqtSignal()
    setPathFromCam = pyqtSignal()
    # NEW: generic nudges (thread-safe to UI)
    nudgeTranslate   = pyqtSignal(str, int)   # axis: "X"/"Y"/"Z", dir: +1/-1
    nudgeRotate      = pyqtSignal(str, int)   # axis: "X"/"Y"/"Z", dir: +1/-1
BUS = ActionBus()

# Rising-edge memory so a held toggle doesn’t spam
_AVATAR_TOGGLE_PREV = {
    "SetTargetFromCam": 0.0,
    "SetPathFromCam": 0.0,

    # NEW: translation nudges
    "SetDolly_T+X": 0.0, "SetDolly_T+Y": 0.0, "SetDolly_T+Z": 0.0,
    "SetDolly_T-X": 0.0, "SetDolly_T-Y": 0.0, "SetDolly_T-Z": 0.0,

    # NEW: rotation nudges
    "SetDolly_R+X": 0.0, "SetDolly_R+Y": 0.0, "SetDolly_R+Z": 0.0,
    "SetDolly_R-X": 0.0, "SetDolly_R-Y": 0.0, "SetDolly_R-Z": 0.0,    
}

def _reindex_waypoints(path):
    for i, wp in enumerate(path):
        wp["Index"] = i

def add_pause_at_end(path, duration=None):
    """
    Insert a single pause waypoint at the end that keeps the camera fixed
    for `duration` seconds.
    """
    if not path:
        return path

    pause_len = (
        float(dolly_settings.get("pause_duration", PAUSE_DURATION_DEFAULT))
        if duration is None else float(duration)
    )

    base = copy.deepcopy(path[-1])

    # keep same transform & schema; only Duration changes
    base["Duration"] = round(pause_len, 3)

    # Optional: ensure Speed=0 if your player honors per-WP speed
    # (If your system ignores Speed for fixed-position hold, you can skip this.)
    base["Speed"] = float(0.0)

    path.append(base)
    _reindex_waypoints(path)
    return path

def add_pause_pair_at_end(path, duration=None):
    if not path:
        return path

    pause_len = (
        float(dolly_settings.get("pause_duration", PAUSE_DURATION_DEFAULT))
        if duration is None else float(duration)
    )

    last = copy.deepcopy(path[-1])
    hold1 = copy.deepcopy(last)
    hold2 = copy.deepcopy(last)

    hold1["Duration"] = round(pause_len, 3)
    hold1["Speed"] = float(0.0)

    # hold2 can be zero duration (acts as a resume marker), or same as hold1
    hold2["Duration"] = float(0.0)
    hold2["Speed"] = float(0.0)

    path.extend([hold1, hold2])
    _reindex_waypoints(path)
    return path

def update_arc_angle_slider(value):
    """
    Update the global arc_angle when the slider value changes.
    """
    global arc_angle, arc_angle_entry
    arc_angle = round(float(value), 2)
    arc_angle_entry.setText(str(arc_angle))
    regenerate_path()

def on_arc_angle_entry_return():
    """
    Update the global arc_angle when the text entry is modified.
    """
    global arc_angle, arc_angle_entry
    try:
        val = float(arc_angle_entry.text())
        # Clamp between 5 and 180.
        val = max(5, min(180, val))
        arc_angle = val
        regenerate_path()
    except ValueError:
        pass

def soft_beep():
    """
    Very subtle notification beep for invalid actions
    """
    try:
        import winsound
        winsound.Beep(600, 90)  # <--- softer than Play tone
    except Exception:
        pass

def get_desktop_folder():
    CSIDL_DESKTOP = 0            # Desktop folder constant
    SHGFP_TYPE_CURRENT = 0
    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOP, None, SHGFP_TYPE_CURRENT, buf)
    return buf.value

DESKTOP_PATH = get_desktop_folder()
PERFORM_MP3_PATH = os.path.join(DESKTOP_PATH, "perform.mp3")
print("Using MP3 file at:", PERFORM_MP3_PATH)

is_local = False  # Global flag to set the islocal property on waypoints.

reverse_path = False  # Global flag to reverse the generated path.

initial_import = True

ICON_BASE64 = b"""
AAABAAEAICAAAAEAIACoEAAAFgAAACgAAAAgAAAAQAAAAAEAIAAAAAAAABAAACUWAAAlFgAAAAAA
AAAAAADm5uYAr6+vLdra2qrd3Nrx19fT/9fX0/7X19P+19fT/tfX0/7X19P+19fT/tfX0/7X19P+
19fT/tfX0/7X19P+19fT/tfX0/7X19P+19fT/tfX0/7X19P+19fT/tfX0/7X19P+19fT/tjY1f7Y
2NX+29vZ9dnZ2au6uroo////AKqpqibc3NrDsLCj/25tUf9fXz//X18//19fP/9fXz//Xl8+/15f
Pv9eXz7/Xl8+/15fPv9eXz7/Xl8+/15fPv9eXz7/Xl8+/15fPv9eXz7/Xl8+/15fPv9eXz7/Xl8+
/19fP/9fX0D/YmJF/2JiRf9vblP/sbGk/9zc28Gnp6gm1NTUlbe3qv9JSST/PT4W/z4/F/8+Pxf/
Pj8X/z4/F/8+Pxf/Pj8X/z4/F/8+Pxf/Pj8X/z4/F/8+Pxf/Pj8X/z4/F/8+Pxf/Pj8W/z0+Ff8+
Pxf/Pj8X/z4/F/8+Pxf/Pj8X/z4/F/8+Pxf/Pj8X/z0+Fv9KSST/uLes/9PS05Xc3NvXf39m/z0+
Ff9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/z9AGP8+Pxf/QEEZ/z9AGP8+Pxf/QEEY
/z9AGP9HSCH/VVUz/0FCGv9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/z0+Ff+AgGf/
2NjY2tjY1+hzclb/Pj4W/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/U1Qx/1hZOP9E
RR7/R0gi/1hZN/9HSCL/S0sm/1BQLf9/f2f/WFk3/z9AF/9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BB
Gf9AQRn/PT4W/3R0WP/Y2Nbo2NjX53NzV/8+Phb/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ
/0NEHf+IiXP/e3xk/3p7Yv95emH/fH1k/31+Zv98fWX/cHFV/4OEbf99fmb/Pj8X/0BBGf9AQRn/
QEEZ/0BBGf9AQRn/QEEZ/0BBGf89Phb/dHRZ/9jY1+fY2NfncXJW/z0+Fv9AQRn/QEEZ/0BBGf9A
QRn/QEEZ/0BBGf9AQRn/REUf/4CBaf9HSCH/h4dx/4uMd/9qa07/iYl1/4GCa/+FhW//gIFp/4CB
af9JSiX/QEEY/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/z0+Fv9zc1j/2NjX59jY1+dxclb/PT4W
/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGP9ERR7/i4x4/3R1Wv99fmX/Wls5/3BxVf9gYUH/
goNr/4GCa/9SUzD/VFUy/0hJI/9AQRj/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/PT4W/3N0WP/Y
2Nfn2NjX53FzVv89Phb/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/RUYf/4CBa/+lppn/k5SD/0tM
J/8+Pxf/PT4V/0ZHIf9cXTz/Vlc1/z9AGP8/QBj/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ
/0BBGf89Phb/c3RY/9jY1+fY2NfncXNW/z0+Fv9AQRn/QEEZ/0BBGf9AQRn/QEEZ/z9AGP9RUi//
kJF//1VWNP+jpJX/i4x5/4WFcf9YWDj/P0AX/z9AF/8/QBj/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9A
QRn/QEEZ/0BBGf9AQRn/QEEZ/z0+Fv9zdFj/2NjX59jY1+dxc1b/PT4W/0BBGf9AQRn/QEEZ/0BB
Gf9AQRn/QEEY/0hJJP+Oj33/jo99/4aHcv9PUCz/YmNF/5GRgP9MTSn/P0AY/0BBGf9AQRn/QEEZ
/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/PT4W/3N0WP/Y2Nfn2NjX53FzVv89Phb/
QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0lKJf9UVTT/RUYh/z9AGP88PRX/eXpi/2BhQv8+
Pxb/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf89Phb/c3RY/9jY
1+fY2NfncXNW/z0+Fv9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/P0AY/z9AF/9AQRn/QEEZ
/0JDHP+JinX/eHlg/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/
QEEZ/z0+Fv9zdFj/2NjX59jY1+dxc1b/PT4W/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9A
QRn/QEEZ/0BBGf8+Pxf/cnNa/4iJdf+Oj33/Y2RH/z4/F/9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BB
Gf9AQRn/QEEZ/0BBGf9AQRn/PT4W/3N0WP/Y2Nfn2NjX53FzVv89Phb/QEEZ/0BBGf9AQRn/QEEZ
/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/z9AF/+Bgm3/a2xR/3t7ZP9ycln/Pj8W/0BBGf9AQRn/
QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf89Phb/c3RY/9jY1+fY2NfncXNW/z0+Fv9A
QRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/P0AY/1JTMP+bnI3/j5B9/0lK
Jf8/QBj/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/z0+Fv9zdFj/2NjX
59jY1+dxc1b/PT4W/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/
PT4V/3N0Wv9jZEb/PT4V/0BBGf8/QBj/P0AY/z9AF/9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9A
QRn/PT4W/3N0WP/Y2Nfn2NjX53FzVv89Phb/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BB
Gf9AQRn/QEEZ/0BBGf8+Pxb/Z2dL/4KDbv9CQxz/QUIa/3Z3Xv+iopL/b29V/0BBGf9AQRn/QEEZ
/0BBGf9AQRn/QEEZ/0BBGf89Phb/c3RY/9jY1+fY2NfncXNW/z0+Fv9AQRn/QEEZ/0BBGf9AQRn/
QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9FRh//gIFs/4qLeP+FhW//paWY/9ra1P+0
tKr/QkMd/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/z0+Fv9zdFj/2NjX59jY1+dxc1b/PT4W/0BB
Gf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/z5AF/8+Pxf/QEEZ/0BBGP9CQxv/UlMw
/15eP/+am4v/5eXi/7u7sP9BQhv/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/PT4W/3N0WP/Y2Nfn
2NjX53FzVv89Phb/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf8+Pxf/XF08/2VmR/9A
QRn/QEEZ/0BBGf8/QBj/PT4V/2RlR//u7uz/y8vB/0ZHIf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BB
Gf89Phb/c3RY/9jY1+fY2NfncXNW/z0+Fv9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/Pj8W
/2FiQ//Z2dP/vLyv/0BBGf8+Pxf/Pj8W/z0+Ff9DQxz/m5uI///////d3df/Tk8r/z9AGP9AQRn/
QEEZ/0BBGf9AQRn/QEEZ/z0+Fv9zdFj/2NjX59jY1+dxc1b/PT4W/0BBGf9AQRn/QEEZ/0BBGf9A
QRn/QEEZ/z4/Fv9hYkP/1tbP///////Jyb//Xl8+/11ePf9kZUb/fHxj/7a3qf/29vT//////9vb
1f9OTyr/P0AY/0BBGf9AQRn/QEEZ/0BBGf9AQRn/PT4W/3N0WP/Y2Nfn2NjX53FzVv89Phb/QEEZ
/0BBGf9AQRn/QEEZ/0BBGf8/QBf/YWJC/9bWz/////////////j4+P/r7On/7O3q//Hx8P/7+/r/
////////////////v7+z/0NEHv9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf89Phb/c3RY/9jY1+fY
2NfncXNW/z0+Fv9AQRn/QEEZ/0BBGf9AQRn/P0AY/1JSL//S0sr/////////////////////////
//////////////////////////////Pz8f95eV//Pj8W/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ
/z0+Fv9zdFj/2NjX59jY1+dyc1f/PT4W/0BBGf9AQRn/QEEZ/0BBGf8/QBj/Tk8q/8XFu///////
///////////////////////////////////////////p6eb/jY54/0JDHP9AQRn/QEEZ/0BBGf9A
QRn/QEEZ/0BBGf9AQRn/PT4W/3N0WP/Y2Nfn2dnX53V1XP8+Pxb/QEEZ/0BBGf9AQRn/QEEZ/0BB
Gf8/QBf/Vlc1/8jIv/////////////X18//g4Nv/39/a/9jY0v/Exbr/m5uJ/2FiQ/9AQRn/QEEY
/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf89Phb/dHRZ/9fY1ufZ2dfodnZd/z4/F/9AQRn/
QEEZ/0BBGf9AQRn/QEEZ/0BBGf8+Pxb/V1g2/8rKwf//////xca7/1NUMf9QUS7/TE0o/0RFHv8+
Pxf/Pj8X/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/z0+Fv90dFn/19fW6NnZ
2N1/f2f/PT4V/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf8+Pxb/WFk3/83NxP+3uKr/QEEa
/z9AGP8/QBj/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/QEEZ/0BBGf9AQRn/
PT4V/39/Zv/Z2djc19fXl7S0qP9HSCL/PT4W/z8/F/8/Pxf/Pj8X/z4/F/8+Pxf/Pj8X/z4/F/89
PhX/UVIv/1laOP8+Pxf/Pj8X/z4/F/8+Pxf/Pj8X/z4/F/8+Pxf/Pj8X/z4/F/8+Pxf/Pj8X/z4/
F/8/Pxf/Pz8X/z0+Fv9ISCP/tbWp/tTU1Ziop6gs2NjXzaysnv9paUv/W1s6/1tbOv9aWzr/Wls6
/1pbOv9aWzr/Wls6/1pbOv9ZWjj/WFk4/1pbOv9aWzr/Wls6/1pbOv9aWzr/Wls6/1pbOv9aWzr/
Wls6/1pbOv9aWzr/Wls6/1tbO/9bWzv/aWlM/62tn//Y2NjMqqqrKf///wCsrKw30dHRw9jY1v7Y
2NP+2NjU/tjY1P7Y2NT+2NjU/tjY1P7Y2NT+2NjU/tjY1P7Y2NT+2NjU/tjY1P7Y2NT+2NjU/tjY
1P7Y2NT+2NjU/tjY1P7Y2NT+2NjU/tjY1P7Y2NT+2NjT/tjY0/7Z2df719fXuLCvsDP///8AgAAA
AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAIAAAAE=
"""

# --------------------------
# Callback Functions for Parameters
# --------------------------

def ensure_dolly_zoom_init():
    global initial_dolly_zoom
    if initial_dolly_zoom is None:
        initial_dolly_zoom = dolly_zoom

def set_reverse_path(checked):
    global reverse_path
    reverse_path = checked
    APP_WINDOW.append_status(f"Reverse path set to: {reverse_path}")
    regenerate_path()

def update_points_count_slider(value):
    global user_points_limit, points_count_entry
    val = int(round(value))
    user_points_limit = val
    points_count_entry.setText(str(val))
    regenerate_path()

def on_points_count_entry_return():
    global user_points_limit, points_count_entry
    try:
        val = int(points_count_entry.text())
        val = max(5, min(50, val))
        user_points_limit = val
        regenerate_path()
    except ValueError:
        pass

def set_is_local(checked):
    global is_local
    is_local = checked
    APP_WINDOW.append_status(f"Is local set to: {is_local}")
    regenerate_path()

def update_dz_exaggeration_slider(value):
    global dolly_zoom_exaggeration, dz_exag_entry
    val = round(float(value) / 100, 2)
    dolly_zoom_exaggeration = val
    dz_exag_entry.setText(str(val))
    if dolly_mode == MODE_DOLLY_ZOOM :
        regenerate_path()

def on_dz_exaggeration_entry_return():
    global dolly_zoom_exaggeration, dz_exag_entry
    try:
        val = float(dz_exag_entry.text())
        val = max(1.0, min(5.0, val))
        dolly_zoom_exaggeration = val
        if dolly_mode == MODE_DOLLY_ZOOM :
            regenerate_path()
    except ValueError:
        pass

def update_aperture_slider(value):
    global aperture, aperture_entry
    val = round(float(value) / 100, 2)
    aperture = val
    aperture_entry.setText(str(val))
    regenerate_path()

def on_aperture_entry_return():
    global aperture, aperture_entry
    try:
        val = float(aperture_entry.text())
        val = max(1.4, min(32, val))
        aperture = val
        regenerate_path()
    except ValueError:
        pass

def update_focal_distance_slider(value):
    global focal_distance, focal_distance_entry
    val = round(float(value) / 100, 2)
    focal_distance = val
    focal_distance_entry.setText(str(val))
    regenerate_path()

def on_focal_distance_entry_return():
    global focal_distance, focal_distance_entry
    try:
        val = float(focal_distance_entry.text())
        val = max(0.1, min(30, val))
        focal_distance = val
        regenerate_path()
    except ValueError:
        pass

def update_radius_slider(value):
    global dolly_settings, radius_entry
    val = round(float(value) / 100, 2)
    dolly_settings["radius"] = val
    radius_entry.setText(str(val))
    regenerate_path()

def update_zoom_slider(value):
    global dolly_zoom, zoom_entry
    val = round(float(value), 2)
    dolly_zoom = val
    zoom_entry.setText(str(val))
    if dolly_mode != MODE_DOLLY_ZOOM:
        send_dolly_path()

def update_speed_slider(value):
    global dolly_speed, speed_entry
    val = round(float(value) / 100, 2)
    dolly_speed = val
    speed_entry.setText(str(val))
    send_dolly_path()

def on_translation_step_entry_return():
    global translation_step_value
    try:
        val = float(translation_step_entry.text())
        val = max(0.01, min(5.0, val))
        translation_step_slider.setValue(int(val * 100))
        translation_step_value = val
    except ValueError:
        pass

def update_translation_step_slider(value):
    global translation_step_value, translation_step_entry
    val = round(float(value) / 100, 2)
    translation_step_value = val
    translation_step_entry.setText(str(val))

def on_rotation_step_entry_return():
    global rotation_step_value
    try:
        val = float(rotation_step_entry.text())
        val = max(0.01, min(90.0, val))
        rotation_step_slider.setValue(int(val * 100))
        rotation_step_value = val
    except ValueError:
        pass

def update_rotation_step_slider(value):
    global rotation_step_value, rotation_step_entry
    val = round(float(value) / 100, 2)
    rotation_step_value = val
    rotation_step_entry.setText(str(val))

def _rising_edge(param_name: str, val: float) -> bool:
    prev = _AVATAR_TOGGLE_PREV.get(param_name, 0.0)
    fire = (val >= 0.5) and (prev < 0.5)
    _AVATAR_TOGGLE_PREV[param_name] = val
    return fire

def update_lookat_x_slider(value):
    global lookat_x_offset, lookat_x_entry
    val = round(float(value) / 100, 2)
    lookat_x_offset = val
    lookat_x_entry.setText(str(val))
    send_dolly_path()

def update_lookat_y_slider(value):
    global lookat_y_offset, lookat_y_entry
    val = round(float(value) / 100, 2)
    lookat_y_offset = val
    lookat_y_entry.setText(str(val))
    send_dolly_path()

def update_duration_slider(value):
    global dolly_settings, duration_entry
    val = round(float(value) / 100, 2)
    dolly_settings["duration"] = val
    duration_entry.setText(str(val))
    regenerate_path()

def on_radius_entry_return():
    try:
        val = float(radius_entry.text())
        val = max(0.1, min(10.0, val))
        update_radius_slider(val * 100)
    except ValueError:
        pass

def on_duration_entry_return():
    try:
        val = float(duration_entry.text())
        val = max(0.1, min(30.0, val))
        update_duration_slider(val * 100)
    except ValueError:
        pass

def on_zoom_entry_return():
    global dolly_zoom
    try:
        val = float(zoom_entry.text())
        val = max(20.0, min(300.0, val))
        zoom_slider.setValue(int(val))
        dolly_zoom = val
        if dolly_mode != MODE_DOLLY_ZOOM:
            send_dolly_path()
    except ValueError:
        pass

def on_speed_entry_return():
    global dolly_speed
    try:
        val = float(speed_entry.text())
        val = max(0.1, min(10.0, val))
        speed_slider.setValue(int(val * 100))
        dolly_speed = val
        send_dolly_path()
    except ValueError:
        pass

def on_lookat_x_entry_return():
    global lookat_x_offset
    try:
        val = float(lookat_x_entry.text())
        val = max(-20.0, min(20.0, val))
        lookat_x_slider.setValue(int(val * 100))
        lookat_x_offset = val
        send_dolly_path()
    except ValueError:
        pass

def on_lookat_y_entry_return():
    global lookat_y_offset
    try:
        val = float(lookat_y_entry.text())
        val = max(-20.0, min(20.0, val))
        lookat_y_slider.setValue(int(val * 100))
        lookat_y_offset = val
        send_dolly_path()
    except ValueError:
        pass

def export_pin(pin_number):
    """Export current start position, view target (if set), camera offset, rotation offset, and various settings as a pin."""
    pin_file = os.path.join(PINS_PATH, f"pin{pin_number}.json")
    settings = {
         "radius": dolly_settings["radius"],
         "duration": dolly_settings["duration"],
         "zoom": dolly_zoom,
         "speed": dolly_speed,
         "aperture": aperture,
         "focal_distance": focal_distance,
         "arc_angle": arc_angle,
         "num_points": user_points_limit,
         "translation_step": translation_step_value,
         "rotation_step": rotation_step_value
    }
    # Convert the current rotation offset to Euler angles (XYZ, degrees)
    rotation_offset_euler = camera_rotation_offset.as_euler('XYZ', degrees=True).tolist()

    data = {
         "origin": start_position,
         "target": view_target,  # May be None if no target is set.
         "camera_offset": camera_offset,  # Save the current translation offset.
         "rotation_offset": rotation_offset_euler,  # Save the rotation offset as Euler angles.
         "settings": settings
    }
    try:
        with open(pin_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        QMessageBox.information(None, "Pin Export", f"Pin {pin_number} updated with current origin, target, offsets, and settings.")
    except Exception as e:
        QMessageBox.critical(None, "Pin Export Error", f"Error exporting Pin {pin_number}: {e}")


def load_pin(pin_number):
    """
    Load the stored pin and update start position, target, camera offset, rotation offset, and various settings.
    Then regenerate the path so that these values take effect.
    """
    global start_position, exported_center, view_target, use_view_target
    global dolly_settings, dolly_zoom, dolly_speed, aperture, focal_distance, arc_angle, user_points_limit
    global translation_step_value, rotation_step_value, camera_offset, camera_rotation_offset

    pin_file = os.path.join(PINS_PATH, f"pin{pin_number}.json")
    if not os.path.exists(pin_file):
        QMessageBox.warning(None, "Pin Empty", f"Pin {pin_number} is empty.")
        return
    try:
        with open(pin_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "origin" in data:
            start_position.clear()
            start_position.update(data["origin"])
            exported_center = copy.deepcopy(start_position)
        if "target" in data:
            view_target = data["target"]
            use_view_target = (view_target is not None)
        if "camera_offset" in data:
            camera_offset.clear()
            camera_offset.update(data["camera_offset"])
        if "rotation_offset" in data:
            # Recreate the camera_rotation_offset from saved Euler angles.
            euler_angles = data["rotation_offset"]
            camera_rotation_offset = R.from_euler('XYZ', euler_angles, degrees=True)
        if "settings" in data:
            settings = data["settings"]
            dolly_settings["radius"] = settings.get("radius", dolly_settings["radius"])
            dolly_settings["duration"] = settings.get("duration", dolly_settings["duration"])
            dolly_zoom = settings.get("zoom", dolly_zoom)
            dolly_speed = settings.get("speed", dolly_speed)
            aperture = settings.get("aperture", aperture)
            focal_distance = settings.get("focal_distance", focal_distance)
            arc_angle = settings.get("arc_angle", arc_angle)
            user_points_limit = settings.get("num_points", user_points_limit)
            translation_step_value = settings.get("translation_step", translation_step_value)
            rotation_step_value = settings.get("rotation_step", rotation_step_value)
        APP_WINDOW.append_status(f"Loaded Pin {pin_number}:\n  Origin: {start_position}\n  Target: {view_target}\n  Camera Offset: {camera_offset}\n  Rotation Offset (Euler): {data.get('rotation_offset')}\n  Settings: {data.get('settings', {})}")
        regenerate_path()
    except Exception as e:
        QMessageBox.critical(None, "Pin Load Error", f"Error loading Pin {pin_number}: {e}")

# --------------------------
# Dolly Path Generation Functions
# --------------------------
def generate_circle_path():
    center = exported_center if exported_center is not None else start_position
    dolly_settings["points"] = user_points_limit  
    waypoints = []
    n_pts = user_points_limit
    per_wp_duration = float(dolly_settings.get('duration', 2.0)) / max(1, int(user_points_limit) - 1)
    for i in range(user_points_limit):
        angle = (i / user_points_limit) * 2 * math.pi
        x = round(center["X"] + dolly_settings["radius"] * math.cos(angle), 3)
        z = round(center["Z"] + dolly_settings["radius"] * math.sin(angle), 3)
        y = round(center["Y"], 3)
        yaw = round(math.degrees(math.atan2(center["Z"] - z, center["X"] - x)), 2)
        wp = {
            "Index": i,
            "PathIndex": 0,
            "FocalDistance": focal_distance,
            "Aperture": aperture,
            "Hue": 120.0,
            "Saturation": 100.0,
            "Lightness": 50.0,
            "LookAtMeXOffset": 0.0,
            "LookAtMeYOffset": 0.0,
            "Zoom": dolly_zoom,
            "Speed": dolly_speed,
            "Duration": round((i / user_points_limit) * dolly_settings["duration"], 3),
            "Position": {"X": x, "Y": y, "Z": z},
            "Rotation": {"X": 0, "Y": yaw, "Z": 0},
            "islocal": is_local
        }
        waypoints.append(wp)
    return waypoints

def generate_arc_path(
    arc_degrees: float,
    radius: float,
    clockwise: bool = False,
    path_index: int = 0,
    hue: float = 120.0,
    saturation: float = 100.0,
    lightness: float = 50.0,
    look_at_center: bool = True,   # if False, face tangent along path
    eps: float = 1e-6,):
    """
    Build a path along an arc centered on `view_target` if set, otherwise
    around the current camera position as a fallback center.

    Waypoint schema matches other generators:
      Index, PathIndex, FocalDistance, Aperture, Hue/Saturation/Lightness,
      LookAtMeXOffset/LookAtMeYOffset, Zoom, Speed, Duration,
      Position{X,Y,Z}, Rotation{X,Y,Z}, islocal

    Rotation rule:
      - if look_at_center: yaw faces the center of the arc (classic orbit)
      - else: yaw faces tangent direction (forward along motion)

    Segment count is derived from arc span: ~1 waypoint per 5 degrees,
    clamped to [2, 180] to avoid under/over-sampling.
    """
    import math

    # ----- Centers & starting angle -----
    if view_target is not None:
        cx = float(view_target["X"])
        cy = float(view_target["Y"])
        cz = float(view_target["Z"])
    else:
        # Fallback to camera as the center if no target set
        cx = float(current_camera_pos.get("X", 0.0))
        cy = float(current_camera_pos.get("Y", 0.0))
        cz = float(current_camera_pos.get("Z", 0.0))

    # Compute start angle from center -> camera vector in world XZ plane
    vx = float(current_camera_pos.get("X", 0.0)) - cx
    vz = float(current_camera_pos.get("Z", 0.0)) - cz
    # Avoid NaN/zero vector (spawn at origin etc.)
    if abs(vx) <= eps and abs(vz) <= eps:
        # default start facing +Z from center
        start_deg = 0.0
    else:
        start_deg = math.degrees(math.atan2(vx, vz))  # yaw convention: atan2(X, Z)

    # Normalize inputs
    span = float(arc_degrees)
    span = max(0.0, min(360.0, span))
    if span == 0.0:
        span = 0.001  # avoid div-by-zero; degenerate tiny arc

    # Direction
    step_sign = -1.0 if clockwise else 1.0

    # Segments: ~every 5 degrees; clamp [2, 180]
    segs = max(2, min(180, int(round(max(2.0, span / 5.0)))))

    # Total duration from settings
    total_duration = float(dolly_settings.get("duration", 5.0))
    per_wp_duration = total_duration / max(1, segs - 1)

    # Precompute fixed fields
    fd = float(focal_distance)
    ap = float(aperture)
    zf = float(dolly_zoom)
    spd = float(dolly_speed)
    local_flag = bool(is_local)

    waypoints = []
    for i in range(segs):
        t = i / (segs - 1) if segs > 1 else 0.0
        ang_deg = start_deg + step_sign * (t * span)
        ang_rad = math.radians(ang_deg)

        # Position on arc in XZ plane, preserve center Y (or raise/lower here if needed)
        x = cx + radius * math.sin(ang_rad)
        z = cz + radius * math.cos(ang_rad)
        y = cy  # keep level with center; adjust if you have a height slider

        # Yaw:
        if look_at_center:
            # Face towards center (orbit): bearing from point to center
            yaw_deg = math.degrees(math.atan2(cx - x, cz - z))
        else:
            # Face tangent along path (forward):
            # derivative of circle param: tangent is 90° ahead in direction of travel
            yaw_deg = ang_deg + ( -90.0 if clockwise else 90.0 )

        wp = {
            "Index": i,
            "PathIndex": path_index,
            "FocalDistance": fd,
            "Aperture": ap,
            "Hue": hue,
            "Saturation": saturation,
            "Lightness": lightness,
            "LookAtMeXOffset": 0.0,
            "LookAtMeYOffset": 0.0,
            "Zoom": zf,
            "Speed": spd,
            "Duration": round(per_wp_duration, 3),
            "Position": {"X": round(x, 3), "Y": round(y, 3), "Z": round(z, 3)},
            "Rotation": {"X": 0.0, "Y": round(yaw_deg, 2), "Z": 0.0},
            "islocal": local_flag,
        }
        waypoints.append(wp)

    return waypoints


def generate_line_path():
    dolly_settings["points"] = user_points_limit
    waypoints = []
    n_pts = user_points_limit
    per_wp_duration = float(dolly_settings.get('duration', 2.0)) / max(1, int(user_points_limit) - 1)
    startX = start_position["X"] - dolly_settings["radius"]
    endX = start_position["X"] + dolly_settings["radius"]
    for i in range(user_points_limit):
        t = i / (user_points_limit - 1) if user_points_limit > 1 else 0
        x = round(startX + t * (endX - startX), 3)
        y = round(start_position["Y"], 3)
        z = start_position["Z"]
        wp = {
            "Index": i,
            "PathIndex": 0,
            "FocalDistance": focal_distance,
            "Aperture": aperture,
            "Hue": 120.0,
            "Saturation": 100.0,
            "Lightness": 50.0,
            "LookAtMeXOffset": 0.0,
            "LookAtMeYOffset": 0.0,
            "Zoom": dolly_zoom,
            "Speed": dolly_speed,
            "Duration": round(per_wp_duration, 3),
            "Position": {"X": x, "Y": y, "Z": z},
            "Rotation": {"X": 0, "Y": 0, "Z": 0},
            "islocal": is_local
        }
        waypoints.append(wp)
    return waypoints

def generate_elliptical_path():
    dolly_settings["points"] = user_points_limit
    waypoints = []
    n_pts = user_points_limit
    per_wp_duration = float(dolly_settings.get('duration', 2.0)) / max(1, int(user_points_limit) - 1)
    elliptical_ratio = 0.75
    for i in range(user_points_limit):
        angle = (i / user_points_limit) * 2 * math.pi
        x = round(start_position["X"] + dolly_settings["radius"] * math.cos(angle), 3)
        z = round(start_position["Z"] + (dolly_settings["radius"] * elliptical_ratio) * math.sin(angle), 3)
        y = round(start_position["Y"], 3)
        wp = {
            "Index": i,
            "PathIndex": 0,
            "FocalDistance": focal_distance,
            "Aperture": aperture,
            "Hue": 120.0,
            "Saturation": 100.0,
            "Lightness": 50.0,
            "LookAtMeXOffset": 0.0,
            "LookAtMeYOffset": 0.0,
            "Zoom": dolly_zoom,
            "Speed": dolly_speed,
            "Duration": round((i / user_points_limit) * dolly_settings["duration"], 3),
            "Position": {"X": x, "Y": y, "Z": z},
            "Rotation": {"X": 0, "Y": 0, "Z": 0}
        }
        waypoints.append(wp)
    return waypoints

def generate_loaded_path():
    if not loaded_path_data_original:
        APP_WINDOW.append_status("No custom path loaded. Returning empty path.")
        return []
    # For file/slot modes, ignore the radius scaling and use a fixed scale factor.
    scale_factor = 1
    xs = [pt["Position"]["X"] for pt in loaded_path_data_original]
    ys = [pt["Position"]["Y"] for pt in loaded_path_data_original]
    zs = [pt["Position"]["Z"] for pt in loaded_path_data_original]
    cx, cy, cz = sum(xs)/len(xs), sum(ys)/len(ys), sum(zs)/len(zs)
    new_waypoints = []
    for original in loaded_path_data_original:
        wp = copy.deepcopy(original)
        rx = wp["Position"]["X"] - cx
        ry = wp["Position"]["Y"] - cy
        rz = wp["Position"]["Z"] - cz
        new_x = cx + rx * scale_factor
        new_y = cy + ry * scale_factor
        new_z = cz + rz * scale_factor
        wp["Position"] = {"X": round(new_x, 3), "Y": round(new_y, 3), "Z": round(new_z, 3)}
        wp["Zoom"] = dolly_zoom
        wp["Speed"] = dolly_speed
        wp["Aperture"] = aperture
        wp["FocalDistance"] = focal_distance
        new_waypoints.append(wp)
    return new_waypoints

def generate_dolly_zoom_path():
    if view_target is None:
        APP_WINDOW.append_status("No target available for Dolly Zoom mode; returning empty path.")
        return []
    start_vec = np.array([start_position["X"], start_position["Y"], start_position["Z"]])
    target_vec = np.array([view_target["X"], view_target["Y"], view_target["Z"]])
    initial_distance = np.linalg.norm(target_vec - start_vec)
    num_points = 5
    max_t = 0.95
    if reverse_dolly_zoom:
        t_values = [max_t - (max_t * i/(num_points-1)) for i in range(num_points)]
    else:
        t_values = [(max_t * i/(num_points-1)) for i in range(num_points)]
    waypoints = []
    for i, t in enumerate(t_values):
        pos = start_vec * (1 - t) + target_vec * t
        duration = round(t * dolly_settings["duration"], 3)
        current_distance = np.linalg.norm(target_vec - pos)
        new_zoom = initial_dolly_zoom * (current_distance / initial_distance) * dolly_zoom_exaggeration if initial_distance > 0 else initial_dolly_zoom
        new_zoom = min(max(new_zoom, 20), 300)
        euler = compute_look_at_unity(pos, target_vec, vertical_mode=False)
        wp = {
            "Index": i,
            "PathIndex": 0,
            "FocalDistance": focal_distance,
            "Aperture": aperture,
            "Hue": 120.0,
            "Saturation": 100.0,
            "Lightness": 50.0,
            "LookAtMeXOffset": 0.0,
            "LookAtMeYOffset": 0.0,
            "Zoom": round(new_zoom, 2),
            "Speed": dolly_speed,
            "Duration": duration,
            "Position": {"X": round(pos[0], 3), "Y": round(pos[1], 3), "Z": round(pos[2], 3)},
            "Rotation": {"X": round(euler[1], 2), "Y": round(euler[0], 2), "Z": round(euler[2], 2)}
        }
        waypoints.append(wp)
    return waypoints

def regenerate_path():
    global current_path_data
    if dolly_mode == MODE_CIRCLE:
        current_path_data = generate_circle_path()
    elif dolly_mode == MODE_ARC:
        current_path_data = generate_arc_path(arc_degrees=float(arc_angle), radius=float(dolly_settings.get("radius", 2.0)), clockwise=False, path_index=0, look_at_center=True)
    elif dolly_mode == MODE_LINE:
        current_path_data = generate_line_path()
    elif dolly_mode == MODE_ELLIPSE:
        current_path_data = generate_elliptical_path()
    elif dolly_mode == MODE_FILE:
        current_path_data = generate_loaded_path()
    elif dolly_mode == MODE_DOLLY_ZOOM:
        current_path_data = generate_dolly_zoom_path()
    else:
        current_path_data = []

    # If file mode with a view target, apply camera_offset only to certain points
    if dolly_mode == MODE_FILE and view_target is not None:
        for i, pt in enumerate(current_path_data):
            if i == 1:  # skip the "target" itself
                continue
            for axis in ['X', 'Y', 'Z']:
                pt["Position"][axis] = round(pt["Position"][axis] + camera_offset[axis], 3)
    # Other modes
    elif dolly_mode not in [MODE_DOLLY_ZOOM]:
        for pt in current_path_data:
            for axis in ['X', 'Y', 'Z']:
                pt["Position"][axis] = round(pt["Position"][axis] + camera_offset[axis], 3)

    # Apply rotation offset for non-Dolly-Zoom modes
    if dolly_mode not in [MODE_DOLLY_ZOOM]:
        if dolly_mode == MODE_FILE and view_target is not None:
            camera_points = []
            for i, pt in enumerate(current_path_data):
                if i == 1:
                    continue
                camera_points.append(np.array([pt["Position"]["X"], pt["Position"]["Y"], pt["Position"]["Z"]]))
        else:
            camera_points = [np.array([pt["Position"]["X"], pt["Position"]["Y"], pt["Position"]["Z"]]) for pt in current_path_data]
        if camera_points:
            pivot = np.mean(camera_points, axis=0)
            for i, pt in enumerate(current_path_data):
                if dolly_mode == MODE_FILE and view_target is not None and i == 1:
                    continue
                pos = np.array([pt["Position"]["X"], pt["Position"]["Y"], pt["Position"]["Z"]])
                rel = pos - pivot
                new_rel = camera_rotation_offset.apply(rel)
                new_pos = pivot + new_rel
                pt["Position"]["X"] = round(new_pos[0], 3)
                pt["Position"]["Y"] = round(new_pos[1], 3)
                pt["Position"]["Z"] = round(new_pos[2], 3)
        if current_path_data:
            for i, pt in enumerate(current_path_data):
                if dolly_mode == MODE_FILE and view_target is not None and i == 1:
                    continue
                base_rot = R.from_euler('XYZ', [pt["Rotation"]["X"], pt["Rotation"]["Y"], pt["Rotation"]["Z"]], degrees=True)
                new_rot = camera_rotation_offset * base_rot
                new_euler = new_rot.as_euler('XYZ', degrees=True)
                pt["Rotation"]["X"] = round(new_euler[0], 2)
                pt["Rotation"]["Y"] = round(new_euler[1], 2)
                pt["Rotation"]["Z"] = round(new_euler[2], 2)
    send_dolly_path()

def send_dolly_path():
    global initial_import
    if initial_import:
        print("Initial import suppressed.")
        initial_import = False
        return
    if current_path_data is None:
        return

    # Make a copy of the current path data.
    final_data = copy.deepcopy(current_path_data)

    # Apply reversal if the flag is set.
    if reverse_path:
        # For file mode (dolly_mode==MODE_FILE) with a target, keep index 1 fixed.
        if dolly_mode == MODE_FILE and view_target is not None and len(final_data) > 2:
            # Keep first two points (start and target) intact.
            start_target = final_data[:2]
            # Reverse the remaining points.
            rest = final_data[2:]
            rest.reverse()
            final_data = start_target + rest
        else:
            final_data.reverse()
        # Optionally update the Index fields for debugging:
        for i, pt in enumerate(final_data):
            pt["Index"] = i
        APP_WINDOW.append_status(f"Reversed path order:", [pt["Index"] for pt in final_data])

    # Apply common adjustments.
    for pt in final_data:
        pt["LookAtMeXOffset"] = lookat_x_offset
        pt["LookAtMeYOffset"] = lookat_y_offset
        if dolly_mode != MODE_DOLLY_ZOOM:
            pt["Zoom"] = dolly_zoom
        pt["Speed"] = dolly_speed

    # If we have a target and are using it, adjust rotations.
    if view_target is not None and use_view_target:
        for i, pt in enumerate(final_data):
            # In file mode, skip the target waypoint (assumed index 1)
            if dolly_mode == MODE_FILE and view_target is not None and i == 1:
                continue
            cam = np.array([pt["Position"]["X"], pt["Position"]["Y"], pt["Position"]["Z"]])
            tgt = np.array([view_target["X"], view_target["Y"], view_target["Z"]])
            euler = compute_look_at_unity(cam, tgt, vertical_mode=False)
            pt["Rotation"] = {"X": round(euler[1], 2), "Y": round(euler[0], 2), "Z": round(euler[2], 2)}

    # Apply vertical adjustment if enabled.
    if dolly_vertical:
        for pt in final_data:
            base_rot = R.from_euler('YXZ', [pt["Rotation"]["Y"], pt["Rotation"]["X"], pt["Rotation"]["Z"]], degrees=True)
            vertical_adjust = R.from_euler('Z', 90, degrees=True)
            final_rot = base_rot * vertical_adjust
            euler = final_rot.as_euler('YXZ', degrees=True)
            pt["Rotation"] = {"X": round(euler[1], 2), "Y": round(euler[0], 2), "Z": round(euler[2], 2)}

    # Handle "Pause" by duplicating the last waypoint if needed.
    if dolly_pause and final_data:
        add_pause_at_end(final_data)  

    json_data = json.dumps(final_data)
    APP_WINDOW.append_status(f"Sending dolly path (size: {len(json_data)} bytes)")
    temp_file_path = os.path.join(USED_LOCATIONS_PATH, "temp_dolly_export.json")
    try:
        with open(temp_file_path, "w", encoding="utf-8") as f:
            f.write(json_data)
        client.send_message("/dolly/Import", temp_file_path)
        print(f"Sent OSC message with file path: {temp_file_path}")
    except Exception as e:
        APP_WINDOW.append_status(f"Error writing temp file: {e}")


def adjust_position(axis, direction):
    global current_path_data, camera_offset, translation_step_value
    delta = direction * translation_step_value
    camera_offset[axis] += delta
    if current_path_data is None:
        return
    for i, pt in enumerate(current_path_data):
        if dolly_mode == MODE_FILE and view_target is not None and i == 1:
            continue
        pt["Position"][axis] = round(pt["Position"][axis] + delta, 3)
    send_dolly_path()

def rotate_path(axis, angle_deg):
    global current_path_data, camera_rotation_offset, rotation_step_value
    delta_angle = angle_deg * rotation_step_value
    delta_rot = R.from_euler(axis, delta_angle, degrees=True)
    camera_rotation_offset = delta_rot * camera_rotation_offset
    regenerate_path()

def rebase_loaded_path():
    global loaded_path_data_original
    if not loaded_path_data_original:
        APP_WINDOW.append_status("No custom path loaded to rebase.")
        return
    offset_x = start_position["X"] - loaded_path_data_original[0]["Position"]["X"]
    offset_y = start_position["Y"] - loaded_path_data_original[0]["Position"]["Y"]
    offset_z = start_position["Z"] - loaded_path_data_original[0]["Position"]["Z"]
    for wp in loaded_path_data_original:
        wp["Position"]["X"] = round(wp["Position"]["X"] + offset_x, 3)
        wp["Position"]["Y"] = round(wp["Position"]["Y"] + offset_y, 3)
        wp["Position"]["Z"] = round(wp["Position"]["Z"] + offset_z, 3)
    APP_WINDOW.append_status("Loaded custom path rebased to start position:", start_position)
    regenerate_path()

def start_osc_server():
    dispatcher = Dispatcher()
    dispatcher.map("/usercamera/Pose", on_usercamera_pose)
    dispatcher.map("/avatar/parameters/SetTargetFromCam", on_avatar_set_target)
    dispatcher.map("/avatar/parameters/SetPathFromCam", on_avatar_set_path)
    dispatcher.map("/avatar/parameters/SetDollyMode", on_avatar_set_dolly_mode)

    # --- NEW: Avatar bool parameters for XYZ translate/rotate nudges ---
    def make_nudge_handler(param_key: str, kind: str, axis: str, direction: int):
        # kind: 'T' or 'R'
        def handler(address, *args):
            try:
                val = float(args[0]) if args else 0.0
            except Exception:
                val = 0.0
            if _rising_edge(param_key, val):
                if kind == 'T':
                    BUS.nudgeTranslate.emit(axis, direction)  # adjust_position(axis, ±1)
                else:
                    BUS.nudgeRotate.emit(axis, direction)     # rotate_path(axis, ±1)
        return handler

    maps = [
        # Translation: + / -
        ("/avatar/parameters/SetDolly_T+X", "SetDolly_T+X", 'T', "X", +1),
        ("/avatar/parameters/SetDolly_T+Y", "SetDolly_T+Y", 'T', "Y", +1),
        ("/avatar/parameters/SetDolly_T+Z", "SetDolly_T+Z", 'T', "Z", +1),
        ("/avatar/parameters/SetDolly_T-X", "SetDolly_T-X", 'T', "X", -1),
        ("/avatar/parameters/SetDolly_T-Y", "SetDolly_T-Y", 'T', "Y", -1),
        ("/avatar/parameters/SetDolly_T-Z", "SetDolly_T-Z", 'T', "Z", -1),

        # Rotation: + / -
        ("/avatar/parameters/SetDolly_R+X", "SetDolly_R+X", 'R', "X", +1),
        ("/avatar/parameters/SetDolly_R+Y", "SetDolly_R+Y", 'R', "Y", +1),
        ("/avatar/parameters/SetDolly_R+Z", "SetDolly_R+Z", 'R', "Z", +1),
        ("/avatar/parameters/SetDolly_R-X", "SetDolly_R-X", 'R', "X", -1),
        ("/avatar/parameters/SetDolly_R-Y", "SetDolly_R-Y", 'R', "Y", -1),
        ("/avatar/parameters/SetDolly_R-Z", "SetDolly_R-Z", 'R', "Z", -1),
    ]
    for addr, key, kind, axis, direc in maps:
        dispatcher.map(addr, make_nudge_handler(key, kind, axis, direc))

    server = osc_server.ThreadingOSCUDPServer((OSC_IP, OSC_PORT_RECEIVE), dispatcher)
    print(f"Starting OSC server on {OSC_IP}:{OSC_PORT_RECEIVE}")
    server.serve_forever()

def on_avatar_set_dolly_mode(address, *args):
    """Handle OSC int parameter to switch dolly mode.
    Accepts values matching the MODE_* constants.
    Equivalent to pressing the corresponding UI button.
    """
    try:
        raw = args[0] if args else 0
        # VRChat avatar parameters arrive as floats; cast to int safely
        val = int(float(raw))
    except Exception:
        return

    valid_modes = {MODE_CIRCLE, MODE_ARC, MODE_LINE, MODE_ELLIPSE, MODE_FILE, MODE_DOLLY_ZOOM}
    if val in valid_modes:
        # Avoid unnecessary regenerations if the mode is already set
        if val != dolly_mode:
            try:
                APP_WINDOW.append_status(f"OSC: SetDollyMode -> {val}")
            except Exception:
                pass
            set_mode(val)  # same path as pressing a UI button

def on_avatar_set_target(address, *args):
    if not _camera_pose_is_nonzero():
        soft_beep()
        APP_WINDOW.append_status("Ignored SetTargetFromCam: camera at origin (0,0,0).")
        return
    try:
        val = float(args[0]) if args else 0.0
    except Exception:
        val = 0.0
    prev = _AVATAR_TOGGLE_PREV["SetTargetFromCam"]
    if val >= 0.5 and prev < 0.5:   # rising edge
        BUS.setTargetFromCam.emit()
    _AVATAR_TOGGLE_PREV["SetTargetFromCam"] = val

def on_avatar_set_path(address, *args):
    if not _camera_pose_is_nonzero():
        soft_beep()
        APP_WINDOW.append_status("Ignored SetPathFromCam: camera at origin (0,0,0).")
        return    
    try:
        val = float(args[0]) if args else 0.0
    except Exception:
        val = 0.0
    prev = _AVATAR_TOGGLE_PREV["SetPathFromCam"]
    if val >= 0.5 and prev < 0.5:   # rising edge
        BUS.setPathFromCam.emit()
    _AVATAR_TOGGLE_PREV["SetPathFromCam"] = val

def start_osc_server_thread():
    threading.Thread(target=start_osc_server, daemon=True).start()


def on_usercamera_pose(address, *args):
    """OSC handler for camera pose: posX, posY, posZ, rotX, rotY, rotZ (degrees)."""
    try:
        if len(args) >= 6:
            x, y, z, rx, ry, rz = [float(a) for a in args[:6]]
            with pose_lock:
                current_camera_pos["X"] = round(x, 3)
                current_camera_pos["Y"] = round(y, 3)
                current_camera_pos["Z"] = round(z, 3)
                current_camera_rot["X"] = round(rx, 2)
                current_camera_rot["Y"] = round(ry, 2)
                current_camera_rot["Z"] = round(rz, 2)
                global last_pose_timestamp
                last_pose_timestamp = time.time()
    except Exception:
        # Ignore malformed packets; keep OSC thread resilient.
        pass


def toggle_reverse_dolly_zoom(val):
    global reverse_dolly_zoom
    reverse_dolly_zoom = val
    APP_WINDOW.append_status(f"Reverse Dolly Zoom: {reverse_dolly_zoom}")
    if dolly_mode == MODE_DOLLY_ZOOM:
        regenerate_path()

def set_mode(mode):
    global dolly_mode
    dolly_mode = mode
    if mode == MODE_DOLLY_ZOOM:  # Dolly Zoom mode
        ensure_dolly_zoom_init()
    regenerate_path()

def toggle_vertical(val):
    global dolly_vertical
    dolly_vertical = val
    APP_WINDOW.append_status(f"Vertical Mode: {dolly_vertical}")
    regenerate_path()

def toggle_pause(val):
    global dolly_pause
    dolly_pause = val
    APP_WINDOW.append_status(f"Pause: {dolly_pause}")
    regenerate_path()

def toggle_use_view_target(val):
    global use_view_target
    use_view_target = val
    APP_WINDOW.append_status(f"Use Target: {use_view_target}")
    regenerate_path()

def _camera_pose_is_nonzero() -> bool:
    """Return True iff current_camera_pos is NOT the world origin (0,0,0).
    No age/timestamp checks. Defensive against missing/NaN values.
    """
    try:
        x = float(current_camera_pos.get("X", 0.0))
        y = float(current_camera_pos.get("Y", 0.0))
        z = float(current_camera_pos.get("Z", 0.0))
        # Treat NaN as invalid (acts like zero guard)
        if any(v != v for v in (x, y, z)):
            return False
        return not (x == 0.0 and y == 0.0 and z == 0.0)
    except Exception:
        return False

def reset_to_defaults():
    global dolly_zoom, dolly_speed, lookat_x_offset, lookat_y_offset
    global camera_offset, camera_rotation_offset, dolly_vertical, dolly_pause
    global translation_step_value, rotation_step_value, dolly_zoom_exaggeration, aperture, focal_distance, user_points_limit
    dolly_settings["radius"] = 2.0
    dolly_settings["duration"] = 2.0
    dolly_zoom = 45.0
    dolly_speed = 3.0
    lookat_x_offset = 0.0
    lookat_y_offset = 0.0
    camera_offset = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    camera_rotation_offset = R.from_euler('XYZ', [0, 0, 0], degrees=True)
    dolly_vertical = False
    dolly_pause = False
    translation_step_value = 0.5
    rotation_step_value = 1.0
    dolly_zoom_exaggeration = 2.0
    aperture = 15.0
    focal_distance = 2
    user_points_limit = 15

    radius_slider.setValue(int(dolly_settings["radius"] * 100))
    radius_entry.setText(str(dolly_settings["radius"]))

    duration_slider.setValue(int(dolly_settings["duration"] * 100))
    duration_entry.setText(str(dolly_settings["duration"]))

    zoom_slider.setValue(int(dolly_zoom))
    zoom_entry.setText(str(dolly_zoom))

    speed_slider.setValue(int(dolly_speed * 100))
    speed_entry.setText(str(dolly_speed))

    lookat_x_slider.setValue(0)
    lookat_x_entry.setText("0.0")

    lookat_y_slider.setValue(0)
    lookat_y_entry.setText("0.0")

    translation_step_slider.setValue(int(translation_step_value * 100))
    translation_step_entry.setText(str(translation_step_value))

    rotation_step_slider.setValue(int(rotation_step_value * 100))
    rotation_step_entry.setText(str(rotation_step_value))
    
    dz_exag_slider.setValue(int(dolly_zoom_exaggeration * 100))
    dz_exag_entry.setText(str(dolly_zoom_exaggeration))
    
    aperture_slider.setValue(int(aperture * 100))
    aperture_entry.setText(str(aperture))

    focal_distance_slider.setValue(int(focal_distance * 100))
    focal_distance_entry.setText(str(focal_distance))

    points_count_slider.setValue(user_points_limit)
    points_count_entry.setText(str(user_points_limit))

    vertical_toggle.setChecked(False)
    pause_toggle.setChecked(False)
    reverse_zoom_checkbox.setChecked(False)
    if view_target is not None:
        use_view_target_checkbox.setChecked(True)
    else:
        use_view_target_checkbox.setChecked(False)

    regenerate_path()

# --------------------------
# PyQt6 User Interface
# --------------------------
class DollyControllerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VRChat Dolly Controller V2.61")
        self.setGeometry(100, 100, 800, 840)
        scroll = QScrollArea()
        self.central_widget = QWidget()
        self.setCentralWidget(scroll)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.setup_ui()

    def pin_button_pressed(self, pin_number):
        modifiers = QGuiApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            export_pin(pin_number)
        else:
            load_pin(pin_number)

    def setup_ui(self):
        #
        # --- 1) Mode Selection (5 main modes only) ---
        #

        action_frame = QHBoxLayout()
        btn_set_path = QPushButton("Set Path")
        btn_set_path.clicked.connect(self.set_path_from_camera)
        action_frame.addWidget(btn_set_path)

        btn_set_target = QPushButton("Set Target")
        btn_set_target.clicked.connect(self.set_target_from_camera)
        action_frame.addWidget(btn_set_target)

        self.main_layout.addLayout(action_frame)

        mode_frame = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        # Just the 5 main modes
        modes = [
            (1, "Circle Mode"),
            (2, "Arc Mode"),
            (3, "Line Mode"),
            (4, "Elliptical Mode"),
            (5, "File Mode"),
            (6, "Dolly Zoom Mode")
        ]
        for mode_val, text in modes:
            button = QPushButton(text)
            button.setCheckable(True)
            self.mode_group.addButton(button, mode_val)
            self.mode_buttons[mode_val] = button
            mode_frame.addWidget(button)
        self.mode_buttons[1].setChecked(True)
        self.mode_group.buttonClicked.connect(lambda btn: self.set_mode(self.mode_group.id(btn)))
        self.main_layout.addLayout(mode_frame)

        #
        # --- 3) Custom JSON, Move Target, and Rebase ---
        #

        load_frame = QHBoxLayout()
        btn_load_custom = QPushButton("Load Custom JSON File")
        btn_load_custom.clicked.connect(self.load_custom_json)
        load_frame.addWidget(btn_load_custom)

        btn_rebase = QPushButton("Rebase Custom Path")
        btn_rebase.clicked.connect(rebase_loaded_path)
        load_frame.addWidget(btn_rebase)
        self.main_layout.addLayout(load_frame)
        self.loaded_file_label = QLabel("No file loaded")
        self.main_layout.addWidget(self.loaded_file_label)

        btn_play = QPushButton("Play")
        btn_play.clicked.connect(self.play)
        load_frame.addWidget(btn_play)

        regen_btn = QPushButton("Regenerate Path")
        regen_btn.clicked.connect(regenerate_path)
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(reset_to_defaults)
        load_frame.addWidget(regen_btn)
        load_frame.addWidget(reset_btn)

        # --- 4) Pin Buttons (arranged as 2 rows of 4) ---

        pin_frame_row1 = QHBoxLayout()
        pin_frame_row2 = QHBoxLayout()
        self.pin_buttons = {}
        for i in range(1, 9):
            btn = QPushButton(f"Pin {i}")
            # They don't need to be checkable
            btn.setCheckable(False)
            # Connect the button so that a shift-click exports the current pin, otherwise load it.
            btn.clicked.connect(lambda checked, p=i: self.pin_button_pressed(p))
            self.pin_buttons[i] = btn
            # Add to first row if i<=4, otherwise second row.
            if i <= 4:
                pin_frame_row1.addWidget(btn)
            else:
                pin_frame_row2.addWidget(btn)
        self.main_layout.addLayout(pin_frame_row1)
        self.main_layout.addLayout(pin_frame_row2)

        #
        # --- 5) Dolly Parameters (sliders, text entries, etc.) ---
        #

        # Radius
        radius_layout = QHBoxLayout()
        radius_layout.addWidget(QLabel("Radius:     "))
        global radius_entry, radius_slider 
        radius_entry = QLineEdit(str(dolly_settings["radius"]))
        radius_entry.setFixedSize(60, 25)
        radius_entry.editingFinished.connect(on_radius_entry_return)
        radius_layout.addWidget(radius_entry)
        radius_slider = QSlider(Qt.Orientation.Horizontal)
        radius_slider.setMinimum(10)
        radius_slider.setMaximum(5000)
        radius_slider.setValue(int(dolly_settings["radius"] * 100))
        radius_slider.valueChanged.connect(update_radius_slider)
        radius_layout.addWidget(radius_slider)
        self.main_layout.addLayout(radius_layout)

        # Duration
        duration_layout = QHBoxLayout()
        duration_layout.addWidget(QLabel("Duration:  "))
        global duration_entry, duration_slider
        duration_entry = QLineEdit(str(dolly_settings["duration"]))
        duration_entry.setFixedSize(60, 25)
        duration_entry.editingFinished.connect(on_duration_entry_return)
        duration_layout.addWidget(duration_entry)
        duration_slider = QSlider(Qt.Orientation.Horizontal)
        duration_slider.setMinimum(10)
        duration_slider.setMaximum(6000)
        duration_slider.setValue(int(dolly_settings["duration"] * 100))
        duration_slider.valueChanged.connect(update_duration_slider)
        duration_layout.addWidget(duration_slider)
        self.main_layout.addLayout(duration_layout)

        # Zoom
        zoom_layout = QHBoxLayout()
        zoom_layout.addWidget(QLabel("Zoom:       "))
        global zoom_entry, zoom_slider
        zoom_entry = QLineEdit(str(dolly_zoom))
        zoom_entry.setFixedSize(60, 25)
        zoom_entry.editingFinished.connect(on_zoom_entry_return)
        zoom_layout.addWidget(zoom_entry)
        zoom_slider = QSlider(Qt.Orientation.Horizontal)
        zoom_slider.setMinimum(20)
        zoom_slider.setMaximum(300)
        zoom_slider.setValue(int(dolly_zoom))
        zoom_slider.valueChanged.connect(update_zoom_slider)
        zoom_layout.addWidget(zoom_slider)
        self.main_layout.addLayout(zoom_layout)

        # Speed
        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("Speed:      "))
        global speed_entry, speed_slider
        speed_entry = QLineEdit(str(dolly_speed))
        speed_entry.setFixedSize(60, 25)
        speed_entry.editingFinished.connect(on_speed_entry_return)
        speed_layout.addWidget(speed_entry)
        speed_slider = QSlider(Qt.Orientation.Horizontal)
        speed_slider.setMinimum(10)
        speed_slider.setMaximum(1500)
        speed_slider.setValue(int(dolly_speed * 100))
        speed_slider.valueChanged.connect(update_speed_slider)
        speed_layout.addWidget(speed_slider)
        self.main_layout.addLayout(speed_layout)

        # Aperture
        aperture_layout = QHBoxLayout()
        aperture_layout.addWidget(QLabel("Aperture:  "))
        global aperture_entry, aperture_slider
        aperture_entry = QLineEdit(str(aperture))
        aperture_entry.setFixedSize(60, 25)
        aperture_entry.editingFinished.connect(on_aperture_entry_return)
        aperture_layout.addWidget(aperture_entry)
        aperture_slider = QSlider(Qt.Orientation.Horizontal)
        aperture_slider.setMinimum(140)
        aperture_slider.setMaximum(3200)
        aperture_slider.setValue(int(aperture * 100))
        aperture_slider.valueChanged.connect(update_aperture_slider)
        aperture_layout.addWidget(aperture_slider)
        self.main_layout.addLayout(aperture_layout)

        # Focal Distance
        focal_distance_layout = QHBoxLayout()
        focal_distance_layout.addWidget(QLabel("Focal Distance:  "))
        global focal_distance_entry, focal_distance_slider
        focal_distance_entry = QLineEdit(str(focal_distance))
        focal_distance_entry.setFixedSize(60, 25)
        focal_distance_entry.editingFinished.connect(on_focal_distance_entry_return)
        focal_distance_layout.addWidget(focal_distance_entry)
        focal_distance_slider = QSlider(Qt.Orientation.Horizontal)
        focal_distance_slider.setMinimum(10)
        focal_distance_slider.setMaximum(3000)
        focal_distance_slider.setValue(int(focal_distance * 100))
        focal_distance_slider.valueChanged.connect(update_focal_distance_slider)
        focal_distance_layout.addWidget(focal_distance_slider)
        self.main_layout.addLayout(focal_distance_layout)

        # Arc Angle Control
        arc_angle_layout = QHBoxLayout()
        arc_angle_layout.addWidget(QLabel("Arc Angle:"))
        global arc_angle_entry
        arc_angle_entry = QLineEdit(str(arc_angle))
        arc_angle_entry.setFixedSize(60, 25)
        arc_angle_entry.editingFinished.connect(on_arc_angle_entry_return)
        arc_angle_layout.addWidget(arc_angle_entry)
        arc_angle_slider = QSlider(Qt.Orientation.Horizontal)
        arc_angle_slider.setMinimum(5)
        arc_angle_slider.setMaximum(180)
        arc_angle_slider.setValue(int(arc_angle))
        arc_angle_slider.valueChanged.connect(update_arc_angle_slider)
        arc_angle_layout.addWidget(arc_angle_slider)
        self.main_layout.addLayout(arc_angle_layout)

        # Dolly Zoom Exaggeration
        dz_exag_layout = QHBoxLayout()
        dz_exag_layout.addWidget(QLabel("Dolly Zoom Exaggeration:"))
        global dz_exag_entry, dz_exag_slider
        dz_exag_entry = QLineEdit(str(dolly_zoom_exaggeration))
        dz_exag_entry.setFixedSize(60, 25)
        dz_exag_entry.editingFinished.connect(on_dz_exaggeration_entry_return)
        dz_exag_layout.addWidget(dz_exag_entry)
        dz_exag_slider = QSlider(Qt.Orientation.Horizontal)
        dz_exag_slider.setMinimum(100)
        dz_exag_slider.setMaximum(500)
        dz_exag_slider.setValue(int(dolly_zoom_exaggeration * 100))
        dz_exag_slider.valueChanged.connect(update_dz_exaggeration_slider)
        dz_exag_layout.addWidget(dz_exag_slider)
        self.main_layout.addLayout(dz_exag_layout)

        # Points Count
        points_layout = QHBoxLayout()
        points_layout.addWidget(QLabel("Number of Points:   "))
        global points_count_entry, points_count_slider
        points_count_entry = QLineEdit(str(user_points_limit))
        points_count_entry.setFixedSize(60, 25)
        points_count_entry.editingFinished.connect(on_points_count_entry_return)
        points_layout.addWidget(points_count_entry)
        points_count_slider = QSlider(Qt.Orientation.Horizontal)
        points_count_slider.setMinimum(5)
        points_count_slider.setMaximum(50)
        points_count_slider.setValue(user_points_limit)
        points_count_slider.valueChanged.connect(update_points_count_slider)
        points_layout.addWidget(points_count_slider)
        self.main_layout.addLayout(points_layout)

        # Step Controls
        step_layout = QHBoxLayout()
        translation_step_label = QLabel("Translation Step (m):")
        step_layout.addWidget(translation_step_label)
        global translation_step_entry, translation_step_slider
        translation_step_entry = QLineEdit(str(translation_step_value))
        translation_step_entry.setFixedSize(60, 25)
        translation_step_entry.editingFinished.connect(on_translation_step_entry_return)
        step_layout.addWidget(translation_step_entry)
        translation_step_slider = QSlider(Qt.Orientation.Horizontal)
        translation_step_slider.setMinimum(1)    # represents 0.01
        translation_step_slider.setMaximum(500)  # represents 5.00
        translation_step_slider.setValue(int(translation_step_value * 100))
        translation_step_slider.valueChanged.connect(update_translation_step_slider)
        step_layout.addWidget(translation_step_slider)
        rotation_step_label = QLabel("Rotation Step (°):")
        step_layout.addWidget(rotation_step_label)
        global rotation_step_entry, rotation_step_slider
        rotation_step_entry = QLineEdit(str(rotation_step_value))
        rotation_step_entry.setFixedSize(60, 25)
        rotation_step_entry.editingFinished.connect(on_rotation_step_entry_return)
        step_layout.addWidget(rotation_step_entry)
        rotation_step_slider = QSlider(Qt.Orientation.Horizontal)
        rotation_step_slider.setMinimum(1)    # represents 0.01°
        rotation_step_slider.setMaximum(9000) # represents 15.00°
        rotation_step_slider.setValue(int(rotation_step_value * 100))
        rotation_step_slider.valueChanged.connect(update_rotation_step_slider)
        step_layout.addWidget(rotation_step_slider)
        self.main_layout.addLayout(step_layout)

        # Toggle Options
        toggle_layout = QHBoxLayout()
        global vertical_toggle, pause_toggle, use_view_target_checkbox, reverse_zoom_checkbox
        vertical_toggle = QCheckBox("Rotate 90")
        vertical_toggle.toggled.connect(toggle_vertical)
        toggle_layout.addWidget(vertical_toggle)
        pause_toggle = QCheckBox("Pause")
        pause_toggle.toggled.connect(toggle_pause)
        toggle_layout.addWidget(pause_toggle)
        use_view_target_checkbox = QCheckBox("Use Target")
        use_view_target_checkbox.setChecked(True)
        use_view_target_checkbox.toggled.connect(toggle_use_view_target)
        toggle_layout.addWidget(use_view_target_checkbox)

        # New "Reverse Path" checkbox.
        reverse_path_checkbox = QCheckBox("Reverse Path")
        reverse_path_checkbox.toggled.connect(lambda checked: set_reverse_path(checked))
        toggle_layout.addWidget(reverse_path_checkbox)

        reverse_zoom_checkbox = QCheckBox("Reverse Dolly Zoom")
        reverse_zoom_checkbox.toggled.connect(toggle_reverse_dolly_zoom)
        toggle_layout.addWidget(reverse_zoom_checkbox)

        self.main_layout.addLayout(toggle_layout)

        # LookAtMe Offsets
        lookat_layout = QVBoxLayout()
        lookat_layout.addWidget(QLabel("LookAtMe Offsets"))
        lookat_x_layout = QHBoxLayout()
        lookat_x_layout.addWidget(QLabel("Horizontal Offset:"))
        global lookat_x_entry, lookat_x_slider
        lookat_x_entry = QLineEdit("0.0")
        lookat_x_entry.setFixedSize(60, 25)
        lookat_x_entry.editingFinished.connect(on_lookat_x_entry_return)
        lookat_x_layout.addWidget(lookat_x_entry)
        lookat_x_slider = QSlider(Qt.Orientation.Horizontal)
        lookat_x_slider.setMinimum(-2000)
        lookat_x_slider.setMaximum(2000)
        lookat_x_slider.setValue(0)
        lookat_x_slider.valueChanged.connect(update_lookat_x_slider)
        lookat_x_layout.addWidget(lookat_x_slider)
        lookat_layout.addLayout(lookat_x_layout)
        lookat_y_layout = QHBoxLayout()
        lookat_y_layout.addWidget(QLabel("Vertical Offset:     "))
        global lookat_y_entry, lookat_y_slider
        lookat_y_entry = QLineEdit("0.0")
        lookat_y_entry.setFixedSize(60, 25)
        lookat_y_entry.editingFinished.connect(on_lookat_y_entry_return)
        lookat_y_layout.addWidget(lookat_y_entry)
        lookat_y_slider = QSlider(Qt.Orientation.Horizontal)
        lookat_y_slider.setMinimum(-2000)
        lookat_y_slider.setMaximum(2000)
        lookat_y_slider.setValue(0)
        lookat_y_slider.valueChanged.connect(update_lookat_y_slider)
        lookat_y_layout.addWidget(lookat_y_slider)
        lookat_layout.addLayout(lookat_y_layout)
        self.main_layout.addLayout(lookat_layout)

        # Axis Controls
        axis_label = QLabel("Axis Controls")
        self.main_layout.addWidget(axis_label)
        for axis in ['X', 'Y', 'Z']:
            ax_layout = QHBoxLayout()
            ax_layout.addWidget(QLabel(f"{axis}-Axis Controls"))
            btn_trans_plus = QPushButton(f"Translate +{axis}")
            btn_trans_plus.clicked.connect(lambda _, a=axis: adjust_position(a, 1))
            ax_layout.addWidget(btn_trans_plus)
            btn_trans_minus = QPushButton(f"Translate -{axis}")
            btn_trans_minus.clicked.connect(lambda _, a=axis: adjust_position(a, -1))
            ax_layout.addWidget(btn_trans_minus)
            btn_rot_plus = QPushButton(f"Rotate +{axis}")
            btn_rot_plus.clicked.connect(lambda _, a=axis: rotate_path(a, 5))
            ax_layout.addWidget(btn_rot_plus)
            btn_rot_minus = QPushButton(f"Rotate -{axis}")
            btn_rot_minus.clicked.connect(lambda _, a=axis: rotate_path(a, -5))
            ax_layout.addWidget(btn_rot_minus)
            self.main_layout.addLayout(ax_layout)

        # --- Status Panel ---
        self.status_box = QTextEdit()
        self.status_box.setReadOnly(True)
        self.status_box.setFixedHeight(80)
        self.status_box.setPlaceholderText("Status: Listening for OSC commands")
        self.main_layout.addWidget(self.status_box)

    def set_mode(self, mode):
        set_mode(mode)  # call global helper to handle init & regen

    def append_status(self, msg):
        ts = time.strftime("%H:%M:%S")
        try:
            self.status_box.append(f"[{ts}] {msg}")
        except Exception:
            APP_WINDOW.append_status(f"[{ts}] {msg}")

    def load_custom_json(self):
        global loaded_path_data_original
        fname, _ = QFileDialog.getOpenFileName(self, "Select a custom path JSON", EXPORT_PATH, "JSON Files (*.json);;All Files (*)")
        if not fname:
            return
        try:
            with open(fname, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded_path_data_original = data
            self.loaded_file_label.setText(f"Loaded file: {os.path.basename(fname)}")
            APP_WINDOW.append_status(f"Custom JSON loaded from {fname}, {len(data)} waypoints.")
            regenerate_path()
        except Exception as e:
            self.loaded_file_label.setText("Failed to load file!")
            APP_WINDOW.append_status(f"Error loading custom JSON: {e}")

    def set_target_from_camera(self):
        # Use the latest cached camera position as the view target
        global view_target, use_view_target
        view_target = {
            "X": current_camera_pos["X"],
            "Y": current_camera_pos["Y"],
            "Z": current_camera_pos["Z"],
        }
        use_view_target = True

        if not _camera_pose_is_nonzero():
            soft_beep()
            APP_WINDOW.append_status("Ignored SetTargetFromCam: camera at origin (0,0,0).")
            return

        APP_WINDOW.append_status(f"Pose rx: pos={current_camera_pos} rot={current_camera_rot}")

        # if you have a checkbox bound: use_view_target_checkbox.setChecked(True)

        APP_WINDOW.append_status(f"Target set from camera: {view_target}")
        regenerate_path()

    def set_path_from_camera(self):
        # Use the latest cached camera position as the path origin/center
        global start_position, exported_center
        start_position["X"] = current_camera_pos["X"]
        start_position["Y"] = current_camera_pos["Y"]
        start_position["Z"] = current_camera_pos["Z"]
        exported_center = dict(start_position)  # if your circle/arc uses this center

        if not _camera_pose_is_nonzero():
            soft_beep()
            APP_WINDOW.append_status("Ignored SetTargetFromCam: camera at origin (0,0,0).")
            return

        APP_WINDOW.append_status(f"Path origin set from camera: {start_position}")
        regenerate_path()

    def play(self):
        # --- Countdown Dialog ---
        countdown_dialog = QDialog(self)
        countdown_dialog.setWindowTitle("Countdown")
        countdown_layout = QVBoxLayout(countdown_dialog)
        countdown_label = QLabel("Starting in 7 seconds...", countdown_dialog)
        countdown_layout.addWidget(countdown_label)
        countdown_dialog.setLayout(countdown_layout)
        
        countdown_time = 7  # seconds
        timer = QTimer(countdown_dialog)
        timer.setInterval(1000)  # 1 second

        def update_countdown():
            nonlocal countdown_time
            countdown_time -= 1
            if countdown_time > 0:
                countdown_label.setText(f"Starting in {countdown_time} seconds...")
            else:
                timer.stop()
                # Play 1-second beep.
                try:
                    import winsound
                    winsound.Beep(1000, 1000)  # 1000Hz for 1000ms.
                except Exception as e:
                    APP_WINDOW.append_status("Error playing beep:", e)
                # Send OSC /dolly/Play command.
                client.send_message("/dolly/Play", 1)
                APP_WINDOW.append_status("Sent OSC /dolly/Play command")
                countdown_dialog.accept()

        timer.timeout.connect(update_countdown)
        timer.start()
        countdown_dialog.exec()
        
        # --- Check if MP3 file exists, skip playback if missing ---
        if not os.path.exists(PERFORM_MP3_PATH):
            APP_WINDOW.append_status(f"MP3 file not found at {PERFORM_MP3_PATH}. Skipping playback.")
            return  # Exit early, skipping the performance dialog.

        # --- Performance Dialog ---
        performance_dialog = QDialog(self)
        performance_dialog.setWindowTitle("Performance")
        perf_layout = QVBoxLayout(performance_dialog)

        # Label to display time (elapsed / total)
        time_label = QLabel("0 / 0 sec", performance_dialog)
        perf_layout.addWidget(time_label)

        # Progress bar (percentage)
        progress_bar = QProgressBar(performance_dialog)
        progress_bar.setRange(0, 100)
        perf_layout.addWidget(progress_bar)

        performance_dialog.setLayout(perf_layout)

        # Set up the media player.
        player = QMediaPlayer()
        audio_output = QAudioOutput()
        audio_output.setVolume(1.0)  # Maximum volume.
        player.setAudioOutput(audio_output)
        player.setSource(QUrl.fromLocalFile(PERFORM_MP3_PATH))

        # Debug: print any media player errors.
        def handle_error():
            err = player.error()
            if err:
                APP_WINDOW.append_status("Media player error:", player.errorString())
        player.errorOccurred.connect(lambda e: handle_error())

        # Update progress bar and time label.
        def update_progress(position):
            duration = player.duration()
            if duration > 0:
                percent = int((position / duration) * 100)
                progress_bar.setValue(percent)
                total_sec = int(duration / 1000)
                current_sec = int(position / 1000)
                time_label.setText(f"{current_sec} / {total_sec} sec")
        player.positionChanged.connect(update_progress)

        # Close the performance dialog when playback finishes.
        def on_media_status_changed(status):
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                performance_dialog.accept()
        player.mediaStatusChanged.connect(on_media_status_changed)

        player.play()
        performance_dialog.exec()

        # Show the modal popup instructing the user.

def setup_ui_and_run():
    app = QApplication(sys.argv)
    app.setStyleSheet("""
    QMainWindow {
        background-color: #1e1e1e;
    }
    QWidget {
        font-size: 12pt;
        color: #eeeeee;
        background-color: #2e2e2e;
    }
    QPushButton {
        background-color: #3a3a3a;
        border: 1px solid #555;
        padding: 5px;
        border-radius: 3px;
    }
    QPushButton:hover {
        background-color: #505050;
    }
    QPushButton:checked {
        background-color: #0078d7;
        border: 1px solid #0078d7;
        color: #ffffff;
    }
    QLineEdit {
        background-color: #3a3a3a;
        border: 1px solid #555;
        border-radius: 3px;
    }
    QSlider::groove:horizontal {
        background: #555;
        height: 6px;
        border-radius: 3px;
    }
    QSlider::sub-page:horizontal {
        background: #0078d7;
        height: 6px;
        border-radius: 3px;
    }
    QSlider::add-page:horizontal {
        background: #999;
        height: 6px;
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        background: #ddd;
        border: 1px solid #aaa;
        width: 14px;
        margin: -4px 0;
        border-radius: 7px;
    }
    QCheckBox {
        spacing: 5px;
    }
    QCheckBox::indicator {
        width: 18px;
        height: 18px;
    }
    """)
    # Set the embedded icon as the app's window icon.
    pixmap = QPixmap()
    pixmap.loadFromData(base64.b64decode(ICON_BASE64), "ICO")
    app.setWindowIcon(QIcon(pixmap))
    global APP_WINDOW
    APP_WINDOW = DollyControllerWindow()
    window = APP_WINDOW
    BUS.setTargetFromCam.connect(window.set_target_from_camera)
    BUS.setPathFromCam.connect(window.set_path_from_camera)  
    BUS.nudgeTranslate.connect(lambda axis, d: adjust_position(axis, d))
    BUS.nudgeRotate.connect(   lambda axis, d: rotate_path(axis, d))      
    window.show()
    sys.exit(app.exec())

# --------------------------
# Main Entry Point
# --------------------------
if __name__ == "__main__":
    start_osc_server_thread()
    regenerate_path()
    setup_ui_and_run()