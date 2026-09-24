#!/bin/bash
# install_watcher.sh -- run start_watcher.sh every 60 seconds on this Mac with launchd.
#   ./install_watcher.sh            install (or reinstall) and start
#   ./install_watcher.sh --remove   stop and remove
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="media.indicator.bna.watcher"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ "$1" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null; rm -f "$PLIST"; echo "removed $LABEL"; exit 0
fi
chmod +x "$DIR/start_watcher.sh" "$DIR/run_event.sh"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/start_watcher.sh</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>AbandonProcessGroup</key><true/>
  <key>StandardOutPath</key><string>$DIR/watcher_launchd.out</string>
  <key>StandardErrorPath</key><string>$DIR/watcher_launchd.err</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "installed $LABEL: checks events/*/START every minute; log in $DIR/watcher.log"
