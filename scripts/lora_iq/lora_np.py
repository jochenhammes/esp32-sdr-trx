"""The numpy LoRa PHY lives in src/espdr/lora_phy.py (the transmitter's `-m lora` uses it too); the scripts import it from here."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
from espdr.lora_phy import *  # noqa: F401,F403,E402
from espdr.lora_phy import _parabolic, _peaks, _windows  # noqa: F401,E402
