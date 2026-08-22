"""Entry point: python -m qwerty_instrument"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from . import config as config_mod
from .app import Application, setup_logging

logger = logging.getLogger("qwerty_instrument")


def main() -> int:
    log_dir = config_mod.user_data_dir() / "logs"
    setup_logging(log_dir)

    qt_app = QApplication(sys.argv)

    try:
        app = Application()
    except Exception as exc:
        logger.exception("failed to initialize application")
        QMessageBox.critical(None, "Startup Error", f"Failed to initialize:\n{exc}")
        return 1

    try:
        app.start()
    except Exception as exc:
        logger.exception("failed to start audio/input")
        QMessageBox.critical(
            None,
            "Startup Error",
            f"Failed to start audio or keyboard capture:\n{exc}\n\n"
            "Try Audio Settings to pick a different device, or run "
            "tools\\audio_diagnostics.py for more detail.",
        )
        return 1

    from .ui.main_window import MainWindow

    is_first_run = not config_mod.default_config_path().exists()
    if is_first_run:
        from .ui.first_run_wizard import FirstRunWizard

        wizard = FirstRunWizard(app)
        wizard.exec()

    window = MainWindow(app)
    window.show()

    exit_code = qt_app.exec()
    app.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
