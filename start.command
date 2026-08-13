#!/bin/zsh

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR" || exit 1

"$SCRIPT_DIR/start.sh" "$@"
STATUS=$?

if [[ $STATUS -ne 0 ]]; then
  echo
  read "?启动失败。按回车键关闭此窗口..."
fi

exit $STATUS
