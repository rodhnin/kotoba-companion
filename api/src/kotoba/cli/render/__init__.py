"""What the terminal draws: capabilities, palette, her face, her prose, her rows.

Nothing here knows the engine exists. `caps` measures the terminal, `theme` turns that into a rich
Console, `kaomoji` and `portrait` are her two faces, `markdown` cuts her stream into blocks that can be
printed once and never revised, and `screen` is the surface that owns the gap discipline. One module
outside this package joins them to a Session.
"""
