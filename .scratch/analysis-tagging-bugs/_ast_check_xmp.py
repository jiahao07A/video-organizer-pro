# -*- coding: utf-8 -*-
"""一次性 triage 验证脚本：检查 sync_metadata_to_xmp 中未绑定名称。"""
import ast
import pathlib

p = pathlib.Path(__file__).resolve().parents[2] / "core" / "video_organizer_service.py"
tree = ast.parse(p.read_text(encoding="utf-8"))

for node in tree.body:
    if not isinstance(node, ast.ClassDef):
        continue
    for item in node.body:
        if not (isinstance(item, ast.FunctionDef) and item.name == "sync_metadata_to_xmp"):
            continue
        assigned = {"self"}
        for a in item.args.args:
            assigned.add(a.arg)
        for n in ast.walk(item):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                assigned.add(n.id)
            if isinstance(n, ast.FunctionDef):
                assigned.add(n.name)
                for a in n.args.args:
                    assigned.add(a.arg)
                for a in n.args.kwonlyargs:
                    assigned.add(a.arg)
            if isinstance(n, ast.Import):
                for alias in n.names:
                    assigned.add(alias.asname or alias.name.split(".")[0])
            if isinstance(n, ast.ImportFrom):
                for alias in n.names:
                    assigned.add(alias.asname or alias.name)
        used = {
            n.id
            for n in ast.walk(item)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        ignore = {
            "True",
            "False",
            "None",
            "os",
            "escape",
            "Optional",
            "List",
            "Dict",
            "Any",
            "len",
            "str",
            "int",
            "enumerate",
            "next",
            "isinstance",
            "open",
            "print",
        }
        free = sorted(used - assigned - ignore)
        print("function:", item.name)
        print("unbound_or_outer_names:", free)