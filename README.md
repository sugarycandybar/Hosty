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

## Images

Included image assets:

- [Hosty/images/console.png](images/console.png)
- [Hosty/images/mods.png](images/mods.png)
- [Hosty/images/properties.png](images/properties.png)