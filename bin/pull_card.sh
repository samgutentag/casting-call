#!/bin/bash

# Pull new P4next takes off the card into a local inbox, then eject the card.
#
# The P4next is not the Mac's audio device while it is in USB File Transfer
# mode, so this does the minimum: copy takes not already pulled, eject, and
# tell you to leave File Transfer. ripa reads the inbox later, any time.
#
#   CARD_VOLUME      mounted card (default /Volumes/P4NEXT_SD)
#   CARD_INBOX       where takes go (default ~/Desktop/P4next-takes)
#   PULL_ALL_INPUTS  1 also copies MIC2 to MIC4 (unused, digital silence)
#   PULL_AGAIN       1 copies takes pulled before (normally skipped via <inbox>/.pulled)
#
# Nothing on the card is deleted or changed. Exits non-zero, and leaves the
# card mounted, if any copy fails.
#
# Usage: pull_card.sh

CARD_VOLUME="${CARD_VOLUME:-/Volumes/P4NEXT_SD}"
CARD_INBOX="${CARD_INBOX:-$HOME/Desktop/P4next-takes}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$CARD_VOLUME" ]; then
    echo "Error: no card at $CARD_VOLUME. On the P4next: Menu > USB File Transfer."; exit 2
fi
if ! mkdir -p "$CARD_INBOX"; then
    echo "Error: cannot create $CARD_INBOX"; exit 1
fi

flags=()
[ "${PULL_ALL_INPUTS:-0}" = "1" ] && flags+=(--all-inputs)
[ "${PULL_AGAIN:-0}" = "1" ] && flags+=(--again)

echo "Pulling takes from $CARD_VOLUME into $CARD_INBOX..."
( cd "$REPO_DIR" && python3 -m casting_call.pull "$CARD_VOLUME" "$CARD_INBOX" "${flags[@]}" )
status=$?
if [ "$status" -ne 0 ]; then
    echo "✗ Pull failed (status $status). The card is still mounted; fix the error and rerun."
    exit "$status"
fi

if [ "${CARD_NO_EJECT:-0}" = "1" ]; then
    echo "CARD_NO_EJECT=1: leaving $CARD_VOLUME mounted."; exit 0
fi
if ! diskutil eject "$CARD_VOLUME" >/dev/null; then
    echo "✗ Copied, but could not eject $CARD_VOLUME. Eject it in Finder."; exit 1
fi
echo ""
echo "████  Card ejected. Exit USB File Transfer on the P4next now.  ████"
