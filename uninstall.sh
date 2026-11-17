#!/usr/bin/env bash
# tube2dvd uninstaller - verwijdert commands en launcher, data standaard blijft staan.
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="${TUBE2DVD_DIR:-$HOME/Videos/tube2dvd}"
PURGE=0
REMOVE_SOURCE=0
YES=0

usage() {
  cat <<'EOF'
Gebruik: ./uninstall.sh [opties]

  --prefix DIR     Installatiemap (default ~/.local/bin)
  --dir DIR        Bibliotheekmap (default ~/Videos/tube2dvd)
  --purge-data     Verwijder ook de bibliotheek (video's, ISO's, index.html)
  --remove-source  Verwijder ook deze repository map
  -y, --yes        Geen bevestiging
  -h, --help       Deze help

Standaard blijven je video's en ISO's gewoon staan.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --dir) DATA_DIR="$2"; shift 2 ;;
    --purge-data) PURGE=1; shift ;;
    --remove-source) REMOVE_SOURCE=1; shift ;;
    -y|--yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Onbekende optie: $1" >&2; usage >&2; exit 1 ;;
  esac
done

info() { printf '[uninstall] %s\n' "$*"; }
confirm() {
  [[ $YES -eq 1 ]] && return 0
  read -r -p "$1 [j/N]: " answer
  [[ "$answer" =~ ^[jJyY](a|es)?$ ]]
}

info "Verwijder $PREFIX/tube2dvd en tube2dvd-gui"
rm -f -- "$PREFIX/tube2dvd" "$PREFIX/tube2dvd-gui"

DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/tube2dvd.desktop"
if [[ -f "$DESKTOP_FILE" ]]; then
  rm -f -- "$DESKTOP_FILE"
  info "Verwijder $DESKTOP_FILE"
fi

if [[ -f "$SRC_DIR/tube2dvd.py.bak" ]]; then
  rm -f -- "$SRC_DIR/tube2dvd.py.bak"
  info "Verwijder backup van update"
fi

if [[ $PURGE -eq 1 && -d "$DATA_DIR" ]]; then
  if confirm "Verwijder bibliotheek $DATA_DIR inclusief video's en ISO's?"; then
    rm -rf -- "$DATA_DIR"
    info "Verwijder $DATA_DIR"
  else
    info "Bibliotheek behouden"
  fi
fi

if [[ $REMOVE_SOURCE -eq 1 ]]; then
  if confirm "Verwijder repository $SRC_DIR (dit script ook)?"; then
    rm -rf -- "$SRC_DIR"
    info "Verwijder $SRC_DIR"
  else
    info "Repository behouden"
  fi
fi

info "Klaar. Controleer eventueel met: command -v tube2dvd"
