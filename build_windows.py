"""Build only this repository's read-only desktop application."""
import os
from pathlib import Path
import PyInstaller.__main__

os.chdir(Path(__file__).resolve().parent)
PyInstaller.__main__.run([
    'app.py', '--name=meme-holdings-readonly', '--onefile', '--windowed', '--noconfirm',
    '--recursive-copy-metadata=web3', '--copy-metadata=py_ecc',
    '--collect-data=eth_utils', '--collect-data=web3', '--collect-data=ens',
    '--exclude-module=pytest', '--exclude-module=IPython',
    '--exclude-module=matplotlib', '--exclude-module=numpy', '--exclude-module=pandas',
])
