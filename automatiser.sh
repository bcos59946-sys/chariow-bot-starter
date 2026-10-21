#!/data/data/com.termux/files/usr/bin/bash
set -eu

cd "$HOME/cei_bot/chariow-bot-starter"

echo "=== 1. Vérification du projet ==="
test -f chariow_bot/main.py || {
  echo "ERREUR : main.py introuvable."
  exit 1
}

echo "=== 2. Sauvegarde et sécurisation des journaux ==="
python - <<'PY'
from pathlib import Path
from datetime import datetime
import ast

p = Path("chariow_bot/main.py")
source = p.read_text()
ast.parse(source)

backup = p.with_name(
    p.name + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
)
backup.write_text(source)

if 'logging.getLogger("httpx").setLevel(logging.WARNING)' not in source:
    tree = ast.parse(source)
    nodes = list(tree.body)
    start = 1 if (
        nodes and isinstance(nodes[0], ast.Expr)
        and isinstance(nodes[0].value, (ast.Str, ast.Constant))
        and isinstance(getattr(nodes[0].value, "value", None), str)
    ) else 0

    # Conserver les éventuels imports __future__ en tête.
    while start < len(nodes) and isinstance(nodes[start], ast.ImportFrom) \
            and nodes[start].module == "__future__":
        start += 1

    # Placer la configuration après le bloc initial d'importations.
    end = start
    while end < len(nodes) and isinstance(
        nodes[end], (ast.Import, ast.ImportFrom)
    ):
        end += 1

    if end > start:
        line = nodes[end - 1].end_lineno
        lines = source.splitlines(keepends=True)
        snippet = (
            "\nimport logging\n"
            'logging.getLogger("httpx").setLevel(logging.WARNING)\n'
            'logging.getLogger("httpcore").setLevel(logging.WARNING)\n'
        )
        lines.insert(line, snippet)
        source = "".join(lines)
    else:
        source = (
            'import logging\n'
            'logging.getLogger("httpx").setLevel(logging.WARNING)\n'
            'logging.getLogger("httpcore").setLevel(logging.WARNING)\n\n'
            + source
        )
    p.write_text(source)

ast.parse(p.read_text())
print("Sauvegarde créée :", backup.name)
print("Configuration des journaux vérifiée.")
PY

echo "=== 3. Vérification Python ==="
source .venv/bin/activate
python -m py_compile chariow_bot/*.py

echo "=== 4. Démarrage du bot ==="
echo "Pour arrêter le bot : CTRL+C"
exec python -m chariow_bot.main
