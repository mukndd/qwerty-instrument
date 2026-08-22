"""Manual (non-pytest) smoke test: launches the real Application (audio
engine + Win32 keyboard hook) and the real MainWindow, pumps the Qt event
loop briefly, then shuts everything down cleanly. Verifies the whole
stack wires together without exceptions -- does not simulate key presses
(that needs a physical keyboard) or require the user to interact.
"""

from __future__ import annotations

import sys
import time

from PySide6.QtWidgets import QApplication

from qwerty_instrument.app import Application, setup_logging


def main() -> int:
    setup_logging()
    qt_app = QApplication(sys.argv)

    app = Application()
    app.start()
    print("Application started OK. capture_enabled=", app.keyboard.capture_enabled)
    print("active_instrument=", app.engine.active_instrument_name)
    print("hook_handle=", app._hook._hook_handle)

    from qwerty_instrument.ui.main_window import MainWindow

    window = MainWindow(app)
    window.show()

    # Pump the event loop for ~2 seconds without blocking on exec()
    end = time.time() + 2.0
    while time.time() < end:
        qt_app.processEvents()
        time.sleep(0.01)

    print("UI ticked OK for 2s. Shutting down...")
    window.close()
    app.stop()
    print("Shutdown OK. hook_handle after stop:", app._hook._hook_handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
