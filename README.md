# Orphaned Gear Checker

A tool to find inventory items that aren't used in your GearSwap lua files.

## Features

- **Analyze** your inventory CSV and GearSwap lua files
- **Identify** gear sitting in your wardrobes that isn't in any gearswap
- **Track progress** with a web-based checklist interface
- **Save/Load** your progress to continue later
- **Export** your checklist at any time
- **System tray icon** for easy server control (Windows)

## Quick Start

### Option 1: Run from Python

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
python orphan_checker_app.py
```

### Option 2: Build Standalone Executable

```bash
# Install dependencies including PyInstaller
pip install -r requirements.txt

# Generate icon files (first time only)
python create_icons.py

# Build the executable
python build.py

# Run the executable
./dist/OrphanedGearChecker.exe
```

The executable runs with a system tray icon - right-click to open browser or stop the server.

### Option 3: Use the Prebuilt Executable in Releases

I've also included a prebuilt executable for users to access that can be found in releases.

## Usage

1. **Start the application** - A browser window will open automatically
2. **Upload your files:**
   - Select your inventory CSV file (exported from the game)
   - Select your GearSwap lua files (can select multiple)
3. **Click "Analyze Files"** - The tool will compare your inventory to your gearswap
4. **Review the checklist** - Items are grouped by container (wardrobe, wardrobe2, etc.)
5. **Check off items** as you move them to storage or deal with them
6. **Export progress** at any time:
   - **JSON** - Can be reloaded into the app to continue later
   - **CSV** - For use in spreadsheets or other tools

## Export Formats

### JSON Export
The JSON export saves the full application state and can be reloaded to continue your progress later.

### CSV Export
The CSV export creates a spreadsheet-friendly file with columns:
- Container
- Item Name
- Augments
- Checked (Yes/No)
- Notes

## File Formats

### Inventory CSV

The inventory CSV should have these columns:
- `container_id` - Container number (8=wardrobe, 10=wardrobe2, etc.)
- `container_name` - Container name
- `item_id` - Item ID
- `item_name` - Item name
- `count` - Quantity

### GearSwap Lua

Standard GearSwap lua files with gear sets defined like:
```lua
sets.engaged = {
    head="Nyame Helm",
    body="Nyame Mail",
    -- etc.
}
```

## Notes

- Only checks wardrobes (not main inventory) since consumables would create noise
- Commented-out gear in lua files is still counted as "used"
- Progress is auto-saved to `orphan_checklist_state.json`

## Command Line Usage

You can also use the checker from the command line:

```bash
python gearswap_inventory_checker.py <lua_folder> <inventory.csv>
```

This will generate a text report (`orphaned_items_report.txt`) without the web interface.
