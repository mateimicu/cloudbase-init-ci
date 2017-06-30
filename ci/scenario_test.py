"""Check if any scenario name is prefix in anonther."""
from __future__ import print_function
import re

TEST_PATH = "tests.py"

def is_prefix(prefix, base):
    """Check if `base` starts with prefix"""
    return base.startswith(prefix) and prefix != base

if __name__ == "__main__":
    try:
        DATA = open(TEST_PATH, "r").read()
    except IOError:
        print("Can't find file {}".format(TEST_PATH))

    SCENARIO_NAMES = re.findall("class ([a-zA-Z]+)\(.+", DATA)

    for scenario in SCENARIO_NAMES:
        match = [comp for comp in SCENARIO_NAMES if is_prefix(scenario, comp)]
        if match:
            print("Scenario {} is prefix in {}".format(
                scenario, match))
