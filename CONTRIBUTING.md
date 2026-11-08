# Contributing

Thank you for helping. Bug reports, format fixes and small features are all welcome.

## Reporting a parsing problem

NSE changes its files from time to time. If something does not parse, open an issue and include:

- the command or function you ran, and the full error;
- the data source (snapshot CSV or historical API, plus `--kind`);
- a few **anonymised** sample rows, or just the header line. Please do not paste large data dumps.

## Making a change

1. Fork the repository and create a branch.
2. Install it in editable mode: `pip install -e ".[test]"`.
3. Add or update a test. Tests must not use the network. Use the fake session in `tests/test_client_cli.py` and synthetic fixture data.
4. Run `pytest` and make sure all tests pass.
5. Open a pull request that explains what changed and why.

Guidelines:

- Keep `requests` as the only runtime dependency, and support Python 3.9.
- Keep the parsers pure (no I/O) and tolerant of messy input.
- Be respectful of NSE's servers. Do not add features that poll aggressively or bypass rate limits or access controls.
- Keep `widget/index.html` as a single self-contained file.

By contributing, you agree that your contributions are licensed under the MIT License.
