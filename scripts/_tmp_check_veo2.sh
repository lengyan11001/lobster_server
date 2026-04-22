#!/usr/bin/env bash
tail -300 /opt/lobster-server/logs/app.log 2>/dev/null | grep -iE "veo|video.generate|credits|deduct|billing|force_credits|pre_estimated|comfly|dry_run" | tail -30
