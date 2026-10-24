# Contributing to Lead2Meeting

Thank you for your interest in contributing to **Lead2Meeting**!

## Development Workflow

1. Fork the repository and create a feature branch (`git checkout -b feat/your-feature`).
2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Make your changes with clear validation and error handling.
4. Ensure all existing and new tests pass:
   ```bash
   python3 -m unittest discover -s tests -v
   ```
5. Verify that simulated demo behavior and live external integrations remain clearly separated (`execution_mode` must accurately reflect `simulated` vs `live`).
6. Open a Pull Request with a clear summary of the problem solved and verification steps performed.
