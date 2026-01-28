"""
Build script for Orphaned Gear Checker

Creates a standalone executable using PyInstaller.
"""

import subprocess
import sys
import os
from pathlib import Path


def build():
    """Build the executable."""
    print("=" * 50)
    print("Building Orphaned Gear Checker")
    print("=" * 50)
    print()
    
    # Ensure we're in the right directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Check for required files
    required_files = [
        'launcher.py',
        'orphan_checker_app.py',
        'gearswap_inventory_checker.py',
        'icon.ico',
        'tray_icon.png',
    ]
    
    missing = [f for f in required_files if not (script_dir / f).exists()]
    if missing:
        print("Missing required files:")
        for f in missing:
            print(f"  - {f}")
        print()
        print("Run 'python create_icons.py' to generate icon files.")
        sys.exit(1)
    
    # PyInstaller command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                          # Single executable
        "--name", "OrphanedGearChecker",      # Output name
        "--icon", "icon.ico",                 # Executable icon
        # Include required modules and assets
        "--add-data", f"orphan_checker_app.py{os.pathsep}.",
        "--add-data", f"gearswap_inventory_checker.py{os.pathsep}.",
        "--add-data", f"icon.ico{os.pathsep}.",
        "--add-data", f"tray_icon.png{os.pathsep}.",
        # Uvicorn hidden imports
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.lifespan.off",
        # Pystray hidden imports
        "--hidden-import", "pystray._win32",
        # PIL hidden imports
        "--hidden-import", "PIL._tkinter_finder",
        "--windowed",                         # No console (tray icon handles UI)
        "launcher.py",                        # Main entry point
    ]
    
    print("Running PyInstaller...")
    print()
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print()
        print("=" * 50)
        print("Build successful!")
        print()
        print("Executable location:")
        print(f"  {script_dir / 'dist' / 'OrphanedGearChecker.exe'}")
        print()
        print("To use:")
        print("  1. Copy OrphanedGearChecker.exe to any folder")
        print("  2. Double-click to run")
        print("  3. Browser opens automatically")
        print("  4. Use system tray icon to stop the server")
        print("=" * 50)
    else:
        print()
        print("Build failed!")
        sys.exit(1)


if __name__ == "__main__":
    build()
