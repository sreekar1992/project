#!/bin/zsh
# Double-click in Finder to start the local ECG Workbench.
cd "${0:A:h}" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Create the virtual environment and install the project first; see README.md."
  read -r "?Press Enter to close."
  exit 1
fi
exec .venv/bin/python -m ecg_cvd.gui --project "$PWD" --open
