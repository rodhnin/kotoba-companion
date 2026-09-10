"""SentenceSplitter — streaming sentence boundaries for the expressive TTS engine."""
from __future__ import annotations

from kotoba.core.voice.sentences import SentenceSplitter


def split_all(*chunks: str) -> tuple[list[str], str]:
    s = SentenceSplitter()
    out: list[str] = []
    for c in chunks:
        out.extend(s.feed(c))
    return out, s.flush()


def test_two_sentences_and_trailing_fragment():
    out, rest = split_all("Hola amiga. ¿Cómo estás hoy? Yo bien")
    assert out == ["Hola amiga.", "¿Cómo estás hoy?"]
    assert rest == "Yo bien"


def test_no_split_on_abbreviations_or_initials():
    out, rest = split_all("El Dr. García y la Sra. Pérez llegaron. Ya")
    assert out == ["El Dr. García y la Sra. Pérez llegaron."]
    out, rest = split_all("J. K. Rowling escribió mucho. Fin")
    assert out == ["J. K. Rowling escribió mucho."]
    assert rest == "Fin"


def test_no_split_inside_decimals():
    out, rest = split_all("El precio es 3.14 euros exactos. Ok")
    assert out == ["El precio es 3.14 euros exactos."]


def test_ellipsis_is_never_split_inside():
    out, rest = split_all("Bueno..", ". no sé qué decir. Pero")
    assert out == ["Bueno... no sé qué decir."]
    assert rest == "Pero"


def test_ellipsis_before_uppercase_is_a_boundary():
    out, _ = split_all("Espera... Ya llegó tu paquete. X")
    assert out == ["Espera...", "Ya llegó tu paquete."]
    out, _ = split_all("Vale… Entiendo perfectamente. K")
    assert out == ["Vale…", "Entiendo perfectamente."]


def test_multi_punctuation_run_kept_whole():
    out, _ = split_all("¿¡Qué dices?! Sí claro. B")
    assert out == ["¿¡Qué dices?!", "Sí claro."]


def test_closing_quote_stays_with_its_sentence():
    out, _ = split_all('Dijo "¡Vamos!" Y salió corriendo. Q')
    assert out == ['Dijo "¡Vamos!"', "Y salió corriendo."]


def test_audio_tag_starts_the_next_sentence():
    out, _ = split_all("¡Genial! [happily] Vamos allá entonces. Z")
    assert out == ["¡Genial!", "[happily] Vamos allá entonces."]


def test_never_splits_inside_a_bracket():
    out, _ = split_all("[laughs softly] Claro que sí. X")
    assert out == ["[laughs softly] Claro que sí."]
    out, _ = split_all("[v3.2] Ok entonces vamos. X")
    assert out == ["[v3.2] Ok entonces vamos."]


def test_tag_split_across_chunks_is_held():
    s = SentenceSplitter()
    assert s.feed("¡Sí! [ner") == ["¡Sí!"]
    assert s.feed("vous] ¿Seguro que sí? Ya") == ["[nervous] ¿Seguro que sí?"]
    assert s.flush() == "Ya"


def test_runaway_bracket_becomes_literal_text():
    out, rest = split_all("[esto no es un tag y sigue mucho mas de cuarenta y cinco caracteres. Ok")
    assert out == ["[esto no es un tag y sigue mucho mas de cuarenta y cinco caracteres."]
    assert rest == "Ok"


def test_streaming_char_boundaries():
    s = SentenceSplitter()
    assert s.feed("Hol") == []
    assert s.feed("a. ¿Q") == ["Hola."]
    assert s.feed("ué tal?") == []
    assert s.flush() == "¿Qué tal?"


def test_pending_sentence_only_when_it_looks_finished():
    s = SentenceSplitter()
    s.feed("Ya te lo busco.")
    assert s.pending_sentence() == "Ya te lo busco."
    assert s.flush() == ""
    s.feed("Quiero decir que")
    assert s.pending_sentence() == ""
    assert s.flush() == "Quiero decir que"


# ---- script-native full stops + the forced boundary (speech-out audit) ---------------------------


def drive(text: str, chunk: int) -> list[str]:
    """Feed in fixed-size chunks and flush — sentences plus the trailing remainder, in order."""
    s = SentenceSplitter()
    out: list[str] = []
    for i in range(0, len(text), chunk):
        out.extend(s.feed(text[i : i + chunk]))
    rest = s.flush()
    if rest:
        out.append(rest)
    return out


def test_japanese_splits_per_sentence_without_spaces():
    out = drive("こんにちは。今日は元気ですか。散歩に行きましょう。", 4)
    assert out == ["こんにちは。", "今日は元気ですか。", "散歩に行きましょう。"]


def test_chinese_fullwidth_marks_split():
    out = drive("你好！今天天气很好。我们去公园吧？好的。", 3)
    assert out == ["你好！", "今天天气很好。", "我们去公园吧？", "好的。"]


def test_arabic_question_mark_splits():
    out = drive("مرحبا. كيف حالك؟ أنا بخير.", 4)
    assert out == ["مرحبا.", "كيف حالك؟", "أنا بخير."]


def test_mixed_scripts_split_on_both_conventions():
    out = drive("Claro que sí. 日本語も話せるよ。すごいでしょ。", 5)
    assert out == ["Claro que sí.", "日本語も話せるよ。", "すごいでしょ。"]


def test_fullwidth_decimal_is_not_a_boundary():
    out = drive("円周率は３．１４です。そうだよ。", 4)
    assert out == ["円周率は３．１４です。", "そうだよ。"]


def test_pending_sentence_recognizes_cjk_full_stop():
    s = SentenceSplitter()
    s.feed("承知しました。")
    assert s.pending_sentence() == "承知しました。"


def test_terminatorless_stream_is_force_cut_below_the_cap():
    from kotoba.core.voice.sentences import _MAX_HELD

    s = SentenceSplitter()
    text = "palabra " * 300  # 2400 chars, no terminator
    pieces = []
    for i in range(0, len(text), 50):
        pieces.extend(s.feed(text[i : i + 50]))
    rest = s.flush()
    assert pieces, "a terminator-less stream must still start speaking"
    assert all(len(p) <= _MAX_HELD for p in pieces)
    assert len(rest) <= _MAX_HELD
    rebuilt = " ".join(pieces + ([rest] if rest else []))
    assert rebuilt.split() == text.split(), "the forced cut must lose no words"


def test_forced_cut_lands_at_a_pause_and_never_inside_a_tag():
    from kotoba.core.voice.sentences import _MAX_HELD

    s = SentenceSplitter()
    text = ("z" * (_MAX_HELD - 10)) + " [laughs softly] " + ("y" * 100)
    pieces = s.feed(text)
    assert pieces, "over the cap must force a piece out"
    joined = "".join(pieces) + s.flush()
    assert "[laughs softly]" in joined, "a tag straddling the cut must survive whole"
    for p in pieces:
        assert p.count("[") == p.count("]"), f"piece cut inside a tag: {p!r}"


def test_forced_cut_prefers_a_pause_over_splitting_a_decimal_comma():
    from kotoba.core.voice.sentences import _MAX_HELD

    s = SentenceSplitter()
    filler = "palabra " * ((_MAX_HELD - 10) // 8)
    pieces = s.feed(filler + "vale 3,14159 euros y la frase sigue sin terminar jamás de verdad")
    joined = " ".join(pieces) + " " + s.flush()
    assert pieces, "over the cap must force a piece out"
    assert "3,14159" in joined, f"the decimal comma must not be a cut point: {pieces!r}"
