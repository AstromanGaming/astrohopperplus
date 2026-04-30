#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
deploy.py

- Version : 1.0
- Auteur : AstromanGaming (remaster), Artyom Beilis (original)

Script de déploiement refactorisé pour AstroHopperPlus.

Fonctionnalités :
- Génération des manuels (README.md et po/README_xx.md) en HTML via markdown
- Inlining des scripts référencés et des images PNG (data URI base64)
- Génération de manual.html (langue par défaut)
- Génération de output_deploy.html et sw_deploy.js
- Options CLI : --deploy, --add-ga, --dry-run, --copy-assets
- Rapport JSON (.deploy_report.json) listant images inlinées et manquantes
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import markdown

from create_data import create_db

# --- Configuration logging ---
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("deploy")

# --- Constantes ---
ROOT = Path(__file__).parent.resolve()
PO_DIR = ROOT / "po"
OUT_HTML = ROOT / "output_deploy.html"
OUT_SW = ROOT / "sw_deploy.js"
REPORT_FILE = ROOT / ".deploy_report.json"

# Dossiers à fouiller pour trouver les PNG et autres assets
SEARCH_DIRS = [ROOT, ROOT / "img", ROOT / "assets", ROOT / "po", ROOT / "static"]

# Regex permissives
_SCRIPT_TAG_RE = re.compile(r'^\s*<script\s+src="([^"]+)"></script>\s*$', flags=re.IGNORECASE)
_VERSION_RE = re.compile(r'.*\b(version)\b.*', flags=re.IGNORECASE)
_URL_PNG_RE = re.compile(r'url\((?:["\']?)([^\)"\']+?\.png)(?:["\']?)\)', flags=re.IGNORECASE)
_IMG_SRC_PNG_RE = re.compile(r'src=(?:["\'])([^"\']+?\.png)(?:["\'])', flags=re.IGNORECASE)


# --- Utilitaires ---

def copy_file(src: Path, dst: Path, *, overwrite: bool = True) -> None:
    """Copie un fichier en vérifiant l'existence et en loggant l'action."""
    if not src.exists():
        raise FileNotFoundError(f"Source introuvable: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not overwrite:
        logger.info("Fichier cible existe et overwrite=False: %s", dst)
        return
    logger.info("Copie %s -> %s", src, dst)
    shutil.copy2(src, dst)


def get_version(changelog: Path = ROOT / "Changelog.md") -> str:
    """
    Extrait la première et la deuxième version sémantique trouvées dans Changelog.md.
    - Si deux versions sont trouvées, retourne "X.Y.Z / A.B.C".
    - Si une seule version est trouvée, retourne "X.Y.Z".
    - Sinon, fallback sur la première ligne non vide.
    """
    if not changelog.exists():
        raise FileNotFoundError("Changelog.md introuvable.")
    text = changelog.read_text(encoding="utf-8")

    # Trouve toutes les occurrences de versions (avec ou sans 'v' préfixe)
    versions = re.findall(r'v?(\d+\.\d+(?:\.\d+)*)', text)
    if len(versions) >= 2:
        return f"{versions[0]} / {versions[1]}"
    if len(versions) == 1:
        return versions[0]

    # fallback : première ligne non vide
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    raise ValueError("Impossible d'extraire la version depuis Changelog.md")


def make_manual(lang: Optional[str] = None) -> str:
    """
    Convertit README.md (ou po/README_xx.md) en HTML via markdown.
    Retourne le HTML généré. Génère manual.html pour la langue par défaut.
    """
    path = ROOT / ("README.md" if lang is None else f"po/README_{lang}.md")
    if not path.exists():
        raise FileNotFoundError(f"Manuel introuvable: {path}")
    text = path.read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=["toc"])
    html = md.convert(text)
    if lang is None:
        out = ROOT / "manual.html"
        header = (ROOT / "header.html").read_text(encoding="utf-8") if (ROOT / "header.html").exists() else ""
        footer = (ROOT / "footer.html").read_text(encoding="utf-8") if (ROOT / "footer.html").exists() else ""
        out.write_text(header + html + footer, encoding="utf-8")
        logger.info("manual.html généré")
    return html


def png_encode(path: Path) -> str:
    """Encode une image PNG en data URI base64."""
    if not path.exists():
        raise FileNotFoundError(f"PNG introuvable: {path}")
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def combine_manuals(mans: Dict[str, str]) -> str:
    """Combine plusieurs manuels en sections HTML (gère sens LTR/RTL)."""
    parts = []
    for lang, html in mans.items():
        cls = "l2r" if lang == "en" else "r2l"
        parts.append(f"<div id='manual_tr_{lang}' class='{cls} manual_section'>\n{html}\n</div>")
    return "\n".join(parts)


# --- Résolution des assets ---

def _resolve_png_path(png_ref: str) -> Optional[Path]:
    """
    Essaie de résoudre png_ref (qui peut être 'qs_1_1.png' ou 'images/qs_1_1.png')
    en testant plusieurs dossiers. Retourne Path si trouvé, sinon None.
    """
    candidate = Path(png_ref)
    # si c'est déjà un chemin absolu ou relatif existant, retourne-le
    if candidate.is_absolute() and candidate.exists():
        return candidate
    if candidate.exists():
        return candidate
    # sinon cherche dans les dossiers connus
    for d in SEARCH_DIRS:
        p = d / png_ref
        if p.exists():
            return p
        # aussi tester uniquement le nom de fichier dans chaque dossier
        p2 = d / candidate.name
        if p2.exists():
            return p2
    return None


# --- Embedding / inlining ---

def embed(manual_html: str, version: str, template: Path = ROOT / "output.html", out: Path = OUT_HTML) -> Tuple[List[str], List[str]]:
    """
    Lit le template HTML, inline les scripts, remplace les images PNG par des data URIs,
    injecte le manuel et la version, puis écrit le fichier de sortie.
    Retourne deux listes: inlined (paths) et missing (refs).
    """
    if not template.exists():
        raise FileNotFoundError(f"Template introuvable: {template}")
    out_lines: List[str] = []
    inlined: List[str] = []
    missing: List[str] = []

    template_text = template.read_text(encoding="utf-8")
    for line in template_text.splitlines(keepends=True):
        m = _SCRIPT_TAG_RE.match(line)
        if m:
            script_path = ROOT / m.group(1)
            if script_path.exists():
                script_text = script_path.read_text(encoding="utf-8")
                out_lines.append("<script>\n")
                out_lines.append(script_text)
                out_lines.append("\n</script>\n")
                continue
            else:
                logger.warning("Script référencé introuvable: %s", script_path)
        if _VERSION_RE.match(line):
            out_lines.append(line.replace("version", version))
            continue

        # Cherche url(...png) ou src="...png"
        u = _URL_PNG_RE.search(line) or _IMG_SRC_PNG_RE.search(line)
        if u:
            png_ref = u.group(1)
            png_path = _resolve_png_path(png_ref)
            if png_path:
                try:
                    b64 = png_encode(png_path)
                    inlined.append(str(png_path))
                    if _URL_PNG_RE.search(line):
                        new_line = _URL_PNG_RE.sub(f'url("{b64}")', line)
                    else:
                        new_line = _IMG_SRC_PNG_RE.sub(f'src="{b64}"', line)
                    out_lines.append(new_line)
                except FileNotFoundError:
                    logger.warning("PNG introuvable pour inlining: %s", png_path)
                    missing.append(png_ref)
                    out_lines.append(line)
            else:
                logger.warning("PNG introuvable pour inlining: %s (réf: %s)", png_ref, png_path)
                missing.append(png_ref)
                out_lines.append(line)
            continue

        if line.lstrip().startswith("MANUAL"):
            out_lines.append(manual_html)
            continue

        out_lines.append(line)

    out.write_text("".join(out_lines), encoding="utf-8")
    logger.info("Fichier déployable écrit: %s", out)
    return inlined, missing


def embed_service_worker(version: str, src: Path = ROOT / "sw.js", out: Path = OUT_SW) -> None:
    """Génère sw_deploy.js en remplaçant le token VERSION par la version fournie."""
    if not src.exists():
        raise FileNotFoundError("sw.js introuvable.")
    content = src.read_text(encoding="utf-8")
    content = content.replace("VERSION", version)
    out.write_text(content, encoding="utf-8")
    logger.info("Service worker déployable écrit: %s", out)


def add_ga(deploy_html: Path = OUT_HTML, ga_snippet: Path = ROOT / "ga.html") -> None:
    """Insère le snippet Google Analytics avant </head>."""
    if not ga_snippet.exists():
        raise FileNotFoundError("ga.html introuvable.")
    page = deploy_html.read_text(encoding="utf-8")
    snippet = ga_snippet.read_text(encoding="utf-8")
    if "</head>" in page:
        page = page.replace("</head>", snippet + "</head>")
        deploy_html.write_text(page, encoding="utf-8")
        logger.info("Google Analytics inséré dans %s", deploy_html)
    else:
        raise RuntimeError("</head> introuvable dans le fichier déployé.")


def deploy_files(target_dir: Path) -> None:
    """Copie les fichiers nécessaires vers le répertoire cible."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    copy_file(OUT_HTML, target_dir / "astrohopper.html")
    copy_file(OUT_SW, target_dir / "sw.js")
    for fname in ["LICENSE", "COPYING.md", "manual.html", "manifest.json"]:
        src = ROOT / fname
        if src.exists():
            copy_file(src, target_dir / fname)
        else:
            logger.warning("Fichier absent et ignoré: %s", src)


def copy_assets(missing: List[str], target_assets_dir: Path) -> List[str]:
    """
    Copie les assets manquants (référencés) vers target_assets_dir.
    Retourne la liste des chemins copiés.
    """
    target_assets_dir = Path(target_assets_dir)
    target_assets_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for ref in missing:
        p = _resolve_png_path(ref)
        if p and p.exists():
            dst = target_assets_dir / p.name
            copy_file(p, dst)
            copied.append(str(dst))
        else:
            logger.warning("Asset manquant non trouvé pour copie: %s", ref)
    return copied


# --- CLI ---

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prépare et déploie AstroHopperPlus")
    p.add_argument("--deploy", "-d", metavar="TARGET", help="Copie les fichiers vers TARGET")
    p.add_argument("--add-ga", action="store_true", help="Ajoute Google Analytics au HTML déployé")
    p.add_argument("--dry-run", action="store_true", help="Affiche les actions sans les exécuter")
    p.add_argument("--copy-assets", metavar="ASSETS_DIR", help="Copie les assets manquants vers ASSETS_DIR/assets")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    try:
        create_db()
        version = get_version()
        manuals: Dict[str, str] = {}
        manuals["en"] = make_manual(None)
        for md in PO_DIR.glob("README_*.md"):
            lang = md.stem.replace("README_", "")
            if 2 <= len(lang) <= 3:
                logger.info("Génération manuel pour %s", lang)
                manuals[lang] = make_manual(lang)
        manual_html = combine_manuals(manuals)
        if args.dry_run:
            logger.info("Dry-run: version=%s, manuels=%s", version, list(manuals.keys()))
            return
        inlined, missing = embed(manual_html, version)
        embed_service_worker(version)
        if args.add_ga:
            add_ga()
        copied_assets: List[str] = []
        if args.copy_assets and missing:
            target = Path(args.copy_assets) / "assets"
            copied_assets = copy_assets(missing, target)
        if args.deploy:
            deploy_files(Path(args.deploy))
        # rapport JSON
        report = {"version": version, "inlined": inlined, "missing": missing, "copied_assets": copied_assets}
        REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Rapport écrit: %s", REPORT_FILE)
    except Exception as exc:
        logger.exception("Erreur lors du déploiement: %s", exc)
        raise


if __name__ == "__main__":
    main()