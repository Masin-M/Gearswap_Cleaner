"""
Orphaned Gear Checker - FastAPI Web Application

A web-based checklist for managing orphaned inventory items
that aren't referenced in GearSwap lua files.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from gearswap_inventory_checker import (
    LuaItemExtractor,
    InventoryLoader,
    compare_inventory_to_gearswap,
    InventoryItem,
    EQUIPPABLE_CONTAINERS,
)


# =============================================================================
# Data Models
# =============================================================================

class ChecklistItem(BaseModel):
    """A single item in the checklist."""
    item_name: str
    container_name: str
    augments: str = ""
    checked: bool = False
    notes: str = ""
    
    @property
    def display_name(self) -> str:
        """Get display name with augments."""
        if self.augments:
            aug_display = self.augments[:60] + "..." if len(self.augments) > 60 else self.augments
            return f"{self.item_name} [{aug_display}]"
        return self.item_name


class ChecklistState(BaseModel):
    """Complete checklist state."""
    created_at: str
    updated_at: str
    inventory_file: str
    lua_files: List[str]
    total_items: int
    checked_count: int
    items: Dict[str, ChecklistItem]  # key is "container:item_name"


class UpdateItemRequest(BaseModel):
    """Request to update an item's checked state."""
    item_key: str
    checked: bool
    notes: Optional[str] = None


# =============================================================================
# Application State
# =============================================================================

class AppState:
    """Manages application state."""
    
    def __init__(self):
        self.checklist: Optional[ChecklistState] = None
        self.save_file: str = "orphan_checklist_state.json"
        self.load_state()
    
    def load_state(self):
        """Load state from file if it exists."""
        if os.path.exists(self.save_file):
            try:
                with open(self.save_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Reconstruct ChecklistState from dict
                    items = {k: ChecklistItem(**v) for k, v in data.get('items', {}).items()}
                    self.checklist = ChecklistState(
                        created_at=data.get('created_at', ''),
                        updated_at=data.get('updated_at', ''),
                        inventory_file=data.get('inventory_file', ''),
                        lua_files=data.get('lua_files', []),
                        total_items=data.get('total_items', 0),
                        checked_count=data.get('checked_count', 0),
                        items=items,
                    )
                    print(f"Loaded existing checklist with {len(items)} items")
            except Exception as e:
                print(f"Failed to load state: {e}")
                self.checklist = None
    
    def save_state(self):
        """Save current state to file."""
        if self.checklist is None:
            return
        
        self.checklist.updated_at = datetime.now().isoformat()
        self.checklist.checked_count = sum(
            1 for item in self.checklist.items.values() if item.checked
        )
        
        # Convert to dict for JSON serialization
        data = {
            'created_at': self.checklist.created_at,
            'updated_at': self.checklist.updated_at,
            'inventory_file': self.checklist.inventory_file,
            'lua_files': self.checklist.lua_files,
            'total_items': self.checklist.total_items,
            'checked_count': self.checklist.checked_count,
            'items': {k: v.model_dump() for k, v in self.checklist.items.items()},
        }
        
        with open(self.save_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    
    def create_checklist(
        self,
        orphaned_items: List[InventoryItem],
        inventory_file: str,
        lua_files: List[str]
    ):
        """Create a new checklist from orphaned items."""
        items = {}
        for item in orphaned_items:
            # Use a unique key that includes augments for disambiguation
            aug_hash = hash(item.augments) if item.augments else 0
            key = f"{item.container_name}:{item.item_name}:{aug_hash}"
            items[key] = ChecklistItem(
                item_name=item.item_name,
                container_name=item.container_name,
                augments=item.augments,
                checked=False,
                notes="",
            )
        
        self.checklist = ChecklistState(
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            inventory_file=inventory_file,
            lua_files=lua_files,
            total_items=len(items),
            checked_count=0,
            items=items,
        )
        self.save_state()
    
    def update_item(self, item_key: str, checked: bool, notes: Optional[str] = None):
        """Update a single item's state."""
        if self.checklist is None:
            raise ValueError("No checklist loaded")
        
        if item_key not in self.checklist.items:
            raise ValueError(f"Item not found: {item_key}")
        
        self.checklist.items[item_key].checked = checked
        if notes is not None:
            self.checklist.items[item_key].notes = notes
        
        self.save_state()


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(title="Orphaned Gear Checker", version="1.0.0")
state = AppState()

# Path to the favicon file (same directory as the script)
FAVICON_PATH = Path(__file__).parent / "icon.ico"


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the favicon."""
    if FAVICON_PATH.exists():
        return FileResponse(FAVICON_PATH, media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main page."""
    return get_html_template()


@app.get("/api/status")
async def get_status():
    """Get current application status."""
    if state.checklist is None:
        return {
            "has_checklist": False,
            "message": "No checklist loaded. Upload files to analyze."
        }
    
    return {
        "has_checklist": True,
        "inventory_file": state.checklist.inventory_file,
        "lua_files": state.checklist.lua_files,
        "total_items": state.checklist.total_items,
        "checked_count": state.checklist.checked_count,
        "created_at": state.checklist.created_at,
        "updated_at": state.checklist.updated_at,
    }


@app.get("/api/checklist")
async def get_checklist():
    """Get the full checklist."""
    if state.checklist is None:
        raise HTTPException(status_code=404, detail="No checklist loaded")
    
    # Group items by container
    by_container: Dict[str, List[dict]] = {}
    for key, item in state.checklist.items.items():
        container = item.container_name
        if container not in by_container:
            by_container[container] = []
        
        # Build display name
        if item.augments:
            aug_display = item.augments[:60] + "..." if len(item.augments) > 60 else item.augments
            display_name = f"{item.item_name} [{aug_display}]"
        else:
            display_name = item.item_name
        
        by_container[container].append({
            "key": key,
            "item_name": item.item_name,
            "display_name": display_name,
            "augments": item.augments,
            "checked": item.checked,
            "notes": item.notes,
        })
    
    # Sort items within each container
    for container in by_container:
        by_container[container].sort(key=lambda x: x['item_name'].lower())
    
    return {
        "total_items": state.checklist.total_items,
        "checked_count": state.checklist.checked_count,
        "by_container": by_container,
    }


@app.post("/api/update-item")
async def update_item(request: UpdateItemRequest):
    """Update a single item's checked state."""
    if state.checklist is None:
        raise HTTPException(status_code=404, detail="No checklist loaded")
    
    try:
        state.update_item(request.item_key, request.checked, request.notes)
        return {"success": True, "checked_count": state.checklist.checked_count}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/analyze")
async def analyze_files(
    inventory_csv: UploadFile = File(...),
    lua_files: List[UploadFile] = File(...)
):
    """Analyze uploaded files and create checklist."""
    # Save uploaded files temporarily
    temp_dir = Path("temp_uploads")
    temp_dir.mkdir(exist_ok=True)
    
    try:
        # Save inventory CSV
        inv_path = temp_dir / inventory_csv.filename
        with open(inv_path, 'wb') as f:
            content = await inventory_csv.read()
            f.write(content)
        
        # Save lua files
        lua_paths = []
        for lua_file in lua_files:
            lua_path = temp_dir / lua_file.filename
            with open(lua_path, 'wb') as f:
                content = await lua_file.read()
                f.write(content)
            lua_paths.append(str(lua_path))
        
        # Extract items from lua files
        extractor = LuaItemExtractor()
        for lua_path in lua_paths:
            try:
                extractor.extract_from_file(lua_path)
            except Exception as e:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Error parsing Lua file '{Path(lua_path).name}': {str(e)}"
                )
        
        # Load inventory
        loader = InventoryLoader()
        try:
            loader.load_from_csv(str(inv_path), equip_only=True)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Error parsing inventory CSV: {str(e)}"
            )
        
        # Compare
        orphaned = compare_inventory_to_gearswap(loader.items, extractor.items)
        
        # Create checklist
        state.create_checklist(
            orphaned_items=orphaned,
            inventory_file=inventory_csv.filename,
            lua_files=[f.filename for f in lua_files],
        )
        
        return {
            "success": True,
            "gearswap_items": len(extractor.items),
            "inventory_items": len(loader.items),
            "orphaned_items": len(orphaned),
        }
    
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Catch any other unexpected errors
        import traceback
        traceback.print_exc()  # Print to server console for debugging
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error during analysis: {str(e)}"
        )
        
    finally:
        # Clean up temp files
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


@app.get("/api/export")
async def export_checklist():
    """Export current checklist state as JSON file."""
    if state.checklist is None:
        raise HTTPException(status_code=404, detail="No checklist loaded")
    
    # Generate export filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_file = f"orphan_checklist_export_{timestamp}.json"
    
    # Save to file
    state.save_state()
    
    return FileResponse(
        state.save_file,
        media_type="application/json",
        filename=export_file,
    )


@app.get("/api/export-csv")
async def export_checklist_csv():
    """Export current checklist state as CSV file."""
    if state.checklist is None:
        raise HTTPException(status_code=404, detail="No checklist loaded")
    
    import csv
    import io
    
    # Generate export filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_file = f"orphan_checklist_{timestamp}.csv"
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Container', 'Item Name', 'Augments', 'Checked', 'Notes'])
    
    # Sort items by container then by name
    sorted_items = sorted(
        state.checklist.items.values(),
        key=lambda x: (x.container_name, x.item_name.lower())
    )
    
    # Write items
    for item in sorted_items:
        writer.writerow([
            item.container_name,
            item.item_name,
            item.augments,
            'Yes' if item.checked else 'No',
            item.notes,
        ])
    
    # Create response
    csv_content = output.getvalue()
    output.close()
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={export_file}"}
    )


@app.post("/api/load-state")
async def load_state_file(state_file: UploadFile = File(...)):
    """Load a previously exported state file."""
    try:
        content = await state_file.read()
        data = json.loads(content.decode('utf-8'))
        
        # Validate structure
        required_keys = ['items', 'total_items', 'inventory_file']
        for key in required_keys:
            if key not in data:
                raise ValueError(f"Missing required key: {key}")
        
        # Save and reload
        with open(state.save_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        
        state.load_state()
        
        return {
            "success": True,
            "total_items": state.checklist.total_items,
            "checked_count": state.checklist.checked_count,
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load state: {e}")


@app.post("/api/clear")
async def clear_checklist():
    """Clear the current checklist."""
    state.checklist = None
    if os.path.exists(state.save_file):
        os.remove(state.save_file)
    return {"success": True}


# =============================================================================
# HTML Template
# =============================================================================

def get_html_template() -> str:
    """Return the HTML template for the web interface."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Orphaned Gear Checker</title>
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        /* Base Reset */
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            scrollbar-width: thin;
            scrollbar-color: #1e2630 #0a0e14;
        }
        
        *::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        
        *::-webkit-scrollbar-track {
            background: #0a0e14;
        }
        
        *::-webkit-scrollbar-thumb {
            background: #1e2630;
            border-radius: 4px;
        }
        
        *::-webkit-scrollbar-thumb:hover {
            background: #2a3640;
        }
        
        ::selection {
            background: rgba(201, 162, 39, 0.3);
            color: #fff;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #0a0e14;
            color: #e8e6e3;
            min-height: 100vh;
            padding: 20px;
        }
        
        /* Subtle grain texture overlay */
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 400 400' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E");
            opacity: 0.02;
            pointer-events: none;
            z-index: 1000;
        }
        
        /* Gold accent line at top of page */
        body::after {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, 
                transparent, 
                #8b7119 20%, 
                #c9a227 50%, 
                #8b7119 80%, 
                transparent
            );
            z-index: 1001;
        }
        
        .container {
            max-width: 900px;
            margin: 0 auto;
            position: relative;
        }
        
        h1 {
            text-align: center;
            margin-bottom: 10px;
            color: #c9a227;
            font-family: 'Cinzel', serif;
            font-weight: 600;
            letter-spacing: 0.05em;
        }
        
        h2 {
            font-family: 'Cinzel', serif;
            color: #c9a227;
        }
        
        .subtitle {
            text-align: center;
            color: #8b9298;
            margin-bottom: 30px;
        }
        
        .status-bar {
            background: #12171f;
            border: 1px solid #1e2630;
            padding: 15px 20px;
            border-radius: 0.5rem;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        }
        
        .progress {
            font-size: 1.2em;
            color: #c9a227;
            font-family: 'Cinzel', serif;
        }
        
        .btn {
            background: #1e2630;
            color: #e8e6e3;
            border: 1px solid #2a3640;
            padding: 0.625rem 1.25rem;
            border-radius: 0.375rem;
            cursor: pointer;
            font-size: 14px;
            transition: background 0.2s, border-color 0.2s, transform 0.1s;
        }
        
        .btn:hover {
            background: #2a3640;
            border-color: #c9a227;
            transform: translateY(-1px);
        }
        
        .btn:active {
            transform: translateY(0);
        }
        
        .btn-primary {
            background: linear-gradient(135deg, #c9a227 0%, #8b7119 100%);
            color: #0a0e14;
            font-weight: 600;
            border: none;
            font-family: 'Cinzel', serif;
            letter-spacing: 0.025em;
        }
        
        .btn-primary:hover {
            box-shadow: 0 4px 12px rgba(201, 162, 39, 0.3);
        }
        
        .btn-danger {
            background: #4a1a2a;
            border-color: #7a2a3a;
            color: #f87171;
        }
        
        .btn-danger:hover {
            background: #5a2a3a;
            border-color: #f87171;
        }
        
        .upload-section {
            background: #12171f;
            border: 1px solid #1e2630;
            padding: 30px;
            border-radius: 0.5rem;
            margin-bottom: 20px;
            text-align: center;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        }
        
        .upload-section h2 {
            margin-bottom: 20px;
        }
        
        .file-input-group {
            margin: 15px 0;
            text-align: left;
        }
        
        .file-input-group label {
            display: block;
            margin-bottom: 5px;
            color: #8b9298;
            font-size: 0.875rem;
        }
        
        .file-input-group input[type="file"] {
            width: 100%;
            padding: 0.5rem 0.75rem;
            background: #0a0e14;
            border: 1px solid #1e2630;
            border-radius: 0.375rem;
            color: #e8e6e3;
            font-size: 0.875rem;
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        
        .file-input-group input[type="file"]:focus {
            outline: none;
            border-color: #c9a227;
            box-shadow: 0 0 0 2px rgba(201, 162, 39, 0.2);
        }
        
        .container-section {
            background: #12171f;
            border: 1px solid #1e2630;
            border-radius: 0.5rem;
            margin-bottom: 15px;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
        }
        
        .container-header {
            background: #1e2630;
            padding: 15px 20px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #2a3640;
            transition: background 0.2s;
        }
        
        .container-header:hover {
            background: #2a3640;
        }
        
        .container-name {
            font-weight: 600;
            text-transform: uppercase;
            color: #c9a227;
            font-family: 'Cinzel', serif;
            letter-spacing: 0.05em;
        }
        
        .container-count {
            color: #8b9298;
            font-size: 0.9em;
        }
        
        .container-items {
            padding: 10px 20px;
            display: none;
            background: #0d1117;
        }
        
        .container-items.expanded {
            display: block;
        }
        
        .item-row {
            display: flex;
            align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid #1e2630;
            transition: background 0.15s;
        }
        
        .item-row:hover {
            background: rgba(201, 162, 39, 0.05);
        }
        
        .item-row:last-child {
            border-bottom: none;
        }
        
        .item-row.checked {
            opacity: 0.4;
        }
        
        .item-row.checked .item-name {
            text-decoration: line-through;
            color: #8b9298;
        }
        
        .item-checkbox {
            width: 18px;
            height: 18px;
            margin-right: 15px;
            cursor: pointer;
            accent-color: #c9a227;
        }
        
        .item-name {
            flex: 1;
            word-break: break-word;
            color: #e8e6e3;
        }
        
        .item-augments {
            color: #8b9298;
            font-size: 0.85em;
            margin-left: 0.5em;
        }
        
        .buttons-row {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        
        .hidden {
            display: none !important;
        }
        
        .loading {
            text-align: center;
            padding: 40px;
            color: #8b9298;
        }
        
        .loading-spinner {
            width: 40px;
            height: 40px;
            border: 3px solid #1e2630;
            border-top-color: #c9a227;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 15px;
        }
        
        .message {
            padding: 15px;
            border-radius: 0.375rem;
            margin-bottom: 20px;
            border: 1px solid;
            animation: fadeIn 0.3s ease-out;
        }
        
        .message.success {
            background: rgba(74, 222, 128, 0.1);
            border-color: #2a5a3a;
            color: #4ade80;
        }
        
        .message.error {
            background: rgba(248, 113, 113, 0.1);
            border-color: #5a2a3a;
            color: #f87171;
        }
        
        .divider {
            color: #8b9298;
            margin: 20px 0;
        }
        
        /* Animations */
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        
        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        
        @keyframes slideUp {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .fade-in {
            animation: fadeIn 0.3s ease-out;
        }
        
        .slide-up {
            animation: slideUp 0.3s ease-out;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚔️ Orphaned Gear Checker</h1>
        <p class="subtitle">Find inventory items not used in your GearSwap lua files</p>
        
        <div id="message" class="message hidden"></div>
        
        <!-- Upload Section -->
        <div id="upload-section" class="upload-section slide-up">
            <h2>Upload Files to Analyze</h2>
            <form id="upload-form">
                <div class="file-input-group">
                    <label>Inventory CSV File:</label>
                    <input type="file" id="inventory-file" accept=".csv" required>
                </div>
                <div class="file-input-group">
                    <label>GearSwap Lua Files (select multiple):</label>
                    <input type="file" id="lua-files" accept=".lua" multiple required>
                </div>
                <br>
                <button type="submit" class="btn btn-primary">⚔️ Analyze Files</button>
            </form>
            <p class="divider">— or —</p>
            <div class="file-input-group">
                <label>Load Previous Session:</label>
                <input type="file" id="load-state-file" accept=".json">
            </div>
        </div>
        
        <!-- Checklist Section -->
        <div id="checklist-section" class="hidden">
            <div class="status-bar slide-up">
                <div class="progress">
                    ✓ <span id="checked-count">0</span> / <span id="total-count">0</span> items processed
                </div>
                <div class="buttons-row">
                    <button class="btn" onclick="exportChecklist()">📥 Export JSON</button>
                    <button class="btn" onclick="exportChecklistCSV()">📊 Export CSV</button>
                    <button class="btn btn-danger" onclick="clearChecklist()">🗑️ Start Over</button>
                </div>
            </div>
            
            <div id="checklist-items">
                <div class="loading">
                    <div class="loading-spinner"></div>
                    Loading checklist...
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // State
        let checklistData = null;
        
        // Escape HTML to prevent XSS
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            checkStatus();
            
            // Upload form handler
            document.getElementById('upload-form').addEventListener('submit', handleUpload);
            
            // Load state file handler
            document.getElementById('load-state-file').addEventListener('change', handleLoadState);
        });
        
        async function checkStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                
                if (data.has_checklist) {
                    document.getElementById('upload-section').classList.add('hidden');
                    document.getElementById('checklist-section').classList.remove('hidden');
                    loadChecklist();
                } else {
                    document.getElementById('upload-section').classList.remove('hidden');
                    document.getElementById('checklist-section').classList.add('hidden');
                }
            } catch (error) {
                showMessage('Failed to check status: ' + error, 'error');
            }
        }
        
        async function handleUpload(e) {
            e.preventDefault();
            
            const inventoryFile = document.getElementById('inventory-file').files[0];
            const luaFiles = document.getElementById('lua-files').files;
            
            if (!inventoryFile || luaFiles.length === 0) {
                showMessage('Please select all required files', 'error');
                return;
            }
            
            const formData = new FormData();
            formData.append('inventory_csv', inventoryFile);
            for (const file of luaFiles) {
                formData.append('lua_files', file);
            }
            
            try {
                showMessage('Analyzing files...', 'success');
                const response = await fetch('/api/analyze', {
                    method: 'POST',
                    body: formData,
                });
                
                if (!response.ok) {
                    // Try to get error details from response
                    const errorText = await response.text();
                    console.error('Server error:', response.status, errorText);
                    showMessage(`Server error (${response.status}): ${errorText.substring(0, 100)}`, 'error');
                    return;
                }
                
                const data = await response.json();
                
                if (data.success) {
                    showMessage(`Found ${data.orphaned_items} orphaned items!`, 'success');
                    checkStatus();
                } else {
                    showMessage('Analysis failed: ' + (data.detail || 'Unknown error'), 'error');
                }
            } catch (error) {
                console.error('Upload error:', error);
                showMessage('Upload failed: ' + error, 'error');
            }
        }
        
        async function handleLoadState(e) {
            const file = e.target.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append('state_file', file);
            
            try {
                const response = await fetch('/api/load-state', {
                    method: 'POST',
                    body: formData,
                });
                
                if (!response.ok) {
                    const errorText = await response.text();
                    console.error('Server error:', response.status, errorText);
                    showMessage(`Failed to load state (${response.status}): ${errorText.substring(0, 100)}`, 'error');
                    return;
                }
                
                const data = await response.json();
                
                if (data.success) {
                    showMessage('Loaded previous session!', 'success');
                    checkStatus();
                }
            } catch (error) {
                console.error('Load state error:', error);
                showMessage('Failed to load state: ' + error, 'error');
            }
        }
        
        async function loadChecklist() {
            try {
                const response = await fetch('/api/checklist');
                
                if (!response.ok) {
                    const errorText = await response.text();
                    console.error('Server error:', response.status, errorText);
                    showMessage(`Failed to load checklist (${response.status}): ${errorText.substring(0, 100)}`, 'error');
                    return;
                }
                
                checklistData = await response.json();
                
                document.getElementById('checked-count').textContent = checklistData.checked_count;
                document.getElementById('total-count').textContent = checklistData.total_items;
                
                renderChecklist();
            } catch (error) {
                console.error('Load checklist error:', error);
                showMessage('Failed to load checklist: ' + error, 'error');
            }
        }
        
        function renderChecklist() {
            const container = document.getElementById('checklist-items');
            container.innerHTML = '';
            
            // Sort containers
            const containerOrder = ['wardrobe', 'wardrobe2', 'wardrobe3', 'wardrobe4', 
                                   'wardrobe5', 'wardrobe6', 'wardrobe7', 'wardrobe8'];
            
            const sortedContainers = Object.keys(checklistData.by_container).sort((a, b) => {
                const aIndex = containerOrder.indexOf(a);
                const bIndex = containerOrder.indexOf(b);
                if (aIndex === -1 && bIndex === -1) return a.localeCompare(b);
                if (aIndex === -1) return 1;
                if (bIndex === -1) return -1;
                return aIndex - bIndex;
            });
            
            sortedContainers.forEach((containerName, index) => {
                const items = checklistData.by_container[containerName];
                const checkedInContainer = items.filter(i => i.checked).length;
                
                const section = document.createElement('div');
                section.className = 'container-section slide-up';
                section.style.animationDelay = `${index * 0.05}s`;
                section.innerHTML = `
                    <div class="container-header" onclick="toggleContainer(this)">
                        <span class="container-name">${containerName}</span>
                        <span class="container-count">${checkedInContainer}/${items.length} done</span>
                    </div>
                    <div class="container-items">
                        ${items.map(item => `
                            <div class="item-row ${item.checked ? 'checked' : ''}" data-key="${item.key}">
                                <input type="checkbox" class="item-checkbox" 
                                       ${item.checked ? 'checked' : ''} 
                                       onchange="toggleItem('${item.key}', this.checked)">
                                <span class="item-name">${escapeHtml(item.display_name)}</span>
                            </div>
                        `).join('')}
                    </div>
                `;
                container.appendChild(section);
            });
        }
        
        function toggleContainer(header) {
            const items = header.nextElementSibling;
            items.classList.toggle('expanded');
        }
        
        async function toggleItem(key, checked) {
            try {
                const response = await fetch('/api/update-item', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({item_key: key, checked: checked}),
                });
                
                if (!response.ok) {
                    const errorText = await response.text();
                    console.error('Server error:', response.status, errorText);
                    showMessage(`Failed to update item (${response.status})`, 'error');
                    return;
                }
                
                const data = await response.json();
                
                if (data.success) {
                    document.getElementById('checked-count').textContent = data.checked_count;
                    
                    // Update row styling
                    const row = document.querySelector(`.item-row[data-key="${key}"]`);
                    if (row) {
                        row.classList.toggle('checked', checked);
                    }
                    
                    // Update container count
                    const section = row.closest('.container-section');
                    const containerItems = section.querySelectorAll('.item-row');
                    const containerChecked = section.querySelectorAll('.item-row.checked').length;
                    section.querySelector('.container-count').textContent = 
                        `${containerChecked}/${containerItems.length} done`;
                }
            } catch (error) {
                console.error('Toggle item error:', error);
                showMessage('Failed to update item: ' + error, 'error');
            }
        }
        
        async function exportChecklist() {
            window.location.href = '/api/export';
        }
        
        async function exportChecklistCSV() {
            window.location.href = '/api/export-csv';
        }
        
        async function clearChecklist() {
            if (!confirm('Are you sure you want to start over? All progress will be lost.')) {
                return;
            }
            
            try {
                await fetch('/api/clear', {method: 'POST'});
                checkStatus();
            } catch (error) {
                showMessage('Failed to clear: ' + error, 'error');
            }
        }
        
        function showMessage(text, type) {
            const msg = document.getElementById('message');
            msg.textContent = text;
            msg.className = `message ${type}`;
            msg.classList.remove('hidden');
            
            setTimeout(() => {
                msg.classList.add('hidden');
            }, 5000);
        }
    </script>
</body>
</html>'''


# =============================================================================
# Entry Point
# =============================================================================

def main():
    """Run the application."""
    import webbrowser
    import threading
    
    # Open browser after a short delay
    def open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:8050")
    
    threading.Thread(target=open_browser, daemon=True).start()
    
    print("=" * 50)
    print("Orphaned Gear Checker")
    print("=" * 50)
    print()
    print("Starting web server...")
    print("Opening browser at http://127.0.0.1:8050")
    print()
    print("Press Ctrl+C to stop the server and exit.")
    print()
    
    uvicorn.run(app, host="127.0.0.1", port=8050, log_level="warning")


if __name__ == "__main__":
    main()
