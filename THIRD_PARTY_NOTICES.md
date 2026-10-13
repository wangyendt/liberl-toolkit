# Third-party notices

The MIT license in this repository covers Liberl Toolkit's own source code.
Dependencies retain their licenses. The Windows release includes the relevant
license texts in its `licenses` folder.

| Component | Version in the Windows release | License / source |
|---|---|---|
| Python | 3.14.0 | PSF license; https://www.python.org/downloads/release/python-3140/ |
| Qt, PySide6, Shiboken6 | 6.12.0 | LGPL/GPL/commercial licensing; https://code.qt.io/cgit/qt/ and https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| python-zstandard | 0.25.0 | BSD-3-Clause; https://github.com/indygreg/python-zstandard |
| PyInstaller bootloader | 6.22.3 | GPL with bootloader exception; https://github.com/pyinstaller/pyinstaller |

The application does not modify these dependencies. Its source and build spec
are provided so it can be rebuilt with replacement compatible libraries. For
an unpacked build with replaceable Qt DLLs, use the command in the README.

No Falcom executable, asset archive, extracted game table, save file, memory dump,
third-party trainer, or reference repository is included. Game tables are read
from the user's own installation. Falcom and the game's trademarks belong to
their respective owners; this is an independent fan-made utility.
