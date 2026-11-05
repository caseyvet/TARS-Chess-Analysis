# TARS Chess Analyzer 1.0.0

TARS Chess Analyzer is a terminal UI for reviewing chess games with a local Stockfish engine.

### Highlights
- Analyze a player's latest Chess.com game or a game selected by URL.
- Load and analyze local PGN files.
- Review move classifications, evaluation changes, and Stockfish's suggested moves.
- View White, Black, combined, and key-move reports.
- Tune engine depth, thread count, and hash allocation.

### Before running
Install Python 3.10+, install the Python packages from `requirements.txt`, and ensure Stockfish is installed and available on your `PATH`.

### Known limitation
The displayed game rating is a rough placeholder estimate based on average centipawn loss; it is not Chess.com's official rating.
