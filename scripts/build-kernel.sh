#!/usr/bin/env bash
# Assemble build/kernel/ from the Omarchy recipe at the commit sources.json pins
# plus the P16 delta in kernel/, then run makepkg there.
source "$(dirname "$0")/common.sh"
check_machine
[[ $EUID != 0 ]] || { echo 'makepkg als normaler Benutzer ausführen.' >&2; exit 1; }
pin=$(python3 -c 'import json, sys; k = json.load(open(sys.argv[1]))["kernel"]; print(k["repository"], k["commit"], k["path"])' "$REPO/sources.json")
read -r repository commit path <<<"$pin"
# A commit hash names its content, so the fetched recipe needs no checksum of its own.
upstream=$REPO/build/omarchy-pkgs
if [[ ! -d $upstream ]]; then
  git init -q "$upstream"
  git -C "$upstream" remote add origin "$repository"
fi
git -C "$upstream" sparse-checkout set "$path"
git -C "$upstream" fetch -q --depth=1 --filter=blob:none origin "$commit"
git -C "$upstream" -c advice.detachedHead=false checkout -q --force "$commit"
recipe=$REPO/build/kernel
rm -rf "$recipe"
cp -a "$upstream/$path" "$recipe"
shopt -s extglob
cp "$REPO"/kernel/!(PKGBUILD).patch "$recipe"/
patch -d "$recipe" -p1 --fuzz=0 --forward --batch --no-backup-if-mismatch -i "$REPO/kernel/PKGBUILD.patch"
# Isolated keyring: upstream signatures remain enabled.
export GNUPGHOME="$REPO/build/gnupg"
install -d -m700 "$GNUPGHOME"
gpg --batch --import "$recipe"/keys/pgp/*.asc
# Downloads stay outside the recipe, so a new assembly does not fetch them again.
export SRCDEST="$REPO/build/sources"
install -d "$SRCDEST"
cd "$recipe"
PKGEXT=.pkg.tar.zst makepkg --syncdeps --cleanbuild "$@"
