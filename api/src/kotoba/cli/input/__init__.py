"""Everything the person at the keyboard touches: the line editor, its history, and the slash table.

`prompt` owns the keys and the completion, `commands` owns what a `/word` means. Neither imports the
engine — a command returns a decision for the caller to carry out.
"""
