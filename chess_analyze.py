#!/usr/bin/env python3
"""TARS Chess Analyzer: a terminal app that reviews your Chess.com games with Stockfish."""

import os
import re
import json
import shutil
import sys
import platform
import urllib.request
import urllib.error
from io import StringIO

import chess
import chess.pgn
import chess.engine

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Input, Button, Static, DataTable, RichLog
from textual.binding import Binding
from textual import work
from rich.markup import escape
from rich.text import Text


API = "https://api.chess.com/pub/player"

USER_AGENT = "TARS-Chess-Analyzer/1.0.0 (https://github.com/caseyvet/tars-chess-analysis)"

ANALYSIS_DEPTH = 20
ENGINE_THREADS = 6
ENGINE_HASH = 512

DEPTH_RANGE = (1, 60)
THREAD_RANGE = (1, os.cpu_count() or 1)
HASH_RANGE = (1, 8192)

SETTING_HINTS = {
    "depth": (
        "Depth: how many moves ahead Stockfish looks. Higher is more "
        "accurate but slower. 20 is a good starting point."
    ),
    "threads": (
        "Threads: how many CPU cores Stockfish uses. More is faster, "
        "up to the number of cores your computer has."
    ),
    "hash": (
        "Hash: memory in MB that Stockfish uses to remember positions. "
        "More helps on long games. 512 is a good starting point."
    ),
}

# A forced mate counts as this many centipawns (10 pawns) when sorting and
# classifying. Mates are shown as M-notation, never as a pawn figure.
MATE_CP = 1000

# Game rating is an estimate. Chess.com's formula isn't public, so
# this maps average centipawn loss to a rough rating: BASE - PER_CP * ACPL.
# Calibrate these against games where you know the real Game Rating.
PERF_BASE = 3000
PERF_PER_CP = 20
PERF_MIN = 400
PERF_MAX = 3200

RATING_NOTE = "[dim]Estimated from move quality (placeholder formula, not Chess.com's).[/dim]\n"

CATEGORIES = ("GOOD", "INACCURACY", "MISTAKE", "BLUNDER")

CATEGORY_COLORS = {
    "GOOD": "green",
    "INACCURACY": "dark_orange",
    "MISTAKE": "yellow",
    "BLUNDER": "red",
}


def parse_setting(text, name, allowed):
    """Turn a settings box into a whole number, or raise ValueError with a plain message."""
    low, high = allowed
    text = text.strip()

    if not text:
        raise ValueError(f"{name} is empty. Type a whole number from {low} to {high}.")

    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"{name} must be a whole number like {low}, not '{text}'.")

    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}, not {value}.")

    return value


def read_pgn_file(path):
    """Read a PGN file from disk, with a plain message if it can't be used."""
    path = os.path.expanduser(path.strip().strip("'\""))

    if not os.path.isfile(path):
        raise ValueError(f"No PGN file found at {path}")

    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    if not text.strip():
        raise ValueError(f"The PGN file is empty: {path}")

    return text


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())

def get_player_title(username):
    try:
        profile = get_json(
            f"https://api.chess.com/pub/player/{username.lower()}"
        )

        title = profile.get("title", "")

        valid_titles = {
            "GM", "IM", "FM", "CM",
            "WGM", "WIM", "WFM", "WCM"
        }

        return title if title in valid_titles else ""

    except Exception:
        return ""

def get_game(username, game_url=None):
    archives = get_json(f"{API}/{username}/games/archives")["archives"]

    if game_url:
        match = re.search(r"(\d+)/?$", game_url)

        if not match:
            raise ValueError("Invalid Chess.com game URL.")

        game_id = match.group(1)

        for archive in reversed(archives):
            for game in get_json(archive)["games"]:
                if game.get("url", "").rstrip("/").endswith("/" + game_id):
                    return game

        raise ValueError("Game not found in this account's archives.")

    for archive in reversed(archives):
        games = get_json(archive)["games"]

        if games:
            return games[-1]

    raise ValueError("No games found for this account.")


def mover_eval(pov_score):
    """Return (centipawns, mate) from the mover's point of view.

    mate is None when there's no forced mate. Otherwise it's the signed number
    of moves to mate: positive means the mover mates, negative means they're mated.
    """
    mate = pov_score.mate()

    if mate is None:
        return pov_score.score(), None

    sign = 1 if mate >= 0 else -1

    return sign * (MATE_CP - abs(mate)), mate


def terminal_eval(board):
    """Evaluate a finished position. The side that just moved is not on move,
    so a checkmate on the board means the mover delivered it."""
    if board.is_checkmate():
        return MATE_CP, 0

    return 0, None


def format_eval(cp, mate):
    if mate is None:
        return f"{cp / 100:+.2f}"

    if mate == 0:
        return "Mate"

    if mate > 0:
        return f"M{mate}"

    return f"-M{abs(mate)}"


def mate_note(before_mate, after_mate):
    """Return (note, forced_category), or (None, None) if no mate is involved."""
    had_mate = before_mate is not None and before_mate > 0
    keeps_mate = after_mate is not None and after_mate >= 0

    if had_mate and not keeps_mate:
        return f"Missed M{before_mate}", "BLUNDER"

    was_mated = before_mate is not None and before_mate < 0
    now_mated = after_mate is not None and after_mate < 0

    if now_mated and not was_mated:
        return f"Allowed M{abs(after_mate)}", "BLUNDER"

    return None, None


def classify_loss(loss):
    if loss >= 150:
        return "BLUNDER"
    if loss >= 80:
        return "MISTAKE"
    if loss >= 30:
        return "INACCURACY"
    return "GOOD"


def loss_text(item):
    if item.get("note"):
        return item["note"]

    return f"{item['loss'] / 100:.2f} pawns"


def eval_line(item):
    return f"{item['eval_before']} → {item['eval_after']}"


def count_categories(items):
    counts = {category: 0 for category in CATEGORIES}

    for item in items:
        counts[item["category"]] += 1

    return counts


# Chess.com result codes, from its published data API
LOSS_TEXT = {
    "checkmated": "was checkmated",
    "resigned": "resigned",
    "timeout": "lost on time",
    "abandoned": "abandoned the game",
    "kingofthehill": "lost (king reached the hill)",
    "threecheck": "lost (three checks)",
    "bughousepartnerlose": "lost (partner lost)",
    "lose": "lost",
}

DRAW_TEXT = {
    "agreed": "Draw by agreement",
    "repetition": "Draw by repetition",
    "stalemate": "Draw by stalemate",
    "insufficient": "Draw by insufficient material",
    "50move": "Draw by the 50-move rule",
    "timevsinsufficient": "Draw: timeout vs insufficient material",
}


def title_text(title):
    """A red '[GM] ' prefix as a Text object, or an empty Text when there's no title."""
    text = Text()

    if title:
        text.append(f"[{title}] ", style="bold red")

    return text


def describe_ending(game, headers, final_board):
    """Plain-English ending, e.g. 'Black resigned. White wins.' or 'Draw by repetition.'"""
    white = game.get("white", {}).get("result")
    black = game.get("black", {}).get("result")

    for result in (white, black):
        if result in DRAW_TEXT:
            return DRAW_TEXT[result]

    if white == "win" and black in LOSS_TEXT:
        return f"Black {LOSS_TEXT[black]}. White wins."

    if black == "win" and white in LOSS_TEXT:
        return f"White {LOSS_TEXT[white]}. Black wins."

    return headers.get("Termination", "Unknown ending")


def average_loss(results, color):
    losses = [item["loss"] for item in results if item["color"] == color]

    if not losses:
        return 0

    return sum(losses) / len(losses)


def estimate_performance(acpl):
    estimate = PERF_BASE - PERF_PER_CP * acpl
    clamped = max(PERF_MIN, min(PERF_MAX, estimate))

    # Round to the nearest 50 so it reads as a rough estimate, not a precise rating
    return 50 * round(clamped / 50)


def find_stockfish():
    """Return the path to a Stockfish executable, or None if it can't be found.

    Checks, in order:
      1. STOCKFISH_PATH (a full path to the file, or to the folder holding it)
      2. The system PATH (shutil.which also tries .exe on Windows operating systems)
      3. A Stockfish folder next to the script or executable. Accepts
         stockfish.exe, or a downloaded build name such as
         stockfish-windows-x86-64-avx2.exe.
    """
    is_windows = platform.system() == "Windows"
    exe_name = "stockfish.exe" if is_windows else "stockfish"

    def usable(path):
        if not os.path.isfile(path):
            return False
        if is_windows:
            return path.lower().endswith(".exe")
        return os.access(path, os.X_OK) or _make_executable(path)

    # 1. Manual override
    override = os.environ.get("STOCKFISH_PATH", "").strip().strip('"')
    if override:
        if os.path.isdir(override):
            override = os.path.join(override, exe_name)
        if usable(override):
            return override

    # 2. PATH
    found = shutil.which("stockfish")
    if found:
        return found

    # 3. Stockfish folder next to the script or the packaged exe
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    folder = os.path.join(base_dir, "Stockfish")

    if os.path.isdir(folder):
        preferred = os.path.join(folder, exe_name)
        if usable(preferred):
            return preferred

        for name in sorted(os.listdir(folder)):
            if name.lower().startswith("stockfish"):
                candidate = os.path.join(folder, name)
                if usable(candidate):
                    return candidate

    return None


def _make_executable(path):
    """Try to add the execute bit to a downloaded binary on macOS or Linux."""
    try:
        os.chmod(path, 0o755)
        return True
    except OSError:
        return False


class ChessAnalyzer(App):

    TITLE = "TARS CHESS ANALYZER"
    SUB_TITLE = "LOCAL STOCKFISH ANALYSIS"

    CSS = """
    Screen {
        background: #10151e;
        color: #dce6f2;
    }

    Header {
        background: #192638;
        color: #7dcfff;
    }

    Footer {
        background: #192638;
    }

    #top {
        height: 3;
        padding: 0 1;
        background: #192638;
    }

    #pgnrow {
        height: 3;
        padding: 0 1;
        background: #192638;
    }

    #pgnfile {
        width: 1fr;
    }

    #username {
        width: 1fr;
    }

    #gameurl {
        width: 2fr;
    }

    #analyze {
        width: 18;
        background: #176b87;
        color: white;
    }

    #settings {
        height: auto;
        padding: 0 1;
        background: #14202e;
    }

    .setting {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }

    .setting-label {
        color: #7dcfff;
        text-style: bold;
    }

    .hint {
        color: #7f93a8;
    }

    #status {
        height: 3;
        padding: 1;
        color: #7dcfff;
        background: #14202e;
    }

    #matchup {
        height: 3;
        padding: 1;
        background: #192638;
        color: #7dcfff;
        text-style: bold;
        content-align: center middle;
    }

    #main {
        height: 1fr;
    }

    #moves-panel {
        width: 2fr;
        border: round #35506c;
        margin: 1;
    }

    #details-panel {
        width: 3fr;
        border: round #35506c;
        margin: 1;
    }

    #moves {
        height: 1fr;
    }

    #move-detail {
        height: 14;
        padding: 0 1;
        border: round #35506c;
        color: #dce6f2;
    }

    #report {
        height: 1fr;
        padding: 1;
    }

    .heading {
        color: #7dcfff;
        text-style: bold;
        padding: 1;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reset", "Reset"),
        Binding("f1", "show_white_report", "White Report"),
        Binding("f2", "show_black_report", "Black Report"),
        Binding("f3", "show_combined_report", "Combined Report"),
        Binding("f4", "show_key_moves", "Key Moves"),
        Binding("f5", "analyze", "Analyze"),
    ]

    def __init__(self):
        super().__init__()

        self.move_data = []
        self.game_data = None
        self.game_headers = {}
        self.final_board = None
        self.white_player = "Unknown"
        self.black_player = "Unknown"
        self.white_title = ""
        self.black_title = ""

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal(id="top"):
            yield Input(placeholder="Chess.com username", id="username")
            yield Input(placeholder="Optional game URL", id="gameurl")
            yield Button("ANALYZE", id="analyze")

        with Horizontal(id="pgnrow"):
            yield Input(placeholder="Optional PGN file path, e.g. ~/games/game.pgn (overrides username)", id="pgnfile")

        with Horizontal(id="settings"):

            with Vertical(classes="setting"):
                yield Static("Depth", classes="setting-label")
                yield Input(value=str(ANALYSIS_DEPTH), id="depth")
                yield Static(SETTING_HINTS["depth"], classes="hint")

            with Vertical(classes="setting"):
                yield Static("Threads", classes="setting-label")
                yield Input(value=str(min(ENGINE_THREADS, THREAD_RANGE[1])), id="threads")
                yield Static(SETTING_HINTS["threads"], classes="hint")

            with Vertical(classes="setting"):
                yield Static("Hash (MB)", classes="setting-label")
                yield Input(value=str(ENGINE_HASH), id="hash")
                yield Static(SETTING_HINTS["hash"], classes="hint")

        yield Static("READY — Enter your Chess.com username, then press ANALYZE.", id="status")

        yield Static("WHITE: Unknown                         BLACK: Unknown", id="matchup")

        with Horizontal(id="main"):

            with Vertical(id="moves-panel"):
                yield Static("MOVE HISTORY", classes="heading")
                yield DataTable(id="moves")
                yield Static("Click or arrow to a move to see its details.", id="move-detail")

            with Vertical(id="details-panel"):
                yield Static("ANALYSIS REPORT", id="report-heading", classes="heading")
                yield RichLog(id="report", wrap=True, markup=True)

        yield Footer()

    def on_mount(self):
        table = self.query_one("#moves", DataTable)
        table.add_columns("Move", "Side", "Played", "Classification")
        table.cursor_type = "row"

        self.query_one("#report", RichLog).write(
            "[bold cyan]TARS CHESS ANALYZER[/bold cyan]\n\n"
            "[green]Stockfish engine interface initialized.[/green]\n\n"
            "[bold]How to start:[/bold]\n"
            "1. Type your Chess.com username (Tab moves between boxes).\n"
            "2. Optional: paste a game URL, or a PGN file path below.\n"
            "3. Press ANALYZE or F5.\n\n"
            "[bold]Tip:[/bold] Leave the settings alone if you're not sure. "
            "They're already set to sensible values.\n\n"
            "[bold]Keys:[/bold] F1 White report, F2 Black report, "
            "F3 Full report, F4 Key moves, Q quit."
        )

    def reset_heading(self):
        self.query_one("#report-heading", Static).update("ANALYSIS REPORT")

    def set_status(self, message):
        self.query_one("#status", Static).update(message)

    @work(thread=True, exclusive=True)
    def load_game(self, username, game_url, depth, threads, hash_mb, pgn_path=""):
        try:
            if pgn_path:
                self.call_from_thread(self.set_status, "READING PGN FILE...")
                game = {}
                pgn_text = read_pgn_file(pgn_path)
            else:
                self.call_from_thread(self.set_status, "CONNECTING TO CHESS.COM...")
                game = get_game(username, game_url or None)
                pgn_text = game.get("pgn")

            if not pgn_text:
                raise ValueError("Chess.com did not provide a PGN.")

            pgn = chess.pgn.read_game(StringIO(pgn_text))

            if pgn is None:
                raise ValueError("Could not read the game's PGN.")

            moves = list(pgn.mainline_moves())
            final_board = pgn.end().board()

            if not moves:
                raise ValueError("This game contains no moves.")

            engine_path = find_stockfish()

            if not engine_path:
                raise RuntimeError(
                    "Stockfish not found. Put stockfish.exe (Windows) or "
                    "stockfish (macOS/Linux) in a Stockfish folder next to "
                    "this script, add it to PATH, or set STOCKFISH_PATH."
                )

            results = []

            self.call_from_thread(self.set_status, f"STARTING STOCKFISH — {len(moves)} MOVES")

            engine = chess.engine.SimpleEngine.popen_uci(engine_path)

            try:
                engine.configure({"Threads": threads, "Hash": hash_mb})

                board = pgn.board()

                for index, move in enumerate(moves):
                    mover = board.turn
                    move_number = board.fullmove_number
                    color = "White" if mover == chess.WHITE else "Black"

                    before = engine.analyse(board, chess.engine.Limit(depth=depth))

                    if not before.get("pv"):
                        raise RuntimeError("Stockfish returned no principal variation.")

                    best_san = board.san(before["pv"][0])
                    played_san = board.san(move)

                    before_cp, before_mate = mover_eval(before["score"].pov(mover))

                    board.push(move)

                    if board.is_game_over():
                        after_cp, after_mate = terminal_eval(board)
                    else:
                        after = engine.analyse(board, chess.engine.Limit(depth=depth))
                        after_cp, after_mate = mover_eval(after["score"].pov(mover))

                    loss = max(0, before_cp - after_cp)

                    note, forced_category = mate_note(before_mate, after_mate)

                    results.append({
                        "number": move_number,
                        "color": color,
                        "played": played_san,
                        "best": best_san,
                        "loss": loss,
                        "note": note,
                        "category": forced_category or classify_loss(loss),
                        "eval_before": format_eval(before_cp, before_mate),
                        "eval_after": format_eval(after_cp, after_mate),
                        "fen": board.fen(),
                    })

                    self.call_from_thread(
                        self.set_status,
                        f"ANALYZING — MOVE {index + 1}/{len(moves)}",
                    )

            finally:
                engine.quit()

            if pgn_path:
                self.white_title = ""
                self.black_title = ""
            else:
                self.white_title = get_player_title(pgn.headers.get("White", ""))
                self.black_title = get_player_title(pgn.headers.get("Black", ""))

            self.call_from_thread(
                self.show_results,
                pgn.headers,
                game,
                results,
                final_board
            )

        except urllib.error.HTTPError as error:
            self.call_from_thread(self.set_status, f"CHESS.COM HTTP ERROR: {error.code}")

        except urllib.error.URLError as error:
            self.call_from_thread(self.set_status, f"NETWORK ERROR: {error.reason}")

        except Exception as error:
            self.call_from_thread(self.set_status, f"ERROR: {error}")

    def write_rating(self, report, side):
        acpl = average_loss(self.move_data, side)

        key = "WhiteElo" if side == "White" else "BlackElo"

        report.write(
            f"Game rating: {estimate_performance(acpl)}\n"
            f"Player rating: {self.game_headers.get(key, '?')}\n"
            f"Average loss: {acpl / 100:.2f} pawns\n"
        )

    def show_results(self, headers, game, results, final_board):
        self.reset_heading()
        self.game_headers = headers
        self.final_board = final_board
        self.game_data = game
        self.move_data = results
        self.white_player = headers.get("White", "Unknown")
        self.black_player = headers.get("Black", "Unknown")
        ending = describe_ending(game, headers, final_board)

        matchup = Text()
        matchup.append_text(title_text(self.white_title))
        matchup.append(f"WHITE: {self.white_player}    |    ", style="bold #7dcfff")
        matchup.append_text(title_text(self.black_title))
        matchup.append(f"BLACK: {self.black_player}    |    {ending}", style="bold #7dcfff")

        self.query_one("#matchup", Static).update(matchup)

        table = self.query_one("#moves", DataTable)
        table.clear()

        report = self.query_one("#report", RichLog)
        report.clear()

        report.write(
            "[bold cyan]GAME INFORMATION[/bold cyan]\n"
            f"White: {escape(headers.get('White', 'Unknown'))}\n"
            f"Black: {escape(headers.get('Black', 'Unknown'))}\n"
            f"Result: {escape(headers.get('Result', 'Unknown'))}\n"
            f"Total moves: {len(results)}\n"
            f"Game URL: {escape(game.get('url', 'Unknown'))}\n"
            f"Ending: {escape(ending)}\n"
            "\n[bold cyan]MOVE CLASSIFICATION[/bold cyan]\n"
        )

        for index, item in enumerate(results):
            table.add_row(
                str(item["number"]),
                item["color"],
                item["played"],
                item["category"],
                key=str(index),
            )

            if item["category"] != "GOOD":
                player = self.white_player if item["color"] == "White" else self.black_player
                color = CATEGORY_COLORS[item["category"]]

                report.write(
                    f"\n[{color}][bold]{item['category']}[/bold][/]\n"
                    f"Player: {escape(player)} ({item['color']})\n"
                    f"Move: {item['number']}\n"
                    f"Played: {item['played']}\n"
                    f"Suggested: {item['best']}\n"
                    f"Evaluation: {eval_line(item)}\n"
                    f"Loss: {loss_text(item)}\n"
                )

        counts = count_categories(results)

        report.write(
            "\n[bold cyan]FINAL SUMMARY[/bold cyan]\n"
            f"[green]Good moves: {counts['GOOD']}[/green]\n"
            f"[dark_orange]Inaccuracies: {counts['INACCURACY']}[/dark_orange]\n"
            f"[yellow]Mistakes: {counts['MISTAKE']}[/yellow]\n"
            f"[red]Blunders: {counts['BLUNDER']}[/red]\n"
            "\n[bold cyan]GAME RATING[/bold cyan]\n"
            + RATING_NOTE
            + f"\n[bold]White[/bold]\n"
        )
        self.write_rating(report, "White")

        report.write("\n[bold]Black[/bold]\n")
        self.write_rating(report, "Black")

        report.write("\n[bold]Analysis completed.[/bold]")

        self.clear_move_detail()
        self.set_status("ANALYSIS COMPLETE — Select a move to inspect it.")

    def on_data_table_row_highlighted(self, event):
        if event.row_key is None:
            return

        try:
            item = self.move_data[int(event.row_key.value)]
        except (IndexError, ValueError, TypeError):
            return

        self.show_move_detail(item)

    def show_move_detail(self, item):
        self.query_one("#move-detail", Static).update(
            f"[bold cyan]MOVE {item['number']} — {item['color']}[/bold cyan]\n"
            f"Played: {item['played']}\n"
            f"Suggested: {item['best']}\n"
            f"Classification: {item['category']}\n"
            f"Evaluation: {eval_line(item)}\n"
            f"Loss: {loss_text(item)}\n"
            f"Position after move: {item['fen']}"
        )

    def clear_move_detail(self):
        self.query_one("#move-detail", Static).update(
            "Click or arrow to a move to see its details."
        )

    def show_player_report(self, side):
        report = self.query_one("#report", RichLog)
        report.clear()

        player = self.white_player if side == "White" else self.black_player
        title = self.white_title if side == "White" else self.black_title

        heading = Text("ANALYSIS REPORT - " + side.upper() + " - ")
        heading.append_text(title_text(title))
        heading.append(player)
        self.query_one("#report-heading", Static).update(heading)

        header = title_text(title)
        header.append(f"{player.upper()} - PERFORMANCE REPORT", style="bold cyan")
        report.write(header)
        report.write("\n")

        side_items = [item for item in self.move_data if item["color"] == side]

        for item in side_items:
            report.write(
                f"\n[{CATEGORY_COLORS[item['category']]}][bold]{item['category']}[/bold][/]\n"
                f"Move: {item['number']}\n"
                f"Played: {item['played']}\n"
                f"Suggested: {item['best']}\n"
                f"Evaluation: {eval_line(item)}\n"
                f"Loss: {loss_text(item)}\n"
            )

        counts = count_categories(side_items)

        report.write("\n[bold cyan]PLAYER SUMMARY[/bold cyan]\n")

        for category in CATEGORIES:
            report.write(f"{category}: {counts[category]}\n")

        report.write("\n[bold cyan]GAME RATING[/bold cyan]\n" + RATING_NOTE)

        self.write_rating(report, side)

    def action_show_white_report(self):
        if self.move_data:
            self.show_player_report("White")

    def action_show_black_report(self):
        if self.move_data:
            self.show_player_report("Black")

    def action_show_combined_report(self):
        if self.move_data:
            self.show_results(
                self.game_headers,
                self.game_data,
                self.move_data,
                self.final_board
            )

    def action_show_key_moves(self):
        if not self.move_data:
            return

        self.reset_heading()

        report = self.query_one("#report", RichLog)
        report.clear()

        report.write("[bold cyan]KEY MOVES - CRITICAL MOMENTS[/bold cyan]\n")

        important = [
            item for item in self.move_data
            if item["category"] in ("BLUNDER", "MISTAKE", "INACCURACY")
        ]

        # Mates carry a bounded loss, so they sort alongside other big mistakes
        important.sort(key=lambda item: item["loss"], reverse=True)

        if not important:
            report.write("\nNo significant errors found.")
            return

        for item in important:
            player = self.white_player if item["color"] == "White" else self.black_player

            report.write(
                f"\n[{CATEGORY_COLORS[item['category']]}][bold]{item['category']}[/bold][/]\n"
                f"Player: {escape(player)} ({item['color']})\n"
                f"Move: {item['number']}\n"
                f"Played: {item['played']}\n"
                f"Best: {item['best']}\n"
                f"Evaluation: {eval_line(item)}\n"
                f"Loss: {loss_text(item)}\n"
            )

    def on_button_pressed(self, event):
        if event.button.id == "analyze":
            self.action_analyze()

    def action_analyze(self):
        username = self.query_one("#username", Input).value.strip()
        game_url = self.query_one("#gameurl", Input).value.strip()
        pgn_path = self.query_one("#pgnfile", Input).value.strip()

        if not pgn_path and not username:
            self.set_status("ENTER A CHESS.COM USERNAME, OR A PGN FILE PATH.")
            return

        try:
            depth = parse_setting(self.query_one("#depth", Input).value, "Depth", DEPTH_RANGE)
            threads = parse_setting(self.query_one("#threads", Input).value, "Threads", THREAD_RANGE)
            hash_mb = parse_setting(self.query_one("#hash", Input).value, "Hash", HASH_RANGE)

        except ValueError as error:
            self.set_status(f"SETTINGS ERROR: {error}")

            self.query_one("#report", RichLog).write(
                f"[red]Settings error:[/red] {error}\n\n"
                "Fix the value in the box above and press ANALYZE again."
            )
            return

        self.query_one("#moves", DataTable).clear()
        self.query_one("#report", RichLog).clear()
        self.clear_move_detail()

        self.set_status("INITIALIZING ANALYSIS...")

        self.load_game(username, game_url, depth, threads, hash_mb, pgn_path)

    def action_reset(self):
        self.query_one("#username", Input).value = ""
        self.query_one("#gameurl", Input).value = ""
        self.query_one("#pgnfile", Input).value = ""
        self.query_one("#depth", Input).value = str(ANALYSIS_DEPTH)
        self.query_one("#threads", Input).value = str(min(ENGINE_THREADS, THREAD_RANGE[1]))
        self.query_one("#hash", Input).value = str(ENGINE_HASH)

        self.query_one("#moves", DataTable).clear()
        self.query_one("#report", RichLog).clear()

        self.move_data = []
        self.game_data = None

        self.clear_move_detail()
        self.set_status("READY — ENTER A CHESS.COM USERNAME.")

        self.query_one("#report", RichLog).write(
            "[bold cyan]TARS CHESS ANALYZER[/bold cyan]\n\nWaiting for a game."
        )


if __name__ == "__main__":
    ChessAnalyzer().run()
