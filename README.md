# Hosty

Hosty is a desktop app for creating, running, and managing Minecraft Fabric servers with a clean, native-style UI.

It keeps the full server workflow in one app: setup, start/stop, monitoring, mod management, backups, and player access controls.

## Features

- Guided Fabric server creation with version selection and EULA step
- Automatic Java runtime handling when required by selected Minecraft version
- Multi-server library with clear running/starting/stopped status
- Start/stop controls with safety checks and restart-required notices when needed
- Console view with command input, command autocomplete, and output history
- Performance monitoring view for live server resource insight
- Properties editor with autosave for server.properties and RAM allocation
- Worlds and dimensions manager with open-folder actions and safe delete flows
- Backup tools to create, list, restore, and remove world backups
- Mod manager with Modrinth search/install, dependency resolution, and dependency-aware remove warnings
- Undo support for destructive actions across servers, worlds, dimensions, mods, backups, and player list removals
- Connect tools including local IP copy, Playit.gg tunnel setup, and whitelist/ban management

## Run Hosty

- Linux: use the Flatpak release from GitHub Releases.
- Windows: use the EXE release from GitHub Releases.

<details>
<summary>Run from source (Python)</summary>

### Linux

1. Install GTK4/libadwaita and PyGObject system packages.
2. Install Python dependencies:

```bash
python3 -m pip install requests psutil pystray Pillow
```

3. Run Hosty:

```bash
python3 hosty.py
```

### Windows

1. Install Python dependencies:

```bash
python -m pip install -r requirements-windows.txt
```

2. Run Hosty:

```bash
python hosty.py
```

</details>

## Screenshots

<p align="center">
	<img src="images/console.png" alt="Console view" width="900" />
</p>

- Console: stream logs, send commands, and use autocomplete.

<p align="center">
	<img src="images/performance.png" alt="Performance view" width="900" />
</p>

- Performance: monitor live server performance.

<p align="center">
	<img src="images/properties.png" alt="Properties view" width="900" />
</p>

- Properties: edit server settings with autosave.

<p align="center">
	<img src="images/files.png" alt="Files and worlds view" width="900" />
</p>

- Files: manage worlds, dimensions, and backups.

<p align="center">
	<img src="images/mods.png" alt="Mods view" width="900" />
</p>

- Mods: browse Modrinth, install mods, and manage dependencies.

<p align="center">
	<img src="images/connect.png" alt="Connect view" width="900" />
</p>

- Connect: configure Playit and manage whitelist/ban lists.

<p align="center">
	<img src="images/backups.png" alt="Backups view" width="900" />
</p>

- Backups: create and restore world backups safely.

## Project layout

- hosty.py starts the app
- hosty/ contains UI, backend logic, dialogs, and utilities