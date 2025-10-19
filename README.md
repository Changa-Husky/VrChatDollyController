# VRChat Dolly Controller

VRChat Dolly Controller is a Python-based tool that gives you full control over VRChat’s Dolly Camera Mode. Originally developed for my own filmmaking workflow, this software is available for anyone who finds it useful.
<img width="602" height="705" alt="DollyControl_V2 61_ajJ4uKu5hp" src="https://github.com/user-attachments/assets/fd57ccd8-3ced-4980-aae8-686ae1247cb4" />

---

## Features

- **Real-Time Camera Path Generation**  
  Generate camera paths and modify them on the fly using:
  - Circle Mode
  - Arc Mode
  - Line Mode
  - Elliptical Mode
  - Dolly Zoom Mode
  - File Mode

- **Intuitive Dolly Path Setup**  
  Dolly paths in VRChat are generated using two camera exports:
  - Path Origin – defines the path location (for example, the center of a circle)
  - Path Target – defines the point that cameras will always face during playback  
  When either is changed, the path is recalculated automatically.

- **Interactive Updates**  
  Use Move Target and Move Path to reposition paths inside VRChat. When either is enabled, the path is temporarily hidden and replaced by a marker. Confirming the movement regenerates the full path in the new position.

- **Bookmarking System**  
  Save and reload path origins and targets using Pins. Bookmarks are stored in the `Bookmarks` folder and can be reused at any time.

- **Play Function**  
  Includes a simple playback helper that waits seven seconds, plays a beep, and then starts the path while playing `perform.mp3` from your desktop. This was built for personal use but is included as-is.

- **File Mode**  
  Import existing dolly path JSON files and adjust their world position or rotation. This mode is experimental.

- **Avatar OSC Control**  
  Includes a Unity package that adds a local avatar menu for controlling dolly functions. This allows Set Path, Set Target, and axis control directly from an avatar menu. Requires VRCFury to install.

---

## How It Works

The tool communicates with VRChat using OSC. It reads the camera's position and orientation to generate dolly paths dynamically. Paths are injected directly into the VRChat dolly folder. Control is available through the desktop interface or through the included avatar OSC menu.

---

## Getting Started

1. Place the script in your working environment.
2. Ensure OSC is enabled in VRChat (`Settings > OSC > Enabled`).
3. Run the Python script.
4. In VRChat, bring out the camera and click "Set Path" to define a path origin.
5. Optionally click "Set Target" to define a focal point.
6. Adjust path settings, regenerate as needed, and use pins to save and restore locations.

---

## Building a Windows Executable (Optional)

A batch script is included to build a standalone Windows `.exe` using PyInstaller. This includes a proper Windows icon and avoids requiring Python to be installed to run the tool.

---


