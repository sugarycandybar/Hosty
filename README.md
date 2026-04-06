# Hosty

Hosty is a desktop app for making and managing Minecraft Fabric servers.

It helps you set up a server, run it, and manage it without juggling lots of separate tools. The goal is to keep server management simple and friendly. 🙂

## What Hosty does

- Creates Fabric servers with a guided flow
- Starts and stops your servers
- Shows server console output
- Helps manage server settings and files

## Run the app

### Windows

1. Install dependencies:
	python -m pip install -r requirements-windows.txt
2. Run Hosty:
	python hosty.py

### Linux

1. Install GTK4/libadwaita and PyGObject system packages.
2. In this folder, run:
	python3 hosty.py

## Project layout

- hosty.py starts the app
- hosty/ contains the app code (UI, backend, dialogs, and utilities)

Hosty is built for people who want a clean way to host Fabric servers locally. 🚀

## Showcase

Screenshots — Hosty in action:

<p align="center">
  <img src="images/console.png" alt="Console view" width="800" />
</p>

- **Console:** View server output, chat, and run server commands.

<p align="center">
  <img src="images/mods.png" alt="Mods view" width="800" />
</p>

- **Mods:** Manage installed mods and downloads.

<p align="center">
  <img src="images/properties.png" alt="Properties view" width="800" />
</p>

- **Properties:** Edit `server.properties` and configuration options.