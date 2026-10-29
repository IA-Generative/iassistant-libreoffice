"""Faux objets des tests de `menu_actions/calc.py`, qui partent avec lui."""

from unittest.mock import MagicMock


def make_cell(text=""):
    """Stateful fake cell: getString/setString mirror a real cell."""
    cell = MagicMock()
    cell._text = text
    cell.getString.side_effect = lambda: cell._text
    cell.setString.side_effect = lambda v: setattr(cell, "_text", v)
    return cell


def make_sheet(cells: dict):
    """cells: {(col, row): text}  →  getCellByPosition returns a fake cell."""
    cell_map = {pos: make_cell(t) for pos, t in cells.items()}
    sheet = MagicMock()
    sheet.getCellByPosition.side_effect = lambda c, r: cell_map.setdefault(
        (c, r), make_cell()
    )
    return sheet, cell_map


def make_streaming_job(stream_chunks=None):
    """Fake job whose stream_request calls the callback with each chunk."""
    job = MagicMock()
    chunks = stream_chunks or ["hello"]

    def _stream(request, api_type, callback):
        for chunk in chunks:
            callback(chunk)

    job.stream_request.side_effect = _stream
    def _get_config(key, default=""):
        config_defaults = {
            "api_type": "chat",
            "analyze_range_max_tokens": 4000,
        }
        return config_defaults.get(key, default)
    job.get_config.side_effect = _get_config
    job._formula_dialog = None
    job._formula_dialog_state = None
    return job
