"""
Orphaned Gear Checker - Standalone Launcher

This script launches the FastAPI server and opens the browser automatically.
Designed to be compiled with PyInstaller into a standalone executable.

Features:
- System tray icon with right-click menu
- Auto-opens browser on startup
- Clean shutdown from tray
"""

import os
import sys
import time
import threading
import webbrowser

# Server state
server_instance = None
shutdown_event = threading.Event()


def is_windowed_mode():
    """Check if running without a console window."""
    if sys.platform == 'win32':
        try:
            import ctypes
            return ctypes.windll.kernel32.GetConsoleWindow() == 0
        except:
            pass
    return sys.stdout is None or sys.stderr is None


def setup_output():
    """Configure output handling based on whether we have a console."""
    if is_windowed_mode() or sys.stdout is None:
        devnull = open(os.devnull, 'w', encoding='utf-8', errors='replace')
        sys.stdout = devnull
        sys.stderr = devnull
        return False
    return True


HAS_CONSOLE = setup_output()


def log(message):
    """Print a message only if console is available."""
    if HAS_CONSOLE:
        print(message)


def get_base_path():
    """Get the base path for resources, works both in dev and when frozen."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def setup_environment():
    """Set up the environment for the server."""
    base_path = get_base_path()
    os.chdir(base_path)
    
    if base_path not in sys.path:
        sys.path.insert(0, base_path)
    
    return base_path


def open_browser(url):
    """Open the browser to the given URL."""
    log(f">>> Opening browser to {url}")
    webbrowser.open(url)


def open_browser_delayed(url, delay=1.5):
    """Open the browser after a delay to let the server start."""
    def _open():
        time.sleep(delay)
        open_browser(url)
    
    thread = threading.Thread(target=_open, daemon=True)
    thread.start()


def create_icon_image():
    """Load or create the system tray icon image."""
    try:
        from PIL import Image
    except ImportError as e:
        log(f"Failed to import PIL: {e}")
        return None
    
    try:
        base_path = get_base_path()
        
        # Try to load the dedicated tray icon PNG first
        tray_icon_path = os.path.join(base_path, 'tray_icon.png')
        if os.path.exists(tray_icon_path):
            log(f"Loading tray icon from: {tray_icon_path}")
            return Image.open(tray_icon_path)
        
        # Fallback to ico file
        icon_path = os.path.join(base_path, 'icon.ico')
        if os.path.exists(icon_path):
            log(f"Loading icon from: {icon_path}")
            img = Image.open(icon_path)
            return img.resize((64, 64), Image.LANCZOS)
        
        log("No icon files found, creating fallback...")
        
        # Fallback: create a simple broom-like icon
        from PIL import ImageDraw
        size = 64
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Simple design with FFXI colors
        gold = (201, 162, 39, 255)
        dark = (20, 24, 30, 255)
        tan = (180, 150, 100, 255)
        
        # Outer ring
        draw.ellipse([2, 2, size-2, size-2], fill=gold)
        draw.ellipse([6, 6, size-6, size-6], fill=dark)
        
        # Simple broom shape
        draw.line([(20, 44), (44, 20)], fill=tan, width=4)
        draw.ellipse([14, 38, 26, 50], fill=tan)
        
        return img
        
    except Exception as e:
        log(f"Error creating icon: {e}")
        return None


def request_shutdown():
    """Request the server to shut down."""
    global server_instance
    log("\n>>> Shutdown requested...")
    shutdown_event.set()
    if server_instance:
        server_instance.should_exit = True


def setup_tray_icon(url):
    """Set up the system tray icon with menu."""
    try:
        import pystray
        from pystray import MenuItem as item
    except ImportError as e:
        log(f"Note: pystray not available ({e}), running without system tray icon")
        return None
    
    icon_image = create_icon_image()
    if icon_image is None:
        log("Note: Could not create icon image, running without system tray icon")
        return None
    
    def on_open_browser(icon, item):
        open_browser(url)
    
    def on_stop_server(icon, item):
        request_shutdown()
        icon.stop()
    
    menu = pystray.Menu(
        item('Open in Browser', on_open_browser, default=True),
        item('Stop Server', on_stop_server)
    )
    
    icon = pystray.Icon(
        "OrphanedGearChecker",
        icon_image,
        "Orphaned Gear Checker",
        menu
    )
    
    return icon


def run_tray_icon(icon):
    """Run the tray icon in a separate thread."""
    if icon:
        def run():
            icon.run()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread
    return None


def main():
    """Main entry point."""
    global server_instance
    
    log("=" * 60)
    log("  Orphaned Gear Checker")
    log("=" * 60)
    log("")
    
    # Set up environment
    base_path = setup_environment()
    log(f"Working directory: {base_path}")
    log("")
    
    # Server configuration
    host = "127.0.0.1"
    port = 8050
    url = f"http://{host}:{port}"
    
    log(f"Starting server at {url}")
    log("Use the system tray icon to stop the server.")
    log("-" * 60)
    
    # Set up system tray icon
    tray_icon = setup_tray_icon(url)
    run_tray_icon(tray_icon)
    
    # Schedule browser open
    open_browser_delayed(url)
    
    # Import and run uvicorn
    try:
        import uvicorn
        from orphan_checker_app import app
        
        config_kwargs = dict(
            app=app,
            host=host,
            port=port,
            log_level="warning" if not HAS_CONSOLE else "info",
            access_log=HAS_CONSOLE,
        )
        if not HAS_CONSOLE:
            config_kwargs['log_config'] = None
        
        config = uvicorn.Config(**config_kwargs)
        server_instance = uvicorn.Server(config)
        
        server_instance.run()
        
    except KeyboardInterrupt:
        log("\n\nServer stopped by user.")
    except Exception as e:
        log(f"\nError starting server: {e}")
        if HAS_CONSOLE:
            import traceback
            traceback.print_exc()
            print("\nPress Enter to exit...")
            input()
        sys.exit(1)
    finally:
        if tray_icon:
            try:
                tray_icon.stop()
            except:
                pass
    
    log("\nServer stopped. Goodbye!")


if __name__ == "__main__":
    main()
