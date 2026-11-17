#!/usr/bin/env bash
# tube2dvd installer - lokale (user) installatie, geen root nodig voor de app zelf.
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="${TUBE2DVD_DIR:-$HOME/Videos/tube2dvd}"
PORT="8769"
MODE="symlink"
DEPS=1
DESKTOP=1
YES=0

usage() {
  cat <<'EOF'
Gebruik: ./install.sh [opties]

  --prefix DIR     Installatiemap voor commands (default ~/.local/bin)
  --dir DIR        Bibliotheekmap (default ~/Videos/tube2dvd)
  --port N         Poort van de web-app (default 8769)
  --copy           Kopieer het script i.p.v. een symlink te maken
  --no-deps        Controleer/Installeer systeemafhankelijkheden niet
  --no-desktop     Maak geen desktop-launcher
  -y, --yes        Geen bevestigingen (pacman --noconfirm)
  -h, --help       Deze help

Voorbeelden:
  ./install.sh
  ./install.sh --port 9000 --dir ~/Videos/mijn-dvd
  ./install.sh --copy --no-deps
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --dir) DATA_DIR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --copy) MODE="copy"; shift ;;
    --no-deps) DEPS=0; shift ;;
    --no-desktop) DESKTOP=0; shift ;;
    -y|--yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Onbekende optie: $1" >&2; usage >&2; exit 1 ;;
  esac
done

[[ -f "$SRC_DIR/tube2dvd.py" ]] || { echo "tube2dvd.py niet gevonden in $SRC_DIR" >&2; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "--port moet een getal zijn" >&2; exit 1; }

info() { printf '[install] %s\n' "$*"; }

if [[ $DEPS -eq 1 ]]; then
  declare -A PKG=(
    [yt-dlp]=yt-dlp [ffmpeg]=ffmpeg [ffprobe]=ffmpeg [python3]=python
    [mkisofs]=cdrtools [dvdauthor]=dvdauthor [spumux]=dvdauthor
    [growisofs]=dvd+rw-tools [xorriso]=libisoburn
  )
  missing_tools=()
  for tool in "${!PKG[@]}"; do
    command -v "$tool" >/dev/null 2>&1 || missing_tools+=("$tool")
  done

  base_pkgs=(python ttf-dejavu ttf-liberation curl xdg-utils sudo)
  missing_pkgs=()
  if command -v pacman >/dev/null 2>&1; then
    for pkg in "${base_pkgs[@]}"; do
      pacman -Q "$pkg" >/dev/null 2>&1 || missing_pkgs+=("$pkg")
    done
  fi

  packages=()
  for tool in "${missing_tools[@]}"; do
    packages+=("${PKG[$tool]}")
  done
  packages+=("${missing_pkgs[@]}")
  if [[ ${#packages[@]} -gt 0 ]]; then
    mapfile -t packages < <(printf '%s\n' "${packages[@]}" | sort -u)
    info "Ontbrekend: ${missing_tools[*]} ${missing_pkgs[*]}"
    if command -v pacman >/dev/null 2>&1; then
      args=(--needed)
      [[ $YES -eq 1 ]] && args+=(--noconfirm)
      if [[ $EUID -eq 0 ]]; then
        pacman -S "${args[@]}" "${packages[@]}"
      elif command -v sudo >/dev/null 2>&1; then
        sudo pacman -S "${args[@]}" "${packages[@]}"
      else
        info "Installeer zelf: sudo pacman -S ${packages[*]}"
      fi
    else
      info "Installeer zelf: ${packages[*]}"
    fi
  else
    info "Systeemafhankelijkheden aanwezig"
  fi
fi

plugin="${TUBE2DVD_NEWS:-$HOME/.config/omarchy/plugins/tweakers.nieuws/bin/tweakers-nieuws}"
if command -v tweakers-nieuws >/dev/null 2>&1; then
  info "Nieuwsplugin: $(command -v tweakers-nieuws)"
elif [[ -x "$plugin" ]]; then
  info "Nieuwsplugin: $plugin"
else
  info "Nieuwsplugin niet gevonden; nieuws werkt pas na installatie van tweakers.nieuws"
  info "  of zet TUBE2DVD_NEWS=/pad/tweakers-nieuws"
fi

mkdir -p "$PREFIX"
BIN="$PREFIX/tube2dvd"
if [[ $MODE == "copy" ]]; then
  install -m 755 "$SRC_DIR/tube2dvd.py" "$BIN"
else
  ln -sfn "$SRC_DIR/tube2dvd.py" "$BIN"
fi
chmod +x "$BIN"
info "Command: $BIN -> $SRC_DIR/tube2dvd.py"

if [[ $DESKTOP -eq 1 ]]; then
  GUI="$PREFIX/tube2dvd-gui"
  cat > "$GUI" <<EOF
#!/usr/bin/env bash
set -euo pipefail
BIN=$(printf '%q' "$BIN")
DIR=$(printf '%q' "$DATA_DIR")
PORT=$(printf '%q' "$PORT")
URL="http://127.0.0.1:\$PORT/"
if ! curl -fsS -o /dev/null "\$URL" 2>/dev/null; then
  nohup "\$BIN" --json -d "\$DIR" serve --port "\$PORT" >/dev/null 2>&1 &
fi
command -v xdg-open >/dev/null 2>&1 && xdg-open "\$URL" >/dev/null 2>&1 || true
EOF
  chmod +x "$GUI"

  APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$APP_DIR"
  DESKTOP_FILE="$APP_DIR/tube2dvd.desktop"
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=tube2dvd
Comment=Plakboek-app voor je data-dvd
Exec="$GUI"
Terminal=false
Categories=Utility;Video;
EOF
  info "Desktop-launcher: $DESKTOP_FILE"
fi

mkdir -p "$DATA_DIR" "$DATA_DIR/build"
info "Bibliotheek: $DATA_DIR"
info "Klaar. Start met: $BIN --json -d '$DATA_DIR' serve --port $PORT"
