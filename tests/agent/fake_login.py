"""A stand-in for a sign-in command, printing what the real ones print (docs/visualizer-agent.md §5).

    fake_login.py claude|codex|slow

`claude`: a URL (also as an OSC 8 hyperlink), then waits at the paste prompt; the code `good#state` signs
in, anything else fails as the real command does. `codex`: a device URL and code, then "signs in" after a
moment. `slow`: prints nothing and waits.
"""

import sys
import time

mode = sys.argv[1]
if not sys.stdout.isatty():
    sys.exit("not a terminal")
URL = "https://claude.example/oauth/authorize?code=true&client_id=x&state=abc"
if mode == "claude":
    sys.stdout.write("Opening browser to sign in…\r\nIf the browser didn't open, visit: "
                     f"\x1b]8;;{URL}\x07\x1b[94m{URL}\x1b[39m\x1b]8;;\x07\r\nPaste code here if prompted > ")
    sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code == "good#state":
        sys.stdout.write("\r\nLogin successful.\r\n")
        sys.exit(0)
    sys.stdout.write("\r\nLogin failed: That code wasn't accepted. Sign in again to get a new code.\r\n")
    sys.exit(1)
if mode == "codex":
    sys.stdout.write("Follow these steps to sign in with ChatGPT using device code authorization:\r\n\r\n"
                     "1. Open this link in your browser and sign in to your account\r\n"
                     "   \x1b[94mhttps://auth.example/codex/device\x1b[0m\r\n\r\n"
                     "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\r\n"
                     "   \x1b[94mABCD-12EFG\x1b[0m\r\n")
    sys.stdout.flush()
    time.sleep(1.0)
    sys.stdout.write("Successfully logged in\r\n")
    sys.exit(0)
time.sleep(600)
