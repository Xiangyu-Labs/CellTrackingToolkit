#!/bin/zsh

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR" || exit 1

"$SCRIPT_DIR/start.sh" "$@"
STATUS=$?

if [[ $STATUS -ne 0 ]]; then
  echo
  read "?Startup failed. Press Return to close this window..."
fi

exit $STATUS
