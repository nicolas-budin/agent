from index_docs import chunk_text


def test_chunk_text_empty_string_returns_no_chunks():
    assert chunk_text("") == []


def test_chunk_text_whitespace_only_returns_no_chunks():
    assert chunk_text("   \n\n   ") == []


def test_chunk_text_single_short_paragraph_returns_one_chunk():
    assert chunk_text("Bonjour le monde.") == ["Bonjour le monde."]


def test_chunk_text_merges_short_paragraphs_into_one_chunk():
    text = "Premier paragraphe.\n\nDeuxième paragraphe."
    assert chunk_text(text, max_chars=100) == [text]


def test_chunk_text_splits_when_exceeding_max_chars():
    para_a = "A" * 50
    para_b = "B" * 50
    chunks = chunk_text(f"{para_a}\n\n{para_b}", max_chars=60)
    assert chunks == [para_a, para_b]


def test_chunk_text_strips_paragraph_whitespace():
    chunks = chunk_text("  Texte avec espaces.  \n\n  Autre paragraphe.  ")
    assert chunks == ["Texte avec espaces.\n\nAutre paragraphe."]
