"""Run without installing: python run.py --help."""
import pathlib
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent/'src'))
from lean_to_paper.cli import main
main()
