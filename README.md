# VRChat Dolly Controller

VRChat Dolly Controller is a Python-based tool that gives you full control over VRChat’s Dolly mode. Originally developed for my own filmmaking, this software is available for anyone who finds it useful.

![Dolly Controller Screenshot](https://github.com/user-attachments/assets/de04c92d-818e-44f1-95cd-893a7784713f)

## Features

- **Real-Time Camera Path Generation:**  
  Generate dynamic camera paths (e.g., circle, arc, line, elliptical, file, and dolly zoom modes) and modify them on the fly.

- **Automatic Folder Monitoring:**  
  The software monitors the `VRChat\CameraPaths` folder for new exports. It automatically moves new exports into two organized folders: `Used_Locations` and `Bookmarks`. This prevents clutter while preserving your exported data.

- **Intuitive Dolly Path Setup:**  
  Create a Dolly Path in VRChat by exporting two points:  
  - **First Point:** Defines the path location (for example, the center of a circle in Circle Mode).  
  - **Second Point:** Defines the target, which is where all cameras will continuously look.  
  When you move or update the path, the cameras will recalculate to keep their focus on this target.

- **Interactive Updates:**  
  Use the **Move Target** and **Move Path** functions to update the respective positions. When activated, the current path temporarily disappears and is replaced with a single point. After you reposition it in-game and confirm the update, the path is recalculated.

- **Non-Destructive Importing:**  
  All manual exports are imported without being deleted. They are automatically moved to the `Used_Locations` folder to avoid clutter and accidental loss of data.

- **Bookmarking:**  
  Save and load pin locations (stored in the `Bookmarks` folder) to quickly recall your preferred camera setups.

- **Experimental File Mode:**  
  File mode allows you to import existing camera paths and adjust their positions or rotation. This mode is still under testing.

## How It Works

When running, the script continuously monitors the export folder. In VRChat, you create a Dolly Path by exporting two camera points:
- The **first point** serves as the reference (for example, the center of the circle in Circle Mode).
- The **second point** sets the target that all cameras will look at.

When you use the **Move Target** or **Move Path** buttons, the path temporarily resets. You then reposition the point in-game, confirm the update, and the software recalculates the path accordingly.

The tool also preserves any pre-existing paths in memory—unless you actively switch modes, in which case the paths might be updated.

## Getting Started

1. Place the script in your working environment.
2. Run the Python script.
3. In VRChat, export a Dolly Path using two points (start and target).
4. Watch as the tool automatically updates and recalculates the camera paths in real time.
5. Use the provided UI to adjust parameters, move targets, rebase paths, and save/load your favorite configurations via pins.

---

A build script batch file has been included that cam build the EXE with proper windows icon.
