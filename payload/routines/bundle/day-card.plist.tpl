<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- Existing day-card unit: five-minute wakeups, with the waking window
     and publication gate enforced by day-card.sh in tick mode. -->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>__LABEL__</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>__SCRIPT__</string>
        <string>--tick</string>
        <string>--from-hour</string>
        <string>__FROM_H__</string>
        <string>--to-hour</string>
        <string>__TO_H__</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>__HOMEDIR__/.local/bin:__HOMEDIR__/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>__HOMEDIR__</string>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>__ROOT__/.telegram_bot/logs/__LOGNAME__.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>__ROOT__/.telegram_bot/logs/__LOGNAME__.stderr.log</string>
    <key>WorkingDirectory</key>
    <string>__ROOT__</string>
</dict>
</plist>
