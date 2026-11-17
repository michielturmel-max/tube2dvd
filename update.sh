#!/usr/bin/env bash
# tube2dvd update - haal git-updates op en werk de installatie bij.
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="${TUBE2DVD_DIR:-$HOME/Videos/tube2dvd}"
PORT="8769"
COPY=0
CHECK=0
NO_INSTALL=0
YES=0

usage() {
  cat <<'EOF'
Gebruik: ./update.sh [opties]

  --prefix DIR     Installatiemap (default ~/.local/bin)
  --dir DIR        Bibliotheekmap (default ~/Videos/tube2dvd)
  --port N         Poort van de web-app (default 8769)
  --copy           Gebruik kopie i.p.v. symlink bij herinstalleren
  --check          Alleen controleren, niet bijwerken
  --no-install     Alleen git pull, installatie niet opnieuw doen
  -y, --yes        Geen bevestiging
  -h, --help       Deze help

Alternatief (release van GitHub): tube2dvd --json update --check / -y
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --dir) DATA_DIR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --copy) COPY=1; shift ;;
    --check) CHECK=1; shift ;;
    --no-install) NO_INSTALL=1; shift ;;
    -y|--yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Onbekende optie: $1" >&2; usage >&2; exit 1 ;;
  esac
done

info() { printf '[update] %s\n' "$*"; }

command -v git >/dev/null 2>&1 || { echo "git niet gevonden" >&2; exit 1; }
git -C "$SRC_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "$SRC_DIR is geen git-repository" >&2; exit 1;
}

OLD=$(python3 "$SRC_DIR/tube2dvd.py" --version 2>&1 || true)
info "Huidig: $OLD"

if [[ $CHECK -eq 1 ]]; then
  git -C "$SRC_DIR" fetch --quiet
  git -C "$SRC_DIR" log --oneline --decorate HEAD..@{u} 2>/dev/null || info "Geen upstream ingesteld"
  exit 0
fi

if [[ -n $(git -C "$SRC_DIR" status --porcelain) ]]; then
  info "Let op: lokale wijzigingen aanwezig; pull kan conflicts geven"
fi

git -C "$SRC_DIR" pull --ff-only
NEW=$(python3 "$SRC_DIR/tube2dvd.py" --version 2>&1 || true)
info "Nu: $NEW"

if [[ $NO_INSTALL -eq 0 ]]; then
  args=(--prefix "$PREFIX" --dir "$DATA_DIR" --port "$PORT" --no-deps)
  [[ $COPY -eq 1 ]] && args+=(--copy)
  [[ $YES -eq 1 ]] && args+=(-y)
  "$SRC_DIR/install.sh" "${args[@]}"
fi

info "Klaar. Herstart de web-app als die draaide."
