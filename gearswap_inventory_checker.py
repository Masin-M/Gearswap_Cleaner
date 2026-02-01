"""
GearSwap Inventory Checker

Compares inventory items against GearSwap lua files to find
"orphaned" gear that isn't being used in any gearswap.

Usage:
    python gearswap_inventory_checker.py <lua_folder> <inventory_csv>
"""

import re
import csv
import os
from pathlib import Path
from typing import Set, Dict, List, Tuple, Optional, FrozenSet
from dataclasses import dataclass, field


# Equippable container IDs (wardrobes only - excludes main inventory due to consumables)
EQUIPPABLE_CONTAINERS = {
    8: "wardrobe",
    10: "wardrobe2",
    11: "wardrobe3",
    12: "wardrobe4",
    13: "wardrobe5",
    14: "wardrobe6",
    15: "wardrobe7",
    16: "wardrobe8",
}


def normalize_augments(augments_str: str) -> FrozenSet[str]:
    """
    Normalize augments string for comparison.
    
    Handles both CSV format (semicolon-separated) and Lua format (comma-separated in braces).
    Returns a frozenset of normalized augment strings.
    """
    if not augments_str:
        return frozenset()
    
    # Clean up the string
    augments_str = augments_str.strip()
    
    # Remove outer braces if present (lua format)
    if augments_str.startswith('{') and augments_str.endswith('}'):
        augments_str = augments_str[1:-1]
    
    # Determine separator (comma for lua, semicolon for CSV)
    if ';' in augments_str:
        # CSV format - semicolon separated
        parts = augments_str.split(';')
    else:
        # Lua format - comma separated, but need to handle quoted strings
        parts = []
        current = ""
        in_quotes = False
        quote_char = None
        for char in augments_str:
            if char in "\"'" and not in_quotes:
                in_quotes = True
                quote_char = char
            elif char == quote_char and in_quotes:
                in_quotes = False
                quote_char = None
            elif char == ',' and not in_quotes:
                if current.strip():
                    parts.append(current.strip())
                current = ""
                continue
            current += char
        if current.strip():
            parts.append(current.strip())
    
    # Normalize each augment
    normalized = set()
    for part in parts:
        # Strip quotes and whitespace
        part = part.strip().strip("'\"")
        # Replace double-double quotes with single (CSV escaping)
        part = part.replace('""', '"')
        # Skip empty or system augments
        if not part or part.startswith('System:'):
            continue
        normalized.add(part.lower())
    
    return frozenset(normalized)


@dataclass(frozen=True)
class LuaGearItem:
    """Represents an item extracted from a lua file."""
    name: str
    augments: str = ""
    
    @property
    def normalized_augments(self) -> FrozenSet[str]:
        """Get normalized augments for comparison."""
        return normalize_augments(self.augments)
    
    @property
    def name_lower(self) -> str:
        return self.name.lower()


@dataclass
class InventoryItem:
    """Represents an item from the inventory CSV."""
    item_id: int
    item_name: str
    container_id: int
    container_name: str
    augments: str = ""
    count: int = 1
    item_name_log: str = ""  # Full name from log (e.g., "Sacred Kindred's crest" vs "S. Kindred Crest")
    
    def __hash__(self):
        return hash((self.item_id, self.container_id, self.augments))
    
    @property
    def display_name(self) -> str:
        """Get display name with augments if present."""
        if self.augments:
            # Truncate long augment strings for display
            aug_display = self.augments[:60] + "..." if len(self.augments) > 60 else self.augments
            return f"{self.item_name} [{aug_display}]"
        return self.item_name
    
    @property 
    def normalized_augments(self) -> FrozenSet[str]:
        """Get normalized augments for comparison."""
        return normalize_augments(self.augments)
    
    @property
    def name_lower(self) -> str:
        return self.item_name.lower()
    
    @property
    def name_log_lower(self) -> str:
        return self.item_name_log.lower() if self.item_name_log else ""


class LuaItemExtractor:
    """
    Extracts item names and augments from GearSwap Lua files.
    
    Looks for items inside {} brackets in patterns like:
    - slot="Item Name"
    - slot={ name="Item Name", augments={...} }
    - gear.Variable = { name="Item Name", augments={...} }
    - gear.Variable = "Item Name"
    """
    
    # Pattern for augmented item block: { name="Item Name", augments={...} }
    PATTERN_AUGMENTED_BLOCK = re.compile(
        r'\{\s*name\s*=\s*["\']([^"\']+)["\']\s*,\s*augments\s*=\s*\{([^}]*)\}\s*\}',
        re.IGNORECASE
    )
    
    # Pattern for simple item assignment: slot="Item Name" (double quotes)
    PATTERN_SIMPLE_DOUBLE = re.compile(r'([a-z_][a-z0-9_]*)\s*=\s*"([^"]+)"', re.IGNORECASE)
    
    # Pattern for simple item assignment: slot='Item Name' (single quotes)
    PATTERN_SIMPLE_SINGLE = re.compile(r"([a-z_][a-z0-9_]*)\s*=\s*'([^']+)'", re.IGNORECASE)

    def __init__(self):
        self.items: Set[LuaGearItem] = set()
        self.items_by_file: Dict[str, Set[LuaGearItem]] = {}
    
    def extract_from_file(self, filepath: str) -> Set[LuaGearItem]:
        """Extract all items from a lua file."""
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        items = self._extract_items(content)
        self.items.update(items)
        self.items_by_file[filepath] = items
        return items
    
    def extract_from_folder(self, folder_path: str) -> Set[LuaGearItem]:
        """Extract items from all lua files in a folder."""
        folder = Path(folder_path)
        
        for lua_file in folder.glob("*.lua"):
            self.extract_from_file(str(lua_file))
        
        return self.items
    
    def _extract_items(self, content: str) -> Set[LuaGearItem]:
        """Extract items from lua content."""
        items = set()
        found_augmented = set()  # Track names that have augmented versions
        
        # First pass: find all augmented items
        for match in self.PATTERN_AUGMENTED_BLOCK.finditer(content):
            item_name = match.group(1).strip()
            augments_raw = match.group(2).strip()
            if self._is_valid_item_name(item_name):
                items.add(LuaGearItem(name=item_name, augments=augments_raw))
                found_augmented.add(item_name.lower())
        
        # Second pass: find simple items (not already found as augmented)
        for match in self.PATTERN_SIMPLE_DOUBLE.finditer(content):
            slot_name = match.group(1).lower()
            item_name = match.group(2).strip()
            
            # Skip if this is the 'name' field of an augmented item
            if slot_name == 'name':
                continue
                
            if self._is_valid_item_name(item_name):
                # Add as non-augmented item
                items.add(LuaGearItem(name=item_name, augments=""))
        
        for match in self.PATTERN_SIMPLE_SINGLE.finditer(content):
            slot_name = match.group(1).lower()
            item_name = match.group(2).strip()
            
            if slot_name == 'name':
                continue
                
            if self._is_valid_item_name(item_name):
                items.add(LuaGearItem(name=item_name, augments=""))
        
        return items
    
    def _is_valid_item_name(self, name: str) -> bool:
        """Check if a string looks like a valid item name."""
        if not name or len(name) < 2:
            return False
        
        # Skip common non-item values
        skip_values = {
            'none', 'empty', 'true', 'false', 'nil',
            'normal', 'acc', 'dt', 'pdt', 'mdt',
            'idle', 'engaged', 'defense', 'offense',
            'physical', 'magical', 'hybrid',
        }
        if name.lower() in skip_values:
            return False
        
        # Skip if it looks like a function call or variable reference
        if '(' in name or ')' in name:
            return False
        
        # Skip pure numbers
        if name.isdigit():
            return False
        
        # Skip if it's a known slot name being used as a value
        slot_names = {
            'main', 'sub', 'range', 'ammo', 'head', 'neck',
            'ear1', 'ear2', 'left_ear', 'right_ear',
            'body', 'hands', 'ring1', 'ring2', 'left_ring', 'right_ring',
            'back', 'waist', 'legs', 'feet'
        }
        if name.lower() in slot_names:
            return False
        
        return True


class InventoryLoader:
    """Loads inventory from CSV file."""
    
    def __init__(self):
        self.items: List[InventoryItem] = []
        self.items_by_name: Dict[str, List[InventoryItem]] = {}
    
    def load_from_csv(self, csv_path: str, equip_only: bool = True) -> List[InventoryItem]:
        """Load inventory from CSV."""
        self.items.clear()
        self.items_by_name.clear()
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                container_id = int(row['container_id'])
                
                # Filter for equippable containers if requested
                if equip_only and container_id not in EQUIPPABLE_CONTAINERS:
                    continue
                
                augments = row.get('augments', '').strip()
                
                item = InventoryItem(
                    item_id=int(row['item_id']),
                    item_name=row['item_name'],
                    container_id=container_id,
                    container_name=row['container_name'],
                    augments=augments,
                    count=int(row.get('count', 1)),
                    item_name_log=row.get('item_name_log', '').strip(),
                )
                
                self.items.append(item)
                
                # Index by name (normalized to lowercase)
                name_key = item.item_name.lower()
                if name_key not in self.items_by_name:
                    self.items_by_name[name_key] = []
                self.items_by_name[name_key].append(item)
        
        return self.items


def item_is_in_gearswap(inv_item: InventoryItem, lua_items: Set[LuaGearItem]) -> bool:
    """
    Check if an inventory item matches any item in the gearswap lua files.
    
    Matching rules:
    - Name matches if lua item name matches either item_name OR item_name_log (case-insensitive)
    - If lua item has no augments: matches any inventory item with same name
    - If lua item has augments: lua augments must be subset of inventory augments
    """
    inv_name_lower = inv_item.name_lower
    inv_name_log_lower = inv_item.name_log_lower
    inv_augments = inv_item.normalized_augments
    
    for lua_item in lua_items:
        lua_name_lower = lua_item.name_lower
        
        # Name must match either item_name or item_name_log (case-insensitive)
        name_matches = (lua_name_lower == inv_name_lower or 
                       (inv_name_log_lower and lua_name_lower == inv_name_log_lower))
        
        if not name_matches:
            continue
        
        # If lua has no augments specified, it matches any version
        if not lua_item.augments:
            return True
        
        # If lua has augments, check if they're a subset of inventory augments
        lua_augments = lua_item.normalized_augments
        if lua_augments.issubset(inv_augments):
            return True
    
    return False


def compare_inventory_to_gearswap(
    inventory_items: List[InventoryItem],
    gearswap_items: Set[LuaGearItem]
) -> List[InventoryItem]:
    """
    Find inventory items not referenced in gearswap.
    
    Returns list of orphaned items.
    """
    orphaned = []
    for item in inventory_items:
        if not item_is_in_gearswap(item, gearswap_items):
            orphaned.append(item)
    
    return orphaned


def generate_report(
    orphaned_items: List[InventoryItem],
    lua_files: List[str],
    inventory_csv: str
) -> str:
    """Generate a text report of orphaned items."""
    lines = []
    lines.append("=" * 70)
    lines.append("ORPHANED INVENTORY ITEMS REPORT")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Inventory file: {Path(inventory_csv).name}")
    lines.append(f"Lua files checked: {len(lua_files)}")
    for lua_file in lua_files:
        lines.append(f"  - {Path(lua_file).name}")
    lines.append("")
    lines.append(f"Total orphaned items: {len(orphaned_items)}")
    lines.append("")
    lines.append("-" * 70)
    lines.append("")
    
    # Group by container
    by_container: Dict[str, List[InventoryItem]] = {}
    for item in orphaned_items:
        if item.container_name not in by_container:
            by_container[item.container_name] = []
        by_container[item.container_name].append(item)
    
    # Sort containers
    container_order = ['wardrobe', 'wardrobe2', 'wardrobe3', 'wardrobe4', 
                       'wardrobe5', 'wardrobe6', 'wardrobe7', 'wardrobe8']
    
    for container in container_order:
        if container not in by_container:
            continue
        
        items = by_container[container]
        lines.append(f"[{container.upper()}] ({len(items)} items)")
        lines.append("")
        
        # Sort items alphabetically
        for item in sorted(items, key=lambda x: x.item_name.lower()):
            lines.append(f"  {item.display_name}")
        
        lines.append("")
    
    # Any remaining containers not in the standard order
    for container, items in sorted(by_container.items()):
        if container in container_order:
            continue
        
        lines.append(f"[{container.upper()}] ({len(items)} items)")
        lines.append("")
        
        for item in sorted(items, key=lambda x: x.item_name.lower()):
            lines.append(f"  {item.display_name}")
        
        lines.append("")
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python gearswap_inventory_checker.py <lua_folder_or_file> <inventory_csv>")
        print("")
        print("Arguments:")
        print("  lua_folder_or_file  Path to folder containing .lua files, or a single .lua file")
        print("  inventory_csv       Path to inventory CSV file")
        sys.exit(1)
    
    lua_path = sys.argv[1]
    inventory_csv = sys.argv[2]
    
    # Validate paths
    if not os.path.exists(lua_path):
        print(f"Error: Lua path not found: {lua_path}")
        sys.exit(1)
    
    if not os.path.exists(inventory_csv):
        print(f"Error: Inventory CSV not found: {inventory_csv}")
        sys.exit(1)
    
    # Extract items from lua files
    print("Extracting items from GearSwap lua files...")
    extractor = LuaItemExtractor()
    
    if os.path.isdir(lua_path):
        extractor.extract_from_folder(lua_path)
        lua_files = list(Path(lua_path).glob("*.lua"))
    else:
        extractor.extract_from_file(lua_path)
        lua_files = [Path(lua_path)]
    
    # Count items with and without augments
    augmented_count = sum(1 for i in extractor.items if i.augments)
    print(f"  Found {len(extractor.items)} unique items ({augmented_count} with augments) in {len(lua_files)} lua file(s)")
    
    # Load inventory
    print("Loading inventory...")
    loader = InventoryLoader()
    loader.load_from_csv(inventory_csv, equip_only=True)
    augmented_inv = sum(1 for i in loader.items if i.augments)
    print(f"  Found {len(loader.items)} equippable items ({augmented_inv} with augments) in inventory")
    
    # Compare
    print("Comparing...")
    orphaned = compare_inventory_to_gearswap(loader.items, extractor.items)
    print(f"  Found {len(orphaned)} orphaned items")
    
    # Generate report
    report = generate_report(orphaned, [str(f) for f in lua_files], inventory_csv)
    
    # Output report
    output_file = "orphaned_items_report.txt"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\nReport saved to: {output_file}")
    print("")
    print(report)


if __name__ == "__main__":
    main()
