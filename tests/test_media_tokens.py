from services.news import (
    avatar_token,
    avatar_token_valid,
    photo_token,
    photo_token_valid,
)


def test_generated_media_tokens_are_valid():
    assert photo_token_valid(17, photo_token(17))
    assert avatar_token_valid(23, avatar_token(23))


def test_oversized_media_token_is_rejected_without_integer_conversion():
    malicious = f"{'9' * 5000}.{'a' * 32}"

    assert not photo_token_valid(17, malicious)
    assert not avatar_token_valid(23, malicious)


def test_media_token_rejects_noncanonical_signature():
    expires = photo_token(17).split(".", 1)[0]

    assert not photo_token_valid(17, f"{expires}.{'A' * 32}")
    assert not avatar_token_valid(23, f"{expires}.{'g' * 32}")
