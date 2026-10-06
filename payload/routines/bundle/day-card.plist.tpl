<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- day-card.plist.tpl : the WAKING-HOURS variant of routine.plist.tpl,
     rendered at activation time by routines/bundle/day-card-ctl.sh.

     WHY A SECOND TEMPLATE. routine.plist.tpl carries ONE
     StartCalendarInterval dict, so routine-ctl.sh can only schedule a
     routine once a day (or once a week). The pinned day card needs to
     re-render through the day -- its whole point is that the past/now/ahead
     split moves -- so this template carries an ARRAY of intervals, one per
     waking hour. Everything else (token set, env block, log paths, .tpl
     extension keeping it outside mint's substitution globs and outside
     install.sh's auto-schedule glob) is identical to routine.plist.tpl on
     purpose: if routine-ctl.sh ever learns multi-interval scheduling, this
     file folds back into it with no change to the caller.

     The token roster lives in day-card-ctl.sh's sed block, not here: a token
     named inside this comment would be substituted into the rendered plist
     and leave the installed unit explaining itself in someone else's values. -->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>__LABEL__</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>__SCRIPT__</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>__INTERVALS__
    </array>
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
