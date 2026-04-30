#!/usr/bin/env bash

# AUTORUN.sh

# Version : 1.0
# Auteur : AstromanGaming

set -euo pipefail

LOG="${LOG:-./AUTORUN.log}"
PYTHON="${PYTHON:-python3}"
VENV="${VENV:-}"
timestamp() { date +"%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(timestamp)] $*" | tee -a "$LOG"; }

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <fusion.py> <deploy.py> [deploy-args...]" >&2
  exit 1
fi

# --- dépôts à cloner (url|ref|dest) ---
REPOS=(
  "https://github.com/mattiaverga/OpenNGC.git|46694d5825efe9c569c5e137c57a7fba634a9144|./OpenNGC"
  "https://github.com/gmiller123456/vsop87-multilang.git|e1b8882e78e92950a9928d5b2e54ad50eb96f72f|./vsop87-multilang"
  "https://github.com/eleanorlutz/western_constellations_atlas_of_space.git|f5258e29499a357ce132dacd312e46d9acf98f88|./western_constellations_atlas_of_space"
)

# Helper: check if string looks like a 40-char SHA
is_sha() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }

# clone or update ensuring correct ref is checked out
clone_or_update_repo() {
  local url="$1"; local ref="$2"; local dest="$3"
  local tries=0 max_tries=3 backoff=2 lockfile="${dest}.lock"

  log "Traitement du dépôt: $url -> $dest (ref: $ref)"

  mkdir -p "$(dirname "$dest")"

  # simple flock-based lock (file descriptor 9)
  exec 9>"$lockfile"
  if ! flock -n 9 2>/dev/null; then
    log "Attente du verrou sur $dest"
    flock 9
  fi

  while [ $tries -lt $max_tries ]; do
    tries=$((tries+1))

    if [ -d "$dest/.git" ]; then
      log "Le dépôt existe déjà. Mise à jour (essai $tries/$max_tries)"
      if ! git -C "$dest" fetch --quiet --all --prune; then
        log "Fetch échoué pour $dest"
        sleep $backoff
        backoff=$((backoff * 2))
        continue
      fi

      # Si ref est SHA : fetch explicite et checkout détaché
      if is_sha "$ref"; then
        git -C "$dest" fetch --quiet origin "$ref" --depth=1 || true
        if git -C "$dest" rev-parse --verify --quiet "$ref" >/dev/null; then
          git -C "$dest" checkout --quiet "$ref" || { log "Checkout SHA échoué"; }
        else
          log "SHA $ref introuvable localement après fetch"
          sleep $backoff
          backoff=$((backoff * 2))
          continue
        fi
      else
        # ref est branch ou tag : tenter d'identifier s'il existe sur origin
        if git -C "$dest" show-ref --verify --quiet "refs/heads/$ref"; then
          # existe en local : checkout la branche locale et aligner sur origin/ref si possible
          git -C "$dest" checkout --quiet "$ref" || { log "Checkout branche locale $ref échoué"; }
          if git -C "$dest" ls-remote --exit-code --heads origin "$ref" >/dev/null 2>&1; then
            git -C "$dest" reset --hard --quiet "origin/$ref" || true
          fi
        elif git -C "$dest" ls-remote --exit-code --heads origin "$ref" >/dev/null 2>&1; then
          # existe sur origin : créer/mettre à jour branche locale pour suivre origin/ref
          git -C "$dest" checkout --quiet -B "$ref" "origin/$ref" || { log "Checkout origin/$ref échoué"; }
        elif git -C "$dest" ls-remote --exit-code --tags origin "$ref" >/dev/null 2>&1; then
          # tag présent sur origin : checkout du tag (détaché)
          git -C "$dest" fetch --quiet --tags origin "$ref" || true
          git -C "$dest" checkout --quiet "tags/$ref" || { log "Checkout tag $ref échoué"; }
        else
          # fallback : tenter checkout direct (peut marcher si ref est un commit court ou autre)
          if git -C "$dest" rev-parse --verify --quiet "$ref" >/dev/null; then
            git -C "$dest" checkout --quiet "$ref" || { log "Checkout fallback $ref échoué"; }
          else
            log "Ref $ref introuvable (local ni origin). Tentative de fetch explicite puis retry."
            git -C "$dest" fetch --quiet origin "$ref" --depth=1 || true
            sleep $backoff
            backoff=$((backoff * 2))
            continue
          fi
        fi
      fi

      # Vérification finale : HEAD correspond-il à la ref demandée (si SHA fourni)
      if is_sha "$ref"; then
        local head_sha
        head_sha=$(git -C "$dest" rev-parse --verify HEAD)
        if [ "$head_sha" != "$ref" ]; then
          log "Mismatch SHA: HEAD=$head_sha attendu=$ref"
          sleep $backoff
          backoff=$((backoff * 2))
          continue
        fi
      fi

      log "Mise à jour terminée pour $dest (HEAD: $(git -C "$dest" rev-parse --short HEAD))"
      flock -u 9
      return 0
    else
      log "Clonage de $url dans $dest (essai $tries/$max_tries)"
      if is_sha "$ref"; then
        # clone minimal sans checkout puis fetch du commit
        if ! git clone --no-checkout --quiet "$url" "$dest"; then
          log "Clone initial échoué"
          sleep $backoff
          backoff=$((backoff * 2))
          continue
        fi
        if ! git -C "$dest" fetch --quiet origin "$ref" --depth=1; then
          log "Fetch du commit $ref échoué"
          sleep $backoff
          backoff=$((backoff * 2))
          continue
        fi
        git -C "$dest" checkout --quiet "$ref" || { log "Checkout du commit $ref échoué"; }
      else
        # branch/tag : essayer shallow clone de la branche
        if git ls-remote --exit-code --heads "$url" "$ref" >/dev/null 2>&1; then
          if ! git clone --depth 1 --branch "$ref" --quiet "$url" "$dest"; then
            log "Shallow clone branch $ref échoué, tentative clone complet"
            git clone --quiet "$url" "$dest" || { log "Clone complet échoué"; sleep $backoff; backoff=$((backoff * 2)); continue; }
            git -C "$dest" checkout --quiet "$ref" || true
          fi
        elif git ls-remote --exit-code --tags "$url" "$ref" >/dev/null 2>&1; then
          # tag présent : full clone minimal pour récupérer le tag
          if ! git clone --quiet "$url" "$dest"; then
            log "Clone complet échoué"
            sleep $backoff
            backoff=$((backoff * 2))
            continue
          fi
          git -C "$dest" checkout --quiet "tags/$ref" || true
        else
          # fallback clone complet puis checkout
          if ! git clone --quiet "$url" "$dest"; then
            log "Clone complet échoué"
            sleep $backoff
            backoff=$((backoff * 2))
            continue
          fi
          if ! git -C "$dest" checkout --quiet "$ref"; then
            log "Checkout fallback $ref échoué"
            sleep $backoff
            backoff=$((backoff * 2))
            continue
          fi
        fi
      fi

      log "Clone terminé pour $dest (HEAD: $(git -C "$dest" rev-parse --short HEAD))"
      flock -u 9
      return 0
    fi
  done

  flock -u 9
  log "ERREUR: échec du traitement du dépôt $url après $max_tries tentatives"
  return 1
}

# Exécuter clones/updates
for entry in "${REPOS[@]}"; do
  IFS='|' read -r url ref dest <<< "$entry"
  if ! clone_or_update_repo "$url" "$ref" "$dest"; then
    log "ERREUR: échec du dépôt $url"
    exit 10
  fi
done

FUSION="$1"
DEPLOY="$2"
shift 2
DEPLOY_ARGS=("$@")

log "AUTORUN start"

if [ -n "$VENV" ]; then
  if [ -f "$VENV/bin/activate" ]; then
    log "Activation du virtualenv: $VENV"
    # shellcheck disable=SC1090
    . "$VENV/bin/activate"
    PYTHON="${PYTHON:-python}"
  else
    log "AVERTISSEMENT: virtualenv introuvable: $VENV"
  fi
fi

log "Lancement: $PYTHON $FUSION"
if ! $PYTHON "$FUSION" >>"$LOG" 2>&1; then
  log "ERREUR: $FUSION a échoué. Voir $LOG"
  exit 4
fi
log "$FUSION terminé"

log "Lancement: $PYTHON $DEPLOY ${DEPLOY_ARGS[*]}"
if ! $PYTHON "$DEPLOY" "${DEPLOY_ARGS[@]}" >>"$LOG" 2>&1; then
  log "ERREUR: $DEPLOY a échoué. Voir $LOG"
  exit 5
fi
log "$DEPLOY terminé"

log "AUTORUN terminé"
exit 0