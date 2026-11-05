# TARS Chess Analyzer

A terminal-based application that reviews Chess.com games with a locally installed Stockfish engine. It can fetch a player's latest archived game or a game supplied by URL, and it can analyze a local PGN file.

Development environment: Developed and tested on Linux; tested on Windows with added compatibility features.

## Features

- Fetch the latest archived game for a Chess.com username, or select a game by URL.
- Analyze local `.pgn` files without a Chess.com username.
- Compare each played move with Stockfish's top recommendation.
- Classify moves as good, inaccuracy, mistake, or blunder.
- Inspect move-by-move evaluations, key moves, and player-specific reports.
- Configure Stockfish depth, CPU threads, and hash memory.

> **Rating estimate:** The displayed game rating is a rough placeholder derived from average centipawn loss. It is not Chess.com's official Game Rating.

## Requirements

- Python 3.10 or newer
- Stockfish installed and available on your `PATH`
- A terminal that supports Textual's terminal UI

## Installation

### 1. Install Stockfish

Install Stockfish using your operating system's package manager, or download it from the [official Stockfish website](https://stockfishchess.org/download/).

On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install stockfish
```

Verify that it is available:

```bash
stockfish
```

Exit the engine with `quit`.

### 2. Install Python dependencies

Clone or download this repository, then run:

```bash
python -m pip install -r requirements.txt
```

## Run

From the project directory:

```bash
python chess_analyze.py
```

Enter a Chess.com username and optionally a game URL, then press **ANALYZE** or the use the F5 key. To analyze a local PGN instead, enter its path in the PGN field; the PGN file takes precedence over the username and game URL.

## Windows setup

### 1. Install the requirements

- **Python 3** from python.org. During setup, check **Add python.exe to PATH**.
- **Git for Windows** from git-scm.com (optional, if you clone the repo).

Then install the Python packages in PowerShell:

```powershell
python -m pip install chess textual rich
```

### 2. Get the project

```powershell
git clone https://github.com/caseyvet/TARS-Chess-Analysis.git
cd TARS-Chess-Analysis
```

You can also download the ZIP from the repo page (**Code → Download ZIP**) and extract it.

### 3. Install Stockfish

Stockfish is not included. Download the Windows build from https://stockfishchess.org, then use either option below.

**Option A: Project folder (recommended)**

Create a `Stockfish` folder next to `chess_analyze.py` and put the `.exe` inside. The name can be `stockfish.exe` or the long build name from the download, such as `stockfish-windows-x86-64-avx2.exe`.

**Option B: Environment variable**

Set `STOCKFISH_PATH` to the full path of the executable, in quotes if the path contains spaces:

```powershell
setx STOCKFISH_PATH "C:\Tools\Stockfish\stockfish.exe"
```

Open a new PowerShell window after running `setx`. The variable is not set in the window where you ran it.

The app checks, in order: `STOCKFISH_PATH`, the system PATH, then the `Stockfish` folder next to the script.

### 4. Run the app

```powershell
python chess_analyze.py
```

If you see "Stockfish not found," confirm the file is at `Stockfish\stockfish.exe` with `dir Stockfish`, or set `STOCKFISH_PATH` as described above.

### Troubleshooting

- **`python` is not recognized:** reinstall Python and check **Add python.exe to PATH**, or try `py chess_analyze.py`.
- **`git` is not recognized:** close and reopen PowerShell after installing Git, or use Git Bash.
- **Missing module errors:** run `python -m pip install chess textual rich` again in the same Python that runs the script.
- **Garbled symbols in the app:** use Windows Terminal instead of the older Command Prompt window.

### Controls

| Key | Action |
| --- | --- |
| `F1` | White report |
| `F2` | Black report |
| `F3` | Combined report |
| `F4` | Key moves |
| `F5` | Analyze |
| `R` | Reset |
| `Q` | Quit |

## Analysis settings

- **Depth** — search depth; higher values generally improve analysis but take longer.
- **Threads** — number of Stockfish CPU threads, up to the number of cores your computer has.
- **Hash (MB)** — memory allocated to Stockfish's transposition table.

The defaults are intended as a reasonable starting point. Increasing depth, threads, or hash can increase resource use.
## Notes

- Chess.com game retrieval uses its public API and requires an internet connection.
- PGN analysis is local after the file is loaded.
- Stockfish must be installed separately. It is not bundled with this project.
- This project is not affiliated with Chess.com or the Stockfish project.
- The optional **Game URL** box takes a link to a single game on Chess.com. The URL must end with the numeric game ID, optionally followed by a forward slash.
- Accepted URL formats:
  - `chess.com/game/live/<game-id>`
  - `chess.com/game/daily/<game-id>`
  - `chess.com/analysis/game/live/<game-id>`
  - `chess.com/analysis/game/daily/<game-id>`
- The game must belong to the username you entered.

## License

This project is licensed under the GNU General Public License v3.0. See the LICENSE file for details.
