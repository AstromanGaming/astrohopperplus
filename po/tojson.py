#!/usr/bin/env python3
from pathlib import Path
import sys
import json
import glob

# Définit le dossier 'po' relatif à l'emplacement du script
HERE = Path(__file__).resolve().parent
PO_DIR = HERE

# Vérifier la dépendance translate.storage.po
try:
    from translate.storage import po
except ModuleNotFoundError:
    print("Erreur: module 'translate' introuvable. Installez translate-toolkit :")
    print("  python3 -m pip install --user translate-toolkit")
    sys.exit(2)

res = {}

# Cherche les fichiers .po dans le dossier po (adjacent)
po_files = sorted(PO_DIR.glob("*.po"))
if not po_files:
    print(f"Aucun fichier .po trouvé dans {PO_DIR}")
else:
    for file in po_files:
        print("Processing", file)
        lang = file.stem
        with file.open('rb') as fd:
            store = po.pofile()
            store.parse(fd)
            d = {}
            for k in store.getids():
                d[k] = store.translate(k)
            res[lang] = d

# Écrit le fichier messages.js dans le même dossier po
out_path = PO_DIR / 'messages.js'
with out_path.open('w', encoding='utf8') as js:
    js.write('var i18n_dicts = ')
    json.dump(res, js, indent=4, ensure_ascii=False)
    js.write('\n')

print("Wrote", out_path)
