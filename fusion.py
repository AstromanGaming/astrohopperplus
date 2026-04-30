#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fusion.py

- Version : 1.0
- Auteur : AstromanGaming

Fusionne deux fichiers HTML (head + body) en évitant les doublons et collisions d'ID.
Génère un rapport JSON détaillé des éléments ajoutés/modifiés.

Usage:
    python fusion.py [main.html] [patch.html] [-o output.html]

Par défaut:
    main: AstroHopperPlus.html
    patch: astrohopper.html
    out: output.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Optional, Set, Dict, Any

# --- Vérification des dépendances ---
def check_dependencies() -> None:
    missing = []
    try:
        import bs4  # noqa: F401
    except Exception:
        missing.append("beautifulsoup4")
    try:
        import html5lib  # noqa: F401
    except Exception:
        missing.append("html5lib")
    if missing:
        pkg_list = " ".join(missing)
        print("Dépendances manquantes :", pkg_list, file=sys.stderr)
        print("Installe-les avec :", file=sys.stderr)
        print(f"  python -m pip install {pkg_list}", file=sys.stderr)
        sys.exit(2)

check_dependencies()

from bs4 import BeautifulSoup  # type: ignore

# --- Configuration ---
PARSER = "html5lib"
DEFAULT_MAIN = "AstroHopperPlus.html"
DEFAULT_PATCH = "astrohopper.html"
DEFAULT_OUT = "output.html"

LOG = logging.getLogger("fusion")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# --- Utilitaires ---
def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def normalize_html(s: str) -> str:
    s = re.sub(r">\s+<", "><", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def elem_hash(elem) -> str:
    return sha(normalize_html(str(elem)))

def load_soup(path: str) -> BeautifulSoup:
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    try:
        return BeautifulSoup(text, PARSER)
    except Exception:
        return BeautifulSoup(text, "html.parser")

def is_stylesheet_link(tag) -> bool:
    return (
        tag.name == "link"
        and tag.has_attr("rel")
        and any("stylesheet" in r for r in tag["rel"])
    )

# --- Collecte d'IDs référencés (heuristiques) ---
def collect_referenced_ids_from_script(text: Optional[str]) -> Set[str]:
    ids = set()
    if not text:
        return ids
    for m in re.finditer(r"getElementById\(\s*['\"]([^'\"\)]+)['\"]\s*\)", text):
        ids.add(m.group(1))
    for m in re.finditer(r"querySelector(?:All)?\(\s*['\"]\s*#([^'\"\.\>\s]+)['\"]\s*\)", text):
        ids.add(m.group(1))
    for m in re.finditer(r"['\"]#([A-Za-z0-9_\-]+)['\"]", text):
        ids.add(m.group(1))
    return ids

def collect_referenced_ids_from_style(text: Optional[str]) -> Set[str]:
    ids = set()
    if not text:
        return ids
    for m in re.finditer(r"#([A-Za-z0-9_\-]+)\b", text):
        ids.add(m.group(1))
    return ids

def collect_referenced_ids_from_attributes(el) -> Set[str]:
    ids = set()
    for attr in ("href", "aria-controls", "data-target", "for", "data-bs-target", "data-toggle", "data-target"):
        v = el.get(attr)
        if not v:
            continue
        if isinstance(v, list):
            v = " ".join(v)
        m = re.match(r"^\s*#([A-Za-z0-9_\-]+)\s*$", v)
        if m:
            ids.add(m.group(1))
        else:
            for mm in re.finditer(r"([A-Za-z0-9_\-]+)", v):
                ids.add(mm.group(1))
    for k, v in el.attrs.items():
        if isinstance(v, list):
            v = " ".join(v)
        if isinstance(v, str) and ("getElementById" in v or "#" in v):
            ids |= collect_referenced_ids_from_script(v)
    return ids

def collect_referenced_ids_from_html(soup: BeautifulSoup) -> Set[str]:
    ids = set()
    for s in soup.find_all("script"):
        if s.string:
            ids |= collect_referenced_ids_from_script(s.string)
    for st in soup.find_all("style"):
        if st.string:
            ids |= collect_referenced_ids_from_style(st.string)
    for el in soup.find_all(attrs=True):
        ids |= collect_referenced_ids_from_attributes(el)
    return ids

# --- Gestion d'IDs uniques ---
def ensure_unique_id(soup: BeautifulSoup, el, protected_ids: Optional[Set[str]] = None, report: Optional[Dict[str, Any]] = None) -> None:
    protected_ids = protected_ids or set()
    if not getattr(el, "attrs", None) or not el.has_attr("id"):
        return
    base = el["id"]
    if base in protected_ids:
        return
    found = soup.find(id=base)
    if found is None or found is el:
        return
    i = 1
    new_id = f"{base}_{i}"
    while soup.find(id=new_id) is not None:
        i += 1
        new_id = f"{base}_{i}"
    # record change in report if provided
    if report is not None:
        report.setdefault("id_changes", []).append({"old": base, "new": new_id})
    el["id"] = new_id

# --- Fusion head/body (sans injection JS agressif) ---
def merge_head(main: BeautifulSoup, other: BeautifulSoup, report: Dict[str, Any]) -> None:
    seen_link = set()
    seen_style = set()
    seen_script_src = set()
    seen_inline_script = set()
    seen_meta = set()

    if main.head:
        for l in main.head.find_all("link"):
            if is_stylesheet_link(l) and l.has_attr("href"):
                seen_link.add(l["href"])
        for st in main.head.find_all("style"):
            seen_style.add(elem_hash(st))
        for s in main.head.find_all("script"):
            if s.has_attr("src"):
                seen_script_src.add(s["src"])
            else:
                seen_inline_script.add(elem_hash(s))
        for m in main.head.find_all("meta"):
            seen_meta.add(tuple(sorted(m.attrs.items())))

    if not other.head:
        return

    main_has_viewport = any(
        m.get("name", "").lower() == "viewport" for m in (main.head.find_all("meta") or [])
    )

    # metas
    for m in other.head.find_all("meta"):
        key = tuple(sorted(m.attrs.items()))
        if key in seen_meta:
            continue
        if main_has_viewport and m.get("name", "").lower() == "viewport":
            continue
        main.head.append(m)
        seen_meta.add(key)
        report.setdefault("meta_added", []).append(dict(m.attrs))

    # links (stylesheets)
    for l in other.head.find_all("link"):
        if is_stylesheet_link(l):
            href = l.get("href")
            if href and href not in seen_link:
                main.head.append(l)
                seen_link.add(href)
                report.setdefault("links_added", []).append({"href": href, "attrs": dict(l.attrs)})

    # styles
    for st in other.head.find_all("style"):
        h = elem_hash(st)
        if h not in seen_style:
            main.head.append(st)
            seen_style.add(h)
            report.setdefault("styles_added", []).append({"hash": h, "text_preview": (st.string or "")[:200]})

    # scripts in head
    for s in other.head.find_all("script"):
        if s.has_attr("src"):
            src = s["src"]
            if src not in seen_script_src:
                main.head.append(s)
                seen_script_src.add(src)
                report.setdefault("scripts_added", []).append({"type": "external", "src": src, "attrs": dict(s.attrs)})
        else:
            h = elem_hash(s)
            if h not in seen_inline_script:
                script_text = s.string or ""
                wrapped = main.new_tag("script")
                wrapped.string = script_text
                main.head.append(wrapped)
                seen_inline_script.add(h)
                report.setdefault("scripts_added", []).append({"type": "inline", "hash": h, "text_preview": script_text[:200]})

def signature(el) -> str:
    if getattr(el, "name", None):
        el_id = el.get("id")
        if el_id:
            return f"{el.name}#id:{el_id}"
        classes = " ".join(el.get("class", []))
        return f"{el.name}#class:{classes}#hash:{elem_hash(el)}"
    else:
        return f"text#hash:{sha(str(el).strip())}"

def merge_body(main: BeautifulSoup, other: BeautifulSoup, report: Dict[str, Any]) -> None:
    protected_ids = set()
    protected_ids |= collect_referenced_ids_from_html(main)
    protected_ids |= collect_referenced_ids_from_html(other)
    protected_ids.update([
        'map', 'mapid', 'map-canvas', 'map_canvas', 'mapContainer', 'mapDiv'
    ])
    report["protected_ids"] = sorted(list(protected_ids))

    seen_sign = set()
    seen_text_sign = set()

    if main.body:
        for c in main.body.find_all(recursive=False):
            seen_sign.add(signature(c))
            if getattr(c, "name", None) is None:
                seen_text_sign.add(sha(str(c).strip()))

    if not other.body:
        return

    seen_inline_body_scripts = set()
    seen_inline_body_styles = set()

    for c in list(other.body.contents):
        if getattr(c, "name", None) is None and str(c).strip() == "":
            continue

        if getattr(c, "name", None) is None:
            txt = str(c).strip()
            if txt:
                txt_hash = sha(txt)
                if txt_hash in seen_text_sign:
                    continue
                if "No Geolocation" in txt and any("No Geolocation" in (str(x).strip()) for x in main.body.contents):
                    seen_text_sign.add(txt_hash)
                    continue
                seen_text_sign.add(txt_hash)

        if getattr(c, "attrs", None) and c.has_attr("id"):
            # ensure unique id and record changes
            old_id = c["id"]
            ensure_unique_id(main, c, protected_ids=protected_ids, report=report)
            if c.has_attr("id") and c["id"] != old_id:
                # ensure_unique_id already recorded change
                pass

        sig = signature(c)
        if sig in seen_sign:
            continue

        if getattr(c, "name", None) == "script" and not c.has_attr("src"):
            h = elem_hash(c)
            if h in seen_inline_body_scripts:
                continue
            seen_inline_body_scripts.add(h)
            script_text = c.string or ""
            wrapped = main.new_tag("script")
            wrapped.string = script_text
            main.body.append(wrapped)
            seen_sign.add(sig)
            report.setdefault("body_scripts_added", []).append({"type": "inline", "hash": h, "text_preview": script_text[:200]})
            continue

        if getattr(c, "name", None) == "style":
            h = elem_hash(c)
            if h in seen_inline_body_styles:
                continue
            seen_inline_body_styles.add(h)
            main.body.append(c)
            seen_sign.add(sig)
            report.setdefault("body_styles_added", []).append({"hash": h, "text_preview": (c.string or "")[:200]})
            continue

        try:
            text_content = c.get_text(strip=True) if getattr(c, "name", None) else str(c).strip()
        except Exception:
            text_content = ""

        if text_content and "No Geolocation" in text_content:
            if any("No Geolocation" in (x.get_text(strip=True) if getattr(x, "name", None) else str(x).strip()) for x in main.body.contents):
                continue

        # append element and record signature + brief info
        main.body.append(c)
        seen_sign.add(sig)
        entry = {"signature": sig}
        if getattr(c, "name", None):
            entry["tag"] = c.name
            if c.has_attr("id"):
                entry["id"] = c["id"]
            if c.has_attr("class"):
                entry["class"] = " ".join(c.get("class", []))
            entry["html_preview"] = normalize_html(str(c))[:300]
        else:
            entry["text_preview"] = str(c).strip()[:200]
        report.setdefault("body_elements_added", []).append(entry)

# --- Fonction principale de fusion ---
def do_merge(main_path: str, patch_path: str, out_path: str) -> Dict[str, Any]:
    if not os.path.exists(main_path):
        LOG.error("Fichier principal introuvable: %s", main_path)
        raise FileNotFoundError(main_path)
    if not os.path.exists(patch_path):
        LOG.error("Fichier patch introuvable: %s", patch_path)
        raise FileNotFoundError(patch_path)

    LOG.info("Fusion : principal=%s  patch=%s  -> sortie=%s", main_path, patch_path, out_path)

    report: Dict[str, Any] = {
        "main": os.path.abspath(main_path),
        "patch": os.path.abspath(patch_path),
        "out": os.path.abspath(out_path),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "meta_added": [],
        "links_added": [],
        "styles_added": [],
        "scripts_added": [],
        "body_scripts_added": [],
        "body_styles_added": [],
        "body_elements_added": [],
        "id_changes": [],
        "protected_ids": [],
    }

    main_soup = load_soup(main_path)
    patch_soup = load_soup(patch_path)

    # ensure head/body exist
    if main_soup.head is None:
        if main_soup.html:
            main_soup.html.insert(0, main_soup.new_tag("head"))
        else:
            main_soup.insert(0, main_soup.new_tag("head"))
    if main_soup.body is None:
        if main_soup.html:
            main_soup.html.append(main_soup.new_tag("body"))
        else:
            main_soup.append(main_soup.new_tag("body"))

    merge_head(main_soup, patch_soup, report)
    merge_body(main_soup, patch_soup, report)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(str(main_soup))

    # write JSON report next to output file
    report_path = ".fusion_report.json"
    try:
        with open(report_path, "w", encoding="utf-8") as rf:
            json.dump(report, rf, ensure_ascii=False, indent=2)
        LOG.info("Merged saved to %s", out_path)
        LOG.info("Fusion report saved to %s", report_path)
    except Exception as e:
        LOG.exception("Impossible d'écrire le rapport JSON: %s", e)

    return report

# --- CLI ---
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fusionne deux fichiers HTML (head+body) et produit un rapport JSON.")
    p.add_argument("main", nargs="?", default=DEFAULT_MAIN, help="Fichier principal (par défaut: %(default)s)")
    p.add_argument("patch", nargs="?", default=DEFAULT_PATCH, help="Fichier patch (par défaut: %(default)s)")
    p.add_argument("-o", "--out", default=DEFAULT_OUT, help="Fichier de sortie (par défaut: %(default)s)")
    return p.parse_args()

def main() -> None:
    args = parse_args()
    try:
        do_merge(args.main, args.patch, args.out)
    except FileNotFoundError as e:
        LOG.error("Fichier introuvable: %s", e)
        sys.exit(1)
    except Exception as e:
        LOG.exception("Erreur inattendue: %s", e)
        sys.exit(2)

if __name__ == "__main__":
    main()