#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -d .git ]]; then
  echo "ERROR: .git directory not found. Run 'git init' (or clone) first." >&2
  exit 1
fi

touch .gitignore

append_ignore_if_missing() {
  local line="$1"
  if ! grep -Fxq "$line" .gitignore; then
    printf '%s\n' "$line" >> .gitignore
  fi
}

# Temporary/generated artifacts
append_ignore_if_missing "downlink_req-*.png"
append_ignore_if_missing "downlink_*.png"
append_ignore_if_missing "sattie-sky-hub_source_backup_*.tar.gz"

mkdir -p .githooks
cat > .githooks/pre-push <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

# Remove local temporary/generated artifacts before push.
rm -f downlink_req-*.png downlink_*.png sattie-sky-hub_source_backup_*.tar.gz || true
find . -name '.DS_Store' -type f -delete || true
EOF
chmod +x .githooks/pre-push

git config core.hooksPath .githooks

echo "Done."
echo "- .gitignore temp patterns ensured"
echo "- .githooks/pre-push installed"
echo "- git config core.hooksPath=.githooks"
