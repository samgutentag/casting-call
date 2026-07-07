#!/bin/bash
# Stream Deck / double-click front end for `callcheck --live`.
#
# Opens a small Terminal window, captures 6 seconds from the recording
# device, prints the per-channel verdict, and fires a macOS notification
# so the result lands even if the window is behind the Meet window.
#
# Stream Deck setup (one time):
#   1. Drag the built-in "System: Open" action onto a key
#   2. Point it at this file
#   3. Optional: label the key "callcheck" and set a mic icon
#
# Talk while it captures, and make sure the far side has audio playing,
# or a perfectly live channel will read dead.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "casting-call · live channel check"
echo ">>> TALK NOW, and make sure the other side is audible <<<"
echo

if bash "$REPO_DIR/bin/callcheck.sh" --live; then
    osascript -e 'display notification "Both channels capturing." with title "callcheck: PASS" sound name "Glass"'
    echo
    echo "PASS - record away."
    rc=0
else
    osascript -e 'display notification "A channel looks DEAD. Fix routing before recording." with title "callcheck: FAIL" sound name "Basso"'
    echo
    echo "FAIL - a channel is dead. Check Wave Link routing before you trust this recording."
    rc=1
fi

echo
read -n 1 -s -r -p "press any key to close..."
exit $rc
