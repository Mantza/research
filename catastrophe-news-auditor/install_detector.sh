#!/bin/bash
# install_detector.sh -- schedule detect_cycle.sh every hour on this Mac with launchd.
#   ./install_detector.sh            install (or reinstall) and start
#   ./install_detector.sh --remove   stop and remove
# The job runs while the Mac is awake; missed runs while asleep are not caught up.
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="media.indicator.bna.detector"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$1" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
  rm -f "$PLIST"
  echo "removed $LABEL"; exit 0
fi

chmod +x "$DIR/detect_cycle.sh"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/detect_cycle.sh</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/detector_launchd.out</string>
  <key>StandardErrorPath</key><string>$DIR/detector_launchd.err</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "installed $LABEL: runs now and every hour; log in $DIR/detector_cycle.log"
