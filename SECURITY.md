# Security and privacy

This is experimental local device-control software. No network access is needed
by the Python controller. Google platform-tools may create their local ADB server.
Do not disable antivirus, OS security or device protections to install this tool.

Report bugs with synthetic identifiers and a minimal reproduction. Never post
IMEIs, serials, account details, raw private logs or unlock codes publicly.

There is no guaranteed non-destructive code-verification command. Every run must
receive explicit informed consent before attempting `fastboot oem unlock`.

The state machine retries connection failures, not uncertain unlock outcomes.
If a code command times out, inspect the phone and preserve its journal before
deciding how to recover. A saved accepted code is not proof of completed unlock.
