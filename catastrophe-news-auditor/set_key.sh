#!/bin/bash
# set_key.sh -- save an API key into .env next to the scripts, without it appearing on screen or in shell history.
#   ./set_key.sh OPENROUTER_API_KEY      then paste the key at the prompt and press Enter (nothing is echoed)
#   ./set_key.sh OPENAI_API_KEY
#   ./set_key.sh GEMINI_API_KEY
#   ./set_key.sh ANTHROPIC_API_KEY
#   ./set_key.sh NTFY_TOPIC
# Replaces the existing line for that name if there is one; leaves the other lines alone.
DIR="$(cd "$(dirname "$0")" && pwd)"
NAME="${1:-}"
if [ -z "$NAME" ]; then echo "usage: ./set_key.sh KEY_NAME   (e.g. OPENROUTER_API_KEY)"; exit 1; fi
printf 'Paste the value for %s and press Enter (it will not be shown): ' "$NAME"
read -r -s VALUE; echo
VALUE="$(printf '%s' "$VALUE" | tr -d '[:space:]')"
if [ -z "$VALUE" ]; then echo "nothing pasted; .env unchanged"; exit 1; fi
touch "$DIR/.env"
grep -v "^$NAME=" "$DIR/.env" > "$DIR/.env.tmp" 2>/dev/null
printf '%s=%s\n' "$NAME" "$VALUE" >> "$DIR/.env.tmp"
mv "$DIR/.env.tmp" "$DIR/.env"
chmod 600 "$DIR/.env"
echo "saved $NAME (${#VALUE} characters, starts ${VALUE:0:9}…) to $DIR/.env"
echo "lines now in .env: $(cut -d= -f1 "$DIR/.env" | tr '\n' ' ')"
